import base64
import hashlib
import secrets
import re
import json
from datetime import timedelta
from lxml import etree
from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError
from ..services import builders, signature, transport, xml_utils
from .config import DOC_TYPES

PROTECTED={'state','access_key','numeric_code','sequence','environment','unsigned_xml','signed_xml','authorized_xml',
           'authorization_number','authorization_date','payload_hash','attempts','next_attempt','last_message',
           'was_sent','company_snapshot','source_snapshot','retention_move_id','fiscal_number'}
EDITABLE_META={'message_follower_ids','message_partner_ids','activity_ids'}

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
    date=fields.Date(required=True,default=fields.Date.context_today,string='Fecha emisión')
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
            company=rec.company_id._ec_sri_company_data()
            environment=rec.company_id.ec_sri_environment
            if rec.environment and rec.environment!=environment:
                raise UserError(_('Restaure el ambiente original del comprobante antes de corregirlo.'))
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
                    point=rec.point_id
                    if not point or point.company_id!=rec.company_id or point.document_type!=rec.document_type or point.environment!=environment:
                        raise UserError(_('Seleccione el punto correcto para empresa, documento y ambiente.'))
                    if rec.move_id and rec.move_id.partner_id.commercial_partner_id!=rec.partner_id.commercial_partner_id:
                        raise UserError(_('El contacto debe coincidir con el documento contable.'))
                    if rec.picking_id and rec.picking_id.partner_id.commercial_partner_id!=rec.partner_id.commercial_partner_id:
                        raise UserError(_('El destinatario debe coincidir con la transferencia.'))
                    key=rec.access_key
                    if not key:
                        numeric='%08d' % secrets.randbelow(100000000)
                        if rec.document_type in ('01','03','04','05'):
                            seq=rec.move_id._ec_sri_number_parts()
                            if seq[:2]!=(point.establishment,point.emission): raise UserError(_('El punto no coincide con la numeración contable.'))
                            sequence=seq[2]
                        else: sequence=point.sequence_id.next_by_id()
                        key=xml_utils.access_key(rec.date,rec.document_type,company['vat'],environment,point.establishment,point.emission,sequence,numeric)
                    raw,source=rec._native_data(key,company)
                cert,password=rec.company_id._ec_sri_certificate()
                signed=signature.sign_xml(raw,cert,password)
                xml_utils.validate_document(signed,company['vat'],environment,rec.document_type)
            except (ValueError,etree.LxmlError) as exc:
                raise UserError(_('No se pudo preparar el comprobante: %s',str(exc))) from exc
            rec._set(dict(access_key=key,sequence=key[30:39],numeric_code=key[39:47],environment=environment,
                          unsigned_xml=base64.b64encode(raw),signed_xml=base64.b64encode(signed),
                          payload_hash=hashlib.sha256(signed).hexdigest(),company_snapshot=company,source_snapshot=json.loads(json.dumps(source,default=str)),
                          state='queued',attempts=0,next_attempt=fields.Datetime.now(),last_message=False))
            rec._event('prepared','XML validado y firmado. En cola de envío.')
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
        if not self.env.user.has_group('account.group_account_manager'): raise AccessError(_('Requiere administrador contable.'))
        for doc in self:
            if not doc._lock(): continue
            if doc.state!='authorized' or not all([doc.cancellation_reason,doc.cancellation_evidence,doc.cancellation_date]):
                raise UserError(_('Registre motivo, fecha y constancia de anulación realizada en el portal SRI.'))
            doc._set({'state':'cancelled'});doc._event('cancellation',doc.cancellation_reason)

    def action_ride(self):
        if any(d.state not in ('authorized','cancelled') for d in self):
            raise UserError(_('El RIDE se genera únicamente con autorización SRI.'))
        return self.env.ref('l10n_ec_sri_community.action_report_ride').report_action(self)

    def action_post_retention(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(_('La contabilización de retenciones requiere administrador contable.'))
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
        if set(vals)&{'account_id','base_box','tax_box'} and not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(_('Requiere administrador contable para cambiar cuentas o casilleros.'))
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
