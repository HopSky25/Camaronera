import base64
import hashlib
import secrets
import re
import json
import logging
from datetime import timedelta
from lxml import etree
from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError
from ..services import builders, signature, transport, validators, xml_utils
from .config import DOC_TYPES

PROTECTED={'state','access_key','numeric_code','sequence','environment','unsigned_xml','signed_xml','authorized_xml',
           'authorization_number','authorization_date','payload_hash','attempts','next_attempt','last_message',
           'was_sent','company_snapshot','source_snapshot','retention_move_id','fiscal_number','customer_mail_sent'}
EDITABLE_META={'message_follower_ids','message_partner_ids','activity_ids'}
# Operaciones del historial que NO implican haber transmitido nada al SRI. Cualquier
# otra (recepción, autorización, corrección, error de conexión o interno después de
# marcar el envío...) cuenta como «pudo llegar al SRI»: ver _ec_sri_ever_transmitted.
LOCAL_EVENTS={'prepared','warning','key_reserved','prepare_error','redate','mail','mail_error'}
# Comprobantes que se envían por correo al cliente al quedar autorizados.
MAIL_DOCUMENT_TYPES=('01','04')

_logger=logging.getLogger(__name__)

class SriDocument(models.Model):
    _name='ec.sri.document'
    _description='Comprobante electrónico SRI'
    _inherit=['mail.thread','mail.activity.mixin']
    _order='id desc'
    _check_company_auto=True
    name=fields.Char(compute='_compute_name',store=True)
    company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company,index=True)
    partner_id=fields.Many2one('res.partner',required=True,check_company=True)
    document_type=fields.Selection(DOC_TYPES,required=True,string='Tipo de documento',default='01')
    point_id=fields.Many2one('ec.sri.point',check_company=True,string='Punto de emisión')
    date=fields.Date(required=True,default=lambda s:validators.today_ec(),string='Fecha emisión')
    move_id=fields.Many2one('account.move',check_company=True,copy=False,ondelete='restrict',string='Documento contable')
    picking_id=fields.Many2one('stock.picking',check_company=True,copy=False,ondelete='restrict',string='Transferencia')
    mode=fields.Selection([('native','Generar desde Odoo'),('xml','XML externo sin firma')],default='native',required=True)
    import_xml=fields.Binary('XML externo',attachment=False,copy=False)
    import_filename=fields.Char()
    reason=fields.Char('Motivo')
    period=fields.Char('Periodo fiscal MM/AAAA')
    retention_ids=fields.One2many('ec.sri.retention.line','document_id',string='Retenciones')
    environment=fields.Selection([('1','Pruebas'),('2','Producción')],readonly=True,copy=False)
    sequence=fields.Char(readonly=True,copy=False)
    numeric_code=fields.Char(readonly=True,copy=False)
    access_key=fields.Char('Clave de acceso',readonly=True,copy=False,index=True)
    fiscal_number=fields.Char(compute='_compute_name',store=True,copy=False)
    state=fields.Selection([('draft','Borrador'),('queued','En cola'),('waiting','Esperando autorización'),
        ('authorized','Autorizado'),('rejected','Rechazado'),('error','Requiere revisión'),('cancelled','Anulado en SRI')],
        default='draft',required=True,readonly=True,copy=False,tracking=True,index=True)
    unsigned_xml=fields.Binary('XML generado',readonly=True,attachment=False,copy=False)
    signed_xml=fields.Binary('XML firmado',readonly=True,attachment=False,copy=False)
    authorized_xml=fields.Binary('XML autorizado',readonly=True,attachment=False,copy=False)
    xml_filename=fields.Char(compute='_compute_filenames')
    authorized_filename=fields.Char(compute='_compute_filenames')
    authorization_number=fields.Char('Número autorización',readonly=True,copy=False)
    authorization_date=fields.Char('Fecha autorización SRI',readonly=True,copy=False)
    payload_hash=fields.Char('SHA256 XML firmado',readonly=True,copy=False)
    was_sent=fields.Boolean(readonly=True,copy=False)
    attempts=fields.Integer(readonly=True,copy=False)
    next_attempt=fields.Datetime(readonly=True,copy=False,index=True)
    last_message=fields.Text('Última respuesta',readonly=True,copy=False)
    company_snapshot=fields.Json(readonly=True,copy=False)
    source_snapshot=fields.Json(readonly=True,copy=False)
    event_ids=fields.One2many('ec.sri.event','document_id',readonly=True)
    cancellation_reason=fields.Text('Motivo de anulación')
    cancellation_evidence=fields.Binary('Constancia SRI de anulación',attachment=False,copy=False)
    cancellation_date=fields.Date('Fecha anulación SRI',copy=False)
    retention_journal_id=fields.Many2one('account.journal',string='Diario de retenciones',check_company=True,domain="[('type','=','general'),('company_id','=',company_id)]")
    retention_move_id=fields.Many2one('account.move',string='Asiento de retención',check_company=True,readonly=True,copy=False,ondelete='restrict')
    # Correo al cliente con el RIDE y el XML autorizado (una sola vez; ver _ec_sri_notify_customer).
    customer_mail_sent=fields.Boolean('Enviado al cliente',readonly=True,copy=False)
    # Producción: borrador que nunca obtuvo clave ni se transmitió y cuya fecha ya no
    # es la de hoy. No se puede emitir tal cual: ver action_redate_today.
    date_expired=fields.Boolean('Fecha vencida',compute='_compute_date_expired')
    _key_unique=models.Constraint('UNIQUE(company_id, access_key)','La clave de acceso ya está registrada.')
    _move_unique=models.Constraint('UNIQUE(move_id, document_type)','Ya existe este comprobante para el documento contable.')
    _picking_unique=models.Constraint('UNIQUE(picking_id, document_type)','Ya existe una guía para esta transferencia.')
    _number_unique=models.Constraint('UNIQUE(company_id, environment, document_type, fiscal_number)','Ese número de comprobante ya fue utilizado en este ambiente.')

    @api.depends('document_type','sequence','point_id','access_key')
    def _compute_name(self):
        for rec in self:
            key=rec.access_key
            rec.fiscal_number=key[24:39] if key else False
            rec.name='%s %s-%s-%s' % (dict(DOC_TYPES).get(rec.document_type,''),key[24:27],key[27:30],key[30:39]) if key else _('Nuevo comprobante')

    @api.depends('access_key')
    def _compute_filenames(self):
        for rec in self:
            rec.xml_filename=(rec.access_key or 'comprobante')+'.xml'
            rec.authorized_filename=(rec.access_key or 'comprobante')+'-autorizado.xml'

    @api.depends('state','access_key','date','mode','company_id.ec_sri_environment')
    def _compute_date_expired(self):
        today=validators.today_ec()
        for rec in self:
            rec.date_expired=bool(rec.state=='draft' and rec.mode=='native' and not rec.access_key
                and rec.company_id.ec_sri_environment=='2' and rec.date and rec.date<today)

    @api.model_create_multi
    def create(self,vals_list):
        for vals in vals_list:
            if set(vals)&PROTECTED: raise AccessError(_('Los estados y archivos SRI solo se generan mediante sus acciones.'))
        return super().create(vals_list)

    def write(self,vals):
        if set(vals)&PROTECTED: raise AccessError(_('No puede modificar directamente los resultados SRI.'))
        allowed=EDITABLE_META|{'cancellation_reason','cancellation_evidence','cancellation_date','retention_journal_id'}
        for rec in self:
            if rec.retention_move_id and 'retention_journal_id' in vals:
                raise UserError(_('La retención ya está contabilizada.'))
            if rec.state!='draft' and set(vals)-allowed:
                raise UserError(_('El comprobante está bloqueado. Use Corregir después de un rechazo confirmado.'))
            if rec.access_key and set(vals)&{'company_id','document_type','date','point_id','move_id','picking_id','partner_id','mode'}:
                raise UserError(_('No se puede cambiar la identidad de un comprobante con clave asignada.'))
        return super().write(vals)

    def _set(self,vals):
        return super(SriDocument,self).write(vals)

    def unlink(self):
        if any(d.access_key or d.state!='draft' for d in self):
            raise UserError(_('No elimine comprobantes numerados; conserve la trazabilidad.'))
        return super().unlink()

    def _lock(self):
        self.ensure_one();self.check_access('write')
        self.flush_recordset()
        self.env.cr.execute('SELECT id FROM ec_sri_document WHERE id=%s FOR UPDATE SKIP LOCKED',(self.id,))
        found=self.env.cr.fetchone()
        self.invalidate_recordset()
        return bool(found)

    def _event(self,operation,description,raw=None):
        self.env['ec.sri.event']._append(dict(document_id=self.id,company_id=self.company_id.id,
            operation=operation,description=description,raw_response=base64.b64encode(raw) if raw else False))

    def _native_data(self,key,company):
        if self.document_type in ('01','03','04','05'):
            if not self.move_id: raise UserError(_('Seleccione un documento contable publicado.'))
            data=self.move_id._ec_sri_invoice_data(self)
            return builders.invoice(self.document_type,company,key,data),data
        if self.document_type=='07':
            if not self.move_id or self.move_id.move_type!='in_invoice' or self.move_id.state!='posted':
                raise UserError(_('Seleccione una factura de proveedor publicada.'))
            if not self.retention_ids: raise UserError(_('Ingrese los códigos, bases y porcentajes retenidos.'))
            bill_date=self.move_id.invoice_date
            if bill_date and self.date<bill_date:
                raise UserError(_('La retención no puede tener fecha anterior a la factura que sustenta (%s).',bill_date))
            if bill_date and self.date>validators.add_business_days(bill_date,validators.RETENTION_BUSINESS_DAYS):
                self._event('warning',_('La retención se emite después de %s días hábiles desde la factura del proveedor '
                    '(Reglamento, art. 8).',validators.RETENTION_BUSINESS_DAYS))
            support=self.move_id._ec_sri_support_data()
            support['withholdings']=[dict(code=l.tax_type,retention_code=l.retention_code,base=l.base,rate=l.rate) for l in self.retention_ids]
            data=dict(date=self.date.strftime('%d/%m/%Y'),address=self.point_id.address,period=self.period or self.date.strftime('%m/%Y'),
                      partner=self.partner_id._ec_sri_partner_data(),supports=[support])
            return builders.withholding(company,key,data),data
        if not self.picking_id: raise UserError(_('Seleccione una transferencia para la guía.'))
        data=self.picking_id._ec_sri_delivery_data(self)
        return builders.delivery(company,key,data),data

    def action_queue(self):
        for rec in self.with_context(bin_size=False):
            if not rec._lock(): continue
            if rec.state!='draft': raise UserError(_('Solo se pueden preparar borradores.'))
            company,environment=rec._ec_sri_precheck()
            source={}
            try:
                if rec.mode=='xml':
                    raw=base64.b64decode(rec.import_xml or b'',validate=True)
                    root=xml_utils.validate_document(raw,company['vat'],environment,rec.document_type)
                    if root.findall('{%s}Signature' % xml_utils.DS): raise ValueError('Importe XML sin firma.')
                    key=root.findtext('infoTributaria/claveAcceso')
                    if key[:8]!=rec.date.strftime('%d%m%Y'): raise ValueError('La fecha del registro difiere del XML.')
                    if rec.access_key and rec.access_key!=key: raise ValueError('La corrección debe conservar la clave de acceso.')
                    p=rec.partner_id._ec_sri_partner_data()
                    ids=root.xpath('./*/identificacionComprador/text() | ./*/identificacionProveedor/text() | ./*/identificacionSujetoRetenido/text() | ./destinatarios/destinatario/identificacionDestinatario/text()')
                    if p['vat'] not in ids: raise ValueError('El receptor del XML no coincide con el contacto.')
                else:
                    key=rec._ec_sri_reserve_key(company,environment)
                    raw,source=rec._native_data(key,company)
                cert,password=rec.company_id._ec_sri_certificate()
                signed=signature.sign_xml(raw,cert,password)
                if len(signed)>validators.MAX_XML_BYTES:
                    raise ValueError('El comprobante firmado pesa %s KB; el SRI admite hasta 320 KB por comprobante.' % (len(signed)//1024))
                xml_utils.validate_document(signed,company['vat'],environment,rec.document_type)
            except (ValueError,etree.LxmlError) as exc:
                raise UserError(_('No se pudo preparar el comprobante: %s',str(exc))) from exc
            except UserError:
                raise
            except Exception as exc:  # noqa: BLE001 - un fallo inesperado no debe salir como error 500
                _logger.exception('SRI: fallo inesperado al preparar el comprobante %s',rec.id)
                raise UserError(_('No se pudo preparar el comprobante por un error interno (%s). Revise el registro del servidor.',
                    type(exc).__name__)) from exc
            rec._set(dict(access_key=key,sequence=key[30:39],numeric_code=key[39:47],environment=environment,
                          unsigned_xml=base64.b64encode(raw),signed_xml=base64.b64encode(signed),
                          payload_hash=hashlib.sha256(signed).hexdigest(),company_snapshot=company,source_snapshot=json.loads(json.dumps(source,default=str)),
                          state='queued',attempts=0,next_attempt=fields.Datetime.now(),last_message=False))
            rec._event('prepared','XML validado y firmado. En cola de envío.')
            warning=rec.partner_id._ec_sri_identification_warning()
            if warning: rec._event('warning',warning)
        # Transmisión inmediata (Resolución NAC-DGERCGC25-00000017): en lugar
        # de esperar el ciclo de 1 minuto, se dispara el cron para que envíe al
        # SRI en segundos tras confirmar la transacción. Se mantiene todo el
        # blindaje de _cron_process (bloqueo por fila, consulta antes de
        # reenviar y backoff), y si el envío falla el documento queda en cola.
        if self.filtered(lambda r: r.state == 'queued'):
            self.env.ref('l10n_ec_sri_community.ir_cron_sri_queue').sudo()._trigger()
        return True

    def _ec_sri_precheck(self):
        """Comprobaciones previas a numerar y firmar: datos de la empresa, ambiente y
        fecha de emisión. Devuelve (datos de la empresa, ambiente)."""
        self.ensure_one()
        company=self.company_id._ec_sri_company_data()
        environment=self.company_id.ec_sri_environment
        if self.environment and self.environment!=environment:
            raise UserError(_('Restaure el ambiente original del comprobante antes de corregirlo.'))
        today=validators.today_ec()
        if self.date>today:
            raise UserError(_('La fecha de emisión %s es futura.',self.date))
        if environment=='2' and not self.access_key and self.date!=today:
            # Solo llega aquí un comprobante que nunca obtuvo clave de acceso (nunca se
            # transmitió): se puede volver a fechar con «Emitir con fecha de hoy».
            raise UserError(_('Fecha vencida: emitir de nuevo con fecha de hoy. Desde el 01/01/2026 el comprobante se '
                'transmite al SRI en el momento de generarse (Res. NAC-DGERCGC25-00000014) y la fecha de emisión debe '
                'ser la de hoy, %(today)s; este tiene %(date)s y nunca se envió al SRI. Use «Emitir con fecha de hoy» '
                'en el comprobante o en la factura: la vuelve a fechar a hoy y la encola.',today=today,date=self.date))
        return company,environment

    def _ec_sri_reserve_key(self,company,environment):
        """Valida el punto y asigna la clave de acceso (fecha de emisión, número y código
        numérico) ANTES de firmar, y la deja grabada en el comprobante.

        Antes la clave solo se grababa después de firmar: si la firma fallaba (certificado,
        datos del cliente...) se perdía con el rollback, el reintento creaba el comprobante
        otra vez con la fecha de la factura y, en producción, la regla «fecha de emisión =
        hoy» lo bloqueaba para siempre desde el día siguiente. Con la clave grabada, quien
        prepare y firme en savepoints separados (account.move._ec_sri_emit_keep) conserva
        el comprobante aunque la firma falle, y el reintento reutiliza la misma clave.
        La clave no se transmite hasta que el cron envía el comprobante (was_sent)."""
        self.ensure_one()
        point=self.point_id
        if not point or point.company_id!=self.company_id or self.document_type not in point.line_ids.mapped('document_type') or point.environment!=environment:
            raise UserError(_('Seleccione el punto correcto para empresa, documento y ambiente.'))
        if self.move_id.ec_sri_point_id and self.move_id.ec_sri_point_id!=point:
            raise UserError(_('El punto de emisión debe ser el de la factura: %s.',self.move_id.ec_sri_point_id.display_name))
        if self.move_id and self.move_id.partner_id.commercial_partner_id!=self.partner_id.commercial_partner_id:
            raise UserError(_('El contacto debe coincidir con el documento contable.'))
        if self.picking_id and self.picking_id.partner_id.commercial_partner_id!=self.partner_id.commercial_partner_id:
            raise UserError(_('El destinatario debe coincidir con la transferencia.'))
        if self.access_key: return self.access_key
        numeric='%08d' % secrets.randbelow(100000000)
        if self.document_type in ('01','03','04','05'):
            seq=self.move_id._ec_sri_number_parts()
            if seq[:2]!=(point.establishment,point.emission): raise UserError(_('El punto no coincide con la numeración contable.'))
            sequence=seq[2]
        else: sequence=point._sequence_for(self.document_type).next_by_id()
        key=xml_utils.access_key(self.date,self.document_type,company['vat'],environment,point.establishment,point.emission,sequence,numeric)
        self._set(dict(access_key=key,sequence=key[30:39],numeric_code=key[39:47],environment=environment))
        self._event('key_reserved',_('Clave de acceso asignada (fecha de emisión %s). Aún no se ha enviado al SRI.',self.date))
        return key

    def _ec_sri_ever_transmitted(self):
        """¿Pudo llegar algo de este comprobante al SRI? Conservador: was_sent, o cualquier
        evento que no sea puramente local (action_correct vuelve was_sent a False, pero deja
        el evento «correction»)."""
        self.ensure_one()
        return bool(self.was_sent or self.sudo().event_ids.filtered(lambda e:e.operation not in LOCAL_EVENTS))

    def action_redate_today(self):
        """«Emitir con fecha de hoy» (producción). Un comprobante que nunca se transmitió y
        cuya fecha ya no es la de hoy no se puede emitir (Res. NAC-DGERCGC25-00000014). Para
        no dejarlo bloqueado para siempre, el contador lo vuelve a fechar: la factura pasa a
        borrador, toma la fecha de hoy (conserva su número y sus conciliaciones) y se vuelve
        a contabilizar; el comprobante toma la misma fecha y se encola.

        Solo si NUNCA se transmitió nada: sin clave de acceso y sin rastro de envío. Un
        comprobante que llegó al SRI conserva su fecha y su clave (se consulta o se corrige)."""
        today=validators.today_ec()
        for doc in self:
            if not doc._lock(): continue
            if doc.state!='draft' or doc.access_key or doc._ec_sri_ever_transmitted():
                raise UserError(_('Solo se vuelve a fechar un comprobante en borrador que nunca se envió al SRI '
                    '(sin clave de acceso). %s ya tiene clave o se transmitió: consúltelo o corríjalo.',doc.display_name))
            if doc.mode!='native':
                raise UserError(_('Un XML externo lleva su propia fecha: importe uno nuevo con la fecha de hoy.'))
            old=doc.date
            if doc.move_id:
                doc.move_id._ec_sri_redate(today)
            if old!=today:
                doc.write({'date':today})
                doc._event('redate',_('Fecha de emisión cambiada de %(old)s a %(new)s (nunca se había enviado al SRI).',
                    old=old,new=today))
        return self.action_queue()

    def _ec_sri_mail_recipients(self):
        """El cliente de la factura y, si lo tiene, su contacto de facturación (con correo)."""
        self.ensure_one()
        partner=self.move_id.partner_id or self.partner_id
        billing=partner.commercial_partner_id.child_ids.filtered(lambda p:p.type=='invoice' and p.active)
        return (partner|billing).filtered('email')

    def _ec_sri_notify_customer(self,force=False):
        """Envía al cliente el RIDE (PDF) y el XML autorizado de las facturas y notas de
        crédito autorizadas. Solo ENCOLA el correo (mail.mail, force_send=False): si el
        servidor de correo aún no está configurado, el correo espera en la cola.

        Idempotente (customer_mail_sent) y en un savepoint propio: un fallo al preparar el
        correo (plantilla, RIDE, adjunto) se registra en el historial y NUNCA lanza ni
        cambia el estado del comprobante. ``force`` reenvía aunque ya se hubiera enviado."""
        template=self.env.ref('l10n_ec_sri_community.mail_template_sri_authorized',raise_if_not_found=False)
        sent=self.browse()
        for doc in self.sudo().with_context(bin_size=False):
            if doc.state!='authorized' or doc.document_type not in MAIL_DOCUMENT_TYPES or not template:
                continue
            if doc.customer_mail_sent and not force:
                continue
            recipients=doc._ec_sri_mail_recipients()
            if not recipients:
                doc._event('mail',_('El cliente no tiene correo: no se le envió el comprobante.'))
                continue
            # El RIDE se genera aparte (no con report_template_ids de la plantilla): si falla
            # (p. ej. sin wkhtmltopdf en el servidor) el cliente recibe igual el XML autorizado,
            # que es el comprobante con validez tributaria.
            ride=self.env['ir.attachment']
            try:
                with self.env.cr.savepoint():
                    pdf,_fmt=self.env['ir.actions.report'].sudo()._render_qweb_pdf('l10n_ec_sri_community.action_report_ride',doc.ids)
                    ride=self.env['ir.attachment'].sudo().create(dict(name='RIDE-%s.pdf' % doc.access_key,raw=pdf,
                        res_model=doc._name,res_id=doc.id,mimetype='application/pdf'))
            except Exception as exc:  # noqa: BLE001
                _logger.warning('SRI: no se pudo generar el RIDE del comprobante %s: %s',doc.id,exc)
                doc._event('mail_error',_('No se pudo generar el RIDE (%s): el correo lleva solo el XML.',type(exc).__name__))
            try:
                with self.env.cr.savepoint():
                    attachment=self.env['ir.attachment'].sudo().create(dict(name=doc.authorized_filename,
                        raw=base64.b64decode(doc.authorized_xml or b''),res_model=doc._name,res_id=doc.id,
                        mimetype='application/xml'))
                    template.sudo().send_mail(doc.id,force_send=False,email_values={
                        'recipient_ids':[(6,0,recipients.ids)],'attachment_ids':[(4,a.id) for a in ride|attachment]})
                    doc._set({'customer_mail_sent':True})
                    doc._event('mail',_('Comprobante enviado a %s (correo en cola).',', '.join(recipients.mapped('email'))))
                sent|=doc
            except Exception as exc:  # noqa: BLE001 - el correo nunca afecta al comprobante
                _logger.warning('SRI: no se pudo preparar el correo del comprobante %s: %s',doc.id,exc)
                doc._event('mail_error',_('No se pudo preparar el correo al cliente (%s).',type(exc).__name__))
        return sent

    def action_send_customer_mail(self):
        """Botón «Enviar al cliente»: vuelve a encolar el correo con el RIDE y el XML."""
        for doc in self:
            if doc.state!='authorized' or doc.document_type not in MAIL_DOCUMENT_TYPES:
                raise UserError(_('Solo se envían al cliente facturas y notas de crédito autorizadas.'))
            if not doc._ec_sri_mail_recipients():
                raise UserError(_('El cliente %s no tiene correo electrónico.',(doc.move_id.partner_id or doc.partner_id).display_name))
            if not doc._ec_sri_notify_customer(force=True):
                raise UserError(_('No se pudo preparar el correo; vea el historial del comprobante.'))
        return True

    def _apply_authorization(self,result):
        self._event('authorization','\n'.join(result['messages']) or result['state'],result['raw'])
        if result['state']=='AUTORIZADO':
            raw=(result.get('document') or '').encode('utf-8')
            xml_utils.validate_document(raw,self.company_snapshot['vat'],self.environment,self.document_type)
            signature.verify_xml(raw)
            if result.get('number')!=self.access_key: raise ValueError('Número de autorización distinto a la clave.')
            # A previously-authorized, differently corrected payload must never be silently accepted.
            original=xml_utils.unsigned_copy(xml_utils.parse_xml(base64.b64decode(self.signed_xml)))
            returned=xml_utils.unsigned_copy(xml_utils.parse_xml(raw))
            if signature.canonical(original)!=signature.canonical(returned):
                raise ValueError('El SRI autorizó otra versión del XML. Revisión manual requerida.')
            self._set(dict(state='authorized',authorized_xml=base64.b64encode(result['authorization']),
                           authorization_number=result['number'],authorization_date=result.get('date'),
                           next_attempt=False,last_message='\n'.join(result['messages']) or 'AUTORIZADO'))
            if self.document_type=='03' and self.move_id:
                self.move_id.write({'ec_sri_supplier_authorization':result['number']})
            # Correo al cliente con el RIDE y el XML: en su propio savepoint, nunca lanza.
            self._ec_sri_notify_customer()
            return True
        if result['state']=='NO AUTORIZADO':
            self._set(dict(state='rejected',next_attempt=False,last_message='\n'.join(result['messages']) or 'NO AUTORIZADO'))
            return True
        if result['state'] not in ('PENDING','EN PROCESO','EN PROCESAMIENTO'):
            raise transport.SriTransportError('Estado SRI de autorización desconocido.')
        return False

    def _process(self):
        self.ensure_one()
        self._set({'attempts':self.attempts+1})
        try:
            # Always consult before resending after an ambiguous timeout/crash.
            client=transport.SriClient(self.environment)
            if self.was_sent or self.state=='waiting':
                previous=client.authorize(self.access_key)
                # A corrected queued payload may replace a previously rejected version.
                if previous['state']=='NO AUTORIZADO' and self.state=='queued':
                    self._event('previous_rejection','Se enviará la versión corregida.',previous['raw'])
                elif self._apply_authorization(previous): return
                if self.state=='waiting':
                    self._schedule();return
            self._set({'was_sent':True})
            result=client.receive(base64.b64decode(self.signed_xml))
            self._event('reception','\n'.join(result['messages']) or result['state'],result['raw'])
            if result['state']=='RECIBIDA' or set(result['codes'])&{'43','70'}:
                self._set({'state':'waiting','last_message':'\n'.join(result['messages']) or 'RECIBIDA'})
                if not self._apply_authorization(client.authorize(self.access_key)): self._schedule()
            elif result['state']=='DEVUELTA':
                self._set(dict(state='rejected',last_message='\n'.join(result['messages']),next_attempt=False))
            else: raise transport.SriTransportError('Estado SRI de recepción desconocido.')
        except transport.SriTransportError as exc:
            self._event('connection',str(exc));self._set({'last_message':str(exc)});self._schedule()
        except (ValueError,etree.LxmlError) as exc:
            self._event('validation',str(exc));self._set(dict(state='error',next_attempt=False,last_message=str(exc)))

    def _schedule(self):
        if self.attempts>=20:
            self._set(dict(state='error',next_attempt=False,last_message=(self.last_message or '')+'\nLímite de intentos; consulte o reanude manualmente.'))
        else:
            self._set({'next_attempt':fields.Datetime.now()+timedelta(minutes=min(60,2**min(self.attempts,6)))})

    @api.model
    def _cron_process(self):
        docs=self.search([('state','in',['queued','waiting']),('next_attempt','<=',fields.Datetime.now())],limit=5,order='next_attempt,id')
        for doc in docs:
            if not doc._lock() or doc.state not in ('queued','waiting'): continue
            # Commit the intent BEFORE network IO, so a killed worker will query before resending.
            doc._set({'was_sent':True})
            self.env['ir.cron']._commit_progress(0)
            if not doc._lock(): continue
            try:
                with self.env.cr.savepoint(): doc._process()
            except Exception as exc:
                # Keep other documents moving. Do not log secrets or full exception payloads.
                doc._set(dict(state='error',next_attempt=False,last_message='Error interno: '+type(exc).__name__))
                doc._event('internal','Error interno: '+type(exc).__name__)
            if not self.env['ir.cron']._commit_progress(1): break

    def action_resume(self):
        for doc in self:
            if doc._lock() and doc.state=='error' and doc.signed_xml:
                doc._set(dict(state='queued',attempts=0,next_attempt=fields.Datetime.now()))

    def action_consult(self):
        for doc in self:
            if not doc._lock(): continue
            if not doc.access_key or not doc.was_sent: raise UserError(_('El comprobante aún no se ha enviado.'))
            if doc.state in ('authorized','cancelled'): continue
            try:
                doc._apply_authorization(transport.SriClient(doc.environment).authorize(doc.access_key))
            except (transport.SriTransportError,ValueError,etree.LxmlError) as exc:
                doc._event('consult_error',str(exc));doc._set({'last_message':str(exc)})

    def action_correct(self):
        for doc in self:
            if not doc._lock(): continue
            if doc.state!='rejected': raise UserError(_('Solo se corrige un rechazo confirmado; consulte primero si hubo un timeout.'))
            # Preserve old signed XML and its hash in append-only event history.
            doc._event('correction','Corrección conservando clave y secuencial.',base64.b64decode(doc.signed_xml))
            doc._set(dict(state='draft',next_attempt=False,was_sent=False))

    def action_record_cancellation(self):
        if not self.env.user.has_group('l10n_ec_sri_community.group_sri_manager'): raise AccessError(_('Requiere el permiso Administrador de Ecuador SRI.'))
        for doc in self:
            if not doc._lock(): continue
            if doc.state!='authorized' or not all([doc.cancellation_reason,doc.cancellation_evidence,doc.cancellation_date]):
                raise UserError(_('Registre motivo, fecha y constancia de anulación realizada en el portal SRI.'))
            if doc.document_type=='01' and doc.partner_id._ec_sri_identification_code()=='07' and \
                    doc.date>=validators.FINAL_CONSUMER_RULES_FROM:
                raise UserError(_('Las facturas a consumidor final no se pueden anular una vez transmitidas al SRI '
                    '(Res. NAC-DGERCGC25-00000014, art. 3).'))
            if not doc.date<=doc.cancellation_date<=validators.today_ec():
                raise UserError(_('La fecha de anulación debe estar entre la emisión (%s) y hoy.',doc.date))
            deadline=validators.cancellation_deadline(doc.date)
            if doc.cancellation_date>deadline:
                doc._event('warning',_('La anulación es posterior al plazo en línea (%s, día 7 del mes siguiente). Pasado el '
                    'plazo, una factura solo se anula con nota de crédito.',deadline))
            doc._set({'state':'cancelled'});doc._event('cancellation',doc.cancellation_reason)

    def action_ride(self):
        if any(d.state not in ('authorized','cancelled') for d in self):
            raise UserError(_('El RIDE se genera únicamente con autorización SRI.'))
        return self.env.ref('l10n_ec_sri_community.action_report_ride').report_action(self)

    def action_post_retention(self):
        self.ensure_one()
        if not self.env.user.has_group('l10n_ec_sri_community.group_sri_manager'):
            raise AccessError(_('La contabilización de retenciones requiere el permiso Administrador de Ecuador SRI.'))
        if not self._lock(): return
        if self.retention_move_id:
            return {'type':'ir.actions.act_window','res_model':'account.move','view_mode':'form','res_id':self.retention_move_id.id}
        if self.state!='authorized' or self.document_type!='07' or self.mode!='native' or self.environment!='2':
            raise UserError(_('Solo se contabilizan retenciones nativas autorizadas en producción.'))
        bill=self.move_id
        if bill.state!='posted' or not self.retention_journal_id:
            raise UserError(_('Seleccione un diario general y una factura publicada.'))
        if bill.currency_id.name!='USD' or self.company_id.currency_id.name!='USD':
            raise UserError(_('La contabilización nativa de retenciones requiere USD.'))
        payable=bill.line_ids.filtered(lambda l:l.account_id.account_type=='liability_payable' and not l.reconciled)
        if not payable or len(payable.account_id)!=1: raise UserError(_('No hay una cuenta por pagar pendiente y única.'))
        total=sum(self.retention_ids.mapped('amount'))
        if total<=0 or total>abs(sum(payable.mapped('amount_residual')))+.01:
            raise UserError(_('La retención excede el saldo pendiente o no tiene valor.'))
        lines=[]
        for detail in self.retention_ids:
            if not detail.account_id or detail.account_id.account_type not in ('liability_current','liability_non_current'):
                raise UserError(_('Asigne una cuenta de pasivo tributario a cada retención.'))
            if detail.amount:
                lines.append((0,0,dict(name=self.name,account_id=detail.account_id.id,partner_id=self.partner_id.id,credit=detail.amount,debit=0)))
        lines.append((0,0,dict(name=self.name,account_id=payable.account_id.id,partner_id=self.partner_id.id,debit=total,credit=0)))
        entry=self.env['account.move'].with_company(self.company_id).create(dict(move_type='entry',company_id=self.company_id.id,
            journal_id=self.retention_journal_id.id,date=self.date,ref=self.name,line_ids=lines))
        entry.action_post()
        (payable|entry.line_ids.filtered(lambda l:l.account_id==payable.account_id)).reconcile()
        self._set({'retention_move_id':entry.id})
        self._event('accounting','Asiento contable de retención: '+entry.name)
        return {'type':'ir.actions.act_window','res_model':'account.move','view_mode':'form','res_id':entry.id}

    def _ride_data(self):
        self.ensure_one()
        if self.state not in ('authorized','cancelled'):
            raise UserError(_('El comprobante aún no está autorizado.'))
        self=self.with_context(bin_size=False)
        root=xml_utils.parse_xml(base64.b64decode(self.authorized_xml))
        root=xml_utils.parse_xml(root.findtext('comprobante'))
        def flatten(node,prefix=''):
            result=[]
            for child in node:
                if child.tag.startswith('{'): continue
                label=child.get('nombre') or re.sub(r'(?<!^)(?=[A-Z])',' ',child.tag).capitalize()
                if len(child): result.extend(flatten(child,prefix+label+' / '))
                else: result.append((prefix+label,child.text or ''))
            return result
        details=[]
        for section in root:
            if section.tag not in ('infoTributaria','{%s}Signature' % xml_utils.DS):
                titles={'infoFactura':'Datos de la factura','infoLiquidacionCompra':'Datos de la liquidación',
                    'infoNotaCredito':'Datos de la nota de crédito','infoNotaDebito':'Datos de la nota de débito',
                    'infoCompRetencion':'Datos de la retención','infoGuiaRemision':'Datos del transporte',
                    'detalles':'Detalle de productos y servicios','docsSustento':'Documentos de sustento y retenciones',
                    'destinatarios':'Destinatarios y bienes transportados','infoAdicional':'Información adicional'}
                details.append((titles.get(section.tag,section.tag.capitalize()),flatten(section)))
        return {'issuer':dict((n.tag,n.text) for n in root.find('infoTributaria')),'sections':details}

class RetentionLine(models.Model):
    _name='ec.sri.retention.line'
    _description='Detalle de retención SRI'
    _check_company_auto=True
    document_id=fields.Many2one('ec.sri.document',required=True,ondelete='cascade',check_company=True)
    company_id=fields.Many2one(related='document_id.company_id',store=True)
    tax_type=fields.Selection([('1','Renta'),('2','IVA'),('6','ISD')],required=True,default='1')
    withholding_tax_id=fields.Many2one('ec.sri.withholding.tax',string='Código SRI (catálogo)',help='Elige un código del catálogo para llenar tipo, código y porcentaje.')
    retention_code=fields.Char('Código retención SRI',required=True)
    base=fields.Float('Base',digits=(16,2),required=True)
    rate=fields.Float('Porcentaje',digits=(16,2),required=True)
    amount=fields.Float('Valor retenido',compute='_compute_amount',digits=(16,2))
    account_id=fields.Many2one('account.account',string='Cuenta de retención',check_company=True)
    base_box=fields.Char('Casillero base 103')
    tax_box=fields.Char('Casillero valor 103')
    @api.depends('base','rate')
    def _compute_amount(self):
        for rec in self: rec.amount=float(xml_utils.money(rec.base*rec.rate/100))
    @api.onchange('withholding_tax_id')
    def _onchange_withholding_tax(self):
        if self.withholding_tax_id:
            wt=self.withholding_tax_id
            self.tax_type={'renta':'1','iva':'2','isd':'6'}.get(wt.type,self.tax_type)
            self.retention_code=wt.code
            self.rate=wt.percentage
    @api.constrains('base','rate')
    def _check_amounts(self):
        for rec in self:
            if rec.base<0 or not 0<=rec.rate<=100: raise UserError(_('Base o porcentaje de retención inválido.'))
    @api.model_create_multi
    def create(self,vals_list):
        for vals in vals_list:
            doc=self.env['ec.sri.document'].browse(vals.get('document_id'));doc.check_access('write')
            if doc.state!='draft': raise UserError(_('El comprobante está bloqueado.'))
        return super().create(vals_list)
    def write(self,vals):
        if 'document_id' in vals: raise UserError(_('No puede trasladar una retención a otro comprobante.'))
        fiscal_fields=set(vals)-{'account_id','base_box','tax_box'}
        if fiscal_fields and any(r.document_id.state!='draft' for r in self): raise UserError(_('El comprobante está bloqueado.'))
        if set(vals)&{'account_id','base_box','tax_box'} and not self.env.user.has_group('l10n_ec_sri_community.group_sri_manager'):
            raise AccessError(_('Cambiar cuentas o casilleros requiere el permiso Administrador de Ecuador SRI.'))
        if 'account_id' in vals and any(r.document_id.retention_move_id for r in self):
            raise UserError(_('La retención ya está contabilizada.'))
        return super().write(vals)
    def unlink(self):
        if any(r.document_id.state!='draft' for r in self): raise UserError(_('El comprobante está bloqueado.'))
        return super().unlink()

class SriEvent(models.Model):
    _name='ec.sri.event'
    _description='Historial SRI'
    _order='id desc'
    document_id=fields.Many2one('ec.sri.document',required=True,ondelete='restrict')
    company_id=fields.Many2one('res.company',required=True,index=True)
    operation=fields.Char(required=True)
    description=fields.Text()
    raw_response=fields.Binary(attachment=False)
    @api.model
    def _append(self,vals):
        return super(SriEvent,self.sudo()).create(vals)
