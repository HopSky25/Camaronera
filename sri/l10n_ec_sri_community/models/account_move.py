import logging
import re
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ..services.xml_utils import money, decimal
from ..services.builders import aggregate_taxes
from ..services import validators

_logger=logging.getLogger(__name__)

_SRI_STATES=[('draft','Borrador'),('queued','En cola'),('waiting','Esperando autorización'),
    ('authorized','Autorizado'),('rejected','Rechazado'),('error','Requiere revisión'),('cancelled','Anulado en SRI')]

class AccountMove(models.Model):
    _inherit='account.move'
    ec_sri_document_ids=fields.One2many('ec.sri.document','move_id',string='Comprobantes SRI')
    # Espejo del comprobante SRI principal, para ver el estado fiscal desde la factura.
    ec_sri_state=fields.Selection(_SRI_STATES,string='Estado SRI',compute='_compute_ec_sri_status',store=False)
    ec_sri_access_key=fields.Char(string='Clave de acceso SRI',compute='_compute_ec_sri_status',store=False)
    ec_sri_authorization_number=fields.Char(string='N° autorización SRI',compute='_compute_ec_sri_status',store=False)
    ec_sri_document_count=fields.Integer(compute='_compute_ec_sri_status')
    # Producción: la factura nunca se transmitió y su fecha ya no es la de hoy (ver
    # ec.sri.document.action_redate_today). Muestra el aviso y «Emitir con fecha de hoy».
    ec_sri_date_expired=fields.Boolean('Fecha SRI vencida',compute='_compute_ec_sri_date_expired')

    def _compute_ec_sri_date_expired(self):
        today=validators.today_ec()
        for move in self:
            code=move.l10n_latam_document_type_id.code
            expired=False
            if move.state=='posted' and code in ('01','03','04','05') and move.l10n_latam_use_documents \
                    and move.company_id.ec_sri_environment=='2' and move.invoice_date and move.invoice_date<today \
                    and move._ec_sri_requires_point():
                doc=move.sudo().ec_sri_document_ids.filtered(lambda d:d.document_type==code)[:1]
                expired=not doc or doc.date_expired
            move.ec_sri_date_expired=expired

    @api.depends('ec_sri_document_ids.state','ec_sri_document_ids.access_key','ec_sri_document_ids.authorization_number')
    def _compute_ec_sri_status(self):
        # sudo: el estado SRI se muestra también a quien factura sin permiso SRI.
        for move in self:
            docs=move.sudo().ec_sri_document_ids
            main=docs.filtered(lambda d:d.document_type in ('01','03','04','05'))[:1] or docs[:1]
            move.ec_sri_state=main.state if main else False
            move.ec_sri_access_key=main.access_key if main else False
            move.ec_sri_authorization_number=main.authorization_number if main else False
            move.ec_sri_document_count=len(docs)
    ec_sri_supplier_authorization=fields.Char('Autorización proveedor')
    ec_sri_support_code=fields.Char('Código sustento ATS',help='Configure según catálogo SRI. No se asigna un código fiscal por defecto.')
    ec_sri_received_vat=fields.Monetary('Retención IVA recibida',currency_field='currency_id')
    ec_sri_received_income=fields.Monetary('Retención renta recibida',currency_field='currency_id')
    ec_sri_plate=fields.Char('Placa SRI')
    ec_sri_reason=fields.Char('Motivo de nota de crédito/débito')
    ec_sri_ats_treatment=fields.Selection([('include','Reportar en ATS'),('electronic_exclusion','No reportar: electrónico con requisitos de exclusión')],
        string='Tratamiento ATS',help='Clasificación explícita del responsable contable según la ficha ATS; no se deduce solo por tener clave de acceso.')
    ec_sri_ats_exclusion_reason=fields.Char('Sustento de exclusión ATS')
    # Sucursal y punto de emisión se eligen EN la factura: nunca se adivinan.
    # El número lo da el punto: 001-002 + el próximo secuencial de su línea de documento.
    ec_sri_establishment_id=fields.Many2one('ec.sri.establishment',string='Sucursal SRI',check_company=True,
        domain="[('company_id','=',company_id)]")
    ec_sri_point_id=fields.Many2one('ec.sri.point',string='Punto de emisión SRI',check_company=True,
        domain="[('id','in',ec_sri_allowed_point_ids)]")
    ec_sri_allowed_point_ids=fields.Many2many('ec.sri.point',compute='_compute_ec_sri_allowed_point_ids')
    ec_sri_point_required=fields.Boolean(compute='_compute_ec_sri_point_required')
    ec_sri_next_number=fields.Char('Número (se asigna al confirmar)',compute='_compute_ec_sri_next_number',
        help='Número que tomará al confirmar: establecimiento, punto de emisión y próximo secuencial del punto.')

    @api.depends('ec_sri_point_id.line_ids.next_number','l10n_latam_document_type_id','posted_before')
    def _compute_ec_sri_next_number(self):
        for move in self:
            line=move._ec_sri_point_line() if move.ec_sri_point_id and not move.posted_before else False
            move.ec_sri_next_number='%s-%s-%09d' % (line.point_id.establishment,line.point_id.emission,line.next_number) if line else False

    def _ec_sri_point_line(self):
        self.ensure_one()
        code=self.l10n_latam_document_type_id.code
        return self.ec_sri_point_id.line_ids.filtered(lambda l:l.document_type==code)[:1]

    @api.depends('company_id.ec_sri_enabled','move_type','l10n_latam_document_type_id')
    def _compute_ec_sri_point_required(self):
        for move in self: move.ec_sri_point_required=move._ec_sri_requires_point()

    @api.depends('company_id','ec_sri_establishment_id','l10n_latam_document_type_id')
    def _compute_ec_sri_allowed_point_ids(self):
        # Sin tipo de documento (diario sin documentos) se listan igual los puntos de
        # la sucursal: el bloqueo por el diario se explica al confirmar.
        for move in self:
            domain=[('company_id','=',move.company_id.id),('establishment_id','=',move.ec_sri_establishment_id.id),
                    ('environment','=',move.company_id.ec_sri_environment)]
            if move.l10n_latam_document_type_id.code:
                domain.append(('line_ids.document_type','=',move.l10n_latam_document_type_id.code))
            move.ec_sri_allowed_point_ids=self.env['ec.sri.point'].search(domain) if move.ec_sri_establishment_id else False

    def _ec_sri_requires_point(self):
        """Toda venta de una compañía con SRI, y la liquidación de compra (03), se emite
        al SRI: exige sucursal, punto de emisión y un diario que use documentos."""
        self.ensure_one()
        return bool(self.company_id.ec_sri_enabled and (self.move_type in ('out_invoice','out_refund')
            or (self.move_type=='in_invoice' and self.l10n_latam_document_type_id.code=='03')))

    @api.onchange('ec_sri_establishment_id')
    def _onchange_ec_sri_establishment(self):
        if self.ec_sri_point_id.establishment_id!=self.ec_sri_establishment_id:
            self.ec_sri_point_id=False

    @api.onchange('ec_sri_point_id')
    def _onchange_ec_sri_point(self):
        point=self.ec_sri_point_id
        if not point: return
        self.ec_sri_establishment_id=point.establishment_id
        # El número sale del punto. Se prefiere el diario atado a ese punto; si no hay,
        # basta con un diario que use documentos del SRI.
        if self.state=='draft' and not self.posted_before and self.journal_id:
            Journal=self.env['account.journal']
            journal=Journal.search([('company_id','=',self.company_id.id),('type','=',self.journal_id.type),
                ('ec_sri_point_id','=',point.id)],limit=1)
            if not journal and not self.journal_id.l10n_latam_use_documents:
                journal=Journal.search([('company_id','=',self.company_id.id),('type','=',self.journal_id.type),
                    ('l10n_latam_use_documents','=',True)],limit=1)
            if journal and journal!=self.journal_id: self.journal_id=journal

    @api.onchange('journal_id')
    def _onchange_ec_sri_journal(self):
        # El diario atado a un punto de emisión lo propone en la factura.
        point=self.journal_id.ec_sri_point_id
        if point and self.state=='draft' and not self.posted_before and self.ec_sri_point_id!=point:
            self.ec_sri_point_id=point
            self.ec_sri_establishment_id=point.establishment_id

    def _ec_sri_check_point(self):
        self.ensure_one()
        if not self.l10n_latam_use_documents:
            raise UserError(_('El diario %s no usa documentos del SRI, así que esta factura no se podría enviar al SRI. '
                'Use un diario con "Usar documentos" activado.',
                self.journal_id.display_name))
        establishment,point=self.ec_sri_establishment_id,self.ec_sri_point_id
        if not establishment or not point:
            raise UserError(_('Seleccione la sucursal y el punto de emisión SRI de %s antes de confirmarla.',self.display_name))
        if point.establishment_id!=establishment:
            raise UserError(_('El punto de emisión %(point)s no pertenece a la sucursal %(est)s.',
                point=point.display_name,est=establishment.display_name))
        if point.environment!=self.company_id.ec_sri_environment:
            raise UserError(_('El punto de emisión %s no está en el ambiente SRI actual de la compañía.',point.display_name))
        code=self.l10n_latam_document_type_id.code
        if code not in point.line_ids.mapped('document_type'):
            raise UserError(_('El punto de emisión %(point)s no tiene habilitado el documento %(doc)s: agréguelo en "Documentos que emite".',
                point=point.display_name,doc=self.l10n_latam_document_type_id.display_name))

    def _ec_sri_check_rules(self):
        """Reglas del SRI que se validan al confirmar, antes de consumir un número: así un
        comprobante que el SRI rechazaría no queda contabilizado. Fuentes: ficha técnica
        offline 2.34, Reglamento de Comprobantes de Venta y Res. NAC-DGERCGC25-00000014/17."""
        self.ensure_one()
        code=self.l10n_latam_document_type_id.code
        if self.currency_id.name!='USD':
            raise UserError(_('Los comprobantes electrónicos del SRI se emiten en dólares (USD).'))
        if self.company_id.tax_calculation_rounding_method!='round_per_line':
            raise UserError(_('El SRI calcula los impuestos línea por línea: en Contabilidad → Ajustes → Impuestos, '
                'cambie "Redondeo de impuestos" a "Redondear por línea" para %s.',self.company_id.name))
        today=validators.today_ec()
        issue=self.invoice_date or today
        if issue>today:
            raise UserError(_('La fecha de emisión %s es futura: el comprobante se emite y transmite el mismo día.',issue))
        if self.company_id.ec_sri_environment=='2' and not self.posted_before and issue!=today:
            raise UserError(_('Desde el 01/01/2026 el comprobante se transmite al SRI en el momento de generarse '
                '(Res. NAC-DGERCGC25-00000014): la fecha de emisión debe ser la de hoy, %s.',today))
        if code=='04' and self.reversed_entry_id:
            origin=self.reversed_entry_id
            if origin.partner_id._ec_sri_identification_code()=='07' and origin.invoice_date and \
                    origin.invoice_date>=validators.FINAL_CONSUMER_RULES_FROM:
                raise UserError(_('No procede nota de crédito sobre una factura a consumidor final '
                    '(Res. NAC-DGERCGC25-00000014, art. 3).'))
            credited=sum(self.search([('reversed_entry_id','=',origin.id),('state','=','posted'),
                ('move_type','=','out_refund'),('id','!=',self.id)]).mapped('amount_total'))
            if credited+self.amount_total>origin.amount_total+0.005:
                raise UserError(_('Las notas de crédito de %(origin)s suman %(total).2f y superan el valor de la factura '
                    '(%(invoice).2f).',origin=origin.display_name,total=credited+self.amount_total,invoice=origin.amount_total))
        partner=self.partner_id._ec_sri_partner_data()
        if partner['type']=='07':
            if code!='01':
                raise UserError(_('Las notas de crédito, notas de débito y liquidaciones de compra deben identificar al '
                    'receptor: no admiten consumidor final (ficha técnica, 5.7).'))
            if self.amount_total>validators.FINAL_CONSUMER_LIMIT:
                raise UserError(_('Una factura a consumidor final solo puede llegar a USD %(limit)s; esta suma %(total).2f. '
                    'Identifique al comprador (Reglamento, art. 19).',limit=validators.FINAL_CONSUMER_LIMIT,total=self.amount_total))
        if not self.posted_before:
            # Ficha 9.18: los comprobantes se emiten en orden cronológico y secuencial.
            later=self.search([('company_id','=',self.company_id.id),('ec_sri_point_id','=',self.ec_sri_point_id.id),
                ('l10n_latam_document_type_id.code','=',code),('state','=','posted'),('id','!=',self.id),
                ('invoice_date','>',issue)],order='invoice_date desc',limit=1)
            if later:
                raise UserError(_('Orden cronológico (ficha técnica 9.18): %(last)s del mismo punto tiene fecha %(date)s, '
                    'posterior a %(issue)s. Un número nuevo no puede llevar una fecha anterior.',
                    last=later.name,date=later.invoice_date,issue=issue))
        warning=self.partner_id._ec_sri_identification_warning()
        if warning: self.message_post(body=warning)

    def _ec_sri_known_environment(self):
        """Ambiente SRI de la factura cuando se puede saber: el de su punto de emisión o, si
        no lo tiene (facturas anteriores al campo), el de su comprobante. False si no se sabe."""
        self.ensure_one()
        if self.ec_sri_point_id: return self.ec_sri_point_id.environment
        return self.sudo().ec_sri_document_ids.filtered('environment')[:1].environment or False

    def _ec_sri_number_clashes(self,environment,journals):
        """De estas facturas, las que sí ocupan el número para un punto del ambiente
        ``environment`` cuyas facturas van a ``journals``. Una factura de OTRO ambiente
        (p. ej. pruebas, al empezar producción) en otro diario no lo ocupa: el SRI no la
        conoce y Odoo solo exige nombre único por diario. Si no se sabe su ambiente, o
        está en el mismo diario, sí lo ocupa (conservador)."""
        return self.filtered(lambda m:m.journal_id in journals or (m._ec_sri_known_environment() or environment)==environment)

    def _ec_sri_assign_number(self):
        """Numera desde el punto: Fact 001-002-000000007, con el próximo secuencial de la
        línea del documento. Al re-confirmar una factura se conserva su número."""
        self.ensure_one()
        if self.posted_before and self.name and self.name!='/': return
        line=self._ec_sri_point_line()
        point,doc=self.ec_sri_point_id,self.l10n_latam_document_type_id
        # Salta los números ya ocupados en ESTE diario (p. ej. facturas de pruebas en el
        # mismo diario al pasar a producción): Odoo no admite dos iguales en un diario.
        line._ec_sri_skip_used(journals=self.journal_id)
        number='%s-%s-%s' % (point.establishment,point.emission,line.sequence_id.sudo().next_by_id())
        # La secuencia sin huecos avanza por SQL: refrescar el "Próximo secuencial" en caché.
        line.sequence_id.invalidate_recordset(['number_next','number_next_actual'])
        line.invalidate_recordset(['next_number'])
        name='%s %s' % (doc.doc_code_prefix,number) if doc.doc_code_prefix else number
        same=self.search([('company_id','=',self.company_id.id),('l10n_latam_document_type_id.code','=',doc.code),
                          ('name','=',name),('id','!=',self.id)])
        if same._ec_sri_number_clashes(point.environment,self.journal_id):
            raise UserError(_('El número %(num)s ya existe. Ajuste el "Próximo secuencial" de %(doc)s en el punto de emisión %(point)s.',
                num=number,doc=doc.display_name,point=point.display_name))
        self.name=name

    def _post(self,soft=True):
        today=fields.Date.context_today(self)
        for move in self:
            if move._ec_sri_requires_point():
                # Facturas creadas sin formulario (ventas, suscripciones…): usan el punto de su diario.
                if not move.ec_sri_point_id and move.journal_id.ec_sri_point_id:
                    move.write(dict(ec_sri_point_id=move.journal_id.ec_sri_point_id.id,
                                    ec_sri_establishment_id=move.journal_id.ec_sri_point_id.establishment_id.id))
                move._ec_sri_check_point()
                # Las facturas con fecha futura no se confirman ahora (quedan programadas):
                # no consumen número hasta que se confirmen de verdad.
                if not (soft and move.date and move.date>today):
                    # Odoo pondría la fecha con la zona horaria del usuario (UTC si no la tiene):
                    # para el SRI la fecha de emisión es la de Ecuador.
                    if not move.invoice_date: move.invoice_date=validators.today_ec()
                    move._ec_sri_check_rules()
                    move._ec_sri_assign_number()
        return super()._post(soft)

    def _ec_sri_document_point(self,code):
        """Punto de emisión del comprobante propio. Las facturas confirmadas antes
        de existir el campo se resuelven por su número 001-001, sin adivinar."""
        self.ensure_one()
        if self.ec_sri_point_id: return self.ec_sri_point_id
        establishment,emission,_seq=self._ec_sri_number_parts()
        points=self.env['ec.sri.point'].search([('company_id','=',self.company_id.id),('establishment','=',establishment),
            ('emission','=',emission),('environment','=',self.company_id.ec_sri_environment),('line_ids.document_type','=',code)])
        if len(points)!=1:
            raise UserError(_('Seleccione la sucursal y el punto de emisión SRI de la factura: no hay un único punto %s-%s habilitado para este documento.',
                establishment,emission))
        return points

    def _ec_sri_rimpe_retention_code(self,partner=None):
        """Código de retención de renta según el régimen RIMPE del contribuyente:
        332B (Negocio Popular, 0%) / 343A (Emprendedor, 1%). Devuelve False si
        no es RIMPE."""
        partner=(partner or self.partner_id).commercial_partner_id
        return {'popular_business':'332B','entrepreneur':'343A'}.get(partner.ec_sri_rimpe_type,False)

    def _ec_sri_number_parts(self):
        self.ensure_one()
        value=self.l10n_latam_document_number or self.name or ''
        match=re.search(r'(?<!\d)(\d{3})-(\d{3})-(\d{9})$',value)
        if not match: raise UserError(_('El número debe terminar en 001-001-000000001: %s',value))
        return match.groups()

    def _ec_sri_lines(self):
        self.ensure_one()
        if self.currency_id.name!='USD': raise UserError(_('El generador nativo requiere moneda USD.'))
        if self.company_id.tax_calculation_rounding_method!='round_per_line':
            raise UserError(_('Configure redondeo de impuestos por línea para el generador SRI.'))
        lines=[]
        for line in self.invoice_line_ids.filtered(lambda l:l.display_type=='product'):
            if line.quantity<=0 or line.price_unit<0 or not 0<=line.discount<=100:
                raise UserError(_('Use cantidades positivas y descuentos entre 0 y 100.'))
            taxes=line.tax_ids
            computed=taxes.compute_all(line.price_unit*(1-line.discount/100),currency=self.currency_id,
                quantity=line.quantity,product=line.product_id,partner=self.partner_id,is_refund=self.move_type in ('out_refund','in_refund'))
            undiscounted=taxes.compute_all(line.price_unit,currency=self.currency_id,quantity=line.quantity,
                product=line.product_id,partner=self.partner_id,is_refund=self.move_type in ('out_refund','in_refund'))
            rows=[]
            for item in computed['taxes']:
                tax=self.env['account.tax'].browse(item['id'])
                if not tax.ec_sri_tax_code or not tax.ec_sri_percentage_code:
                    raise UserError(_('Configure impuesto y código de porcentaje SRI en %s.',tax.display_name))
                if tax.amount<0: raise UserError(_('Emita las retenciones por separado del XML de venta o compra.'))
                rate=tax.amount if tax.amount_type=='percent' else (item['amount']/item['base']*100 if item['base'] else 0)
                rows.append(dict(code=tax.ec_sri_tax_code,percentage=tax.ec_sri_percentage_code,
                    rate=rate,base=item['base'],amount=item['amount'],ats_bucket=tax.ec_sri_ats_bucket))
            if not rows: raise UserError(_('Configure un impuesto explícito incluso para IVA cero, exento o no objeto.'))
            if sum(1 for r in rows if r['code']=='2')!=1:
                raise UserError(_('Cada línea requiere exactamente una clasificación de IVA.'))
            if abs(computed['total_excluded']-line.price_subtotal)>.01:
                raise UserError(_('El cálculo fiscal no coincide con la base contable.'))
            lines.append(dict(code=line.product_id.default_code or str(line.product_id.id or line.id),
                auxiliary=line.product_id.product_tmpl_id.ec_sri_auxiliary_code,
                description=(line.name or line.product_id.display_name),quantity=line.quantity,
                unit_price=undiscounted['total_excluded']/line.quantity,
                discount=undiscounted['total_excluded']-computed['total_excluded'],subtotal=computed['total_excluded'],taxes=rows))
        return lines

    def _ec_sri_payments(self):
        self.ensure_one()
        payment=self.l10n_ec_sri_payment_id
        if not payment: raise UserError(_('Seleccione la forma de pago SRI.'))
        return [dict(code=payment.code,amount=self.amount_total,
                     days=max(0,(self.invoice_date_due-self.invoice_date).days) if self.invoice_date_due else 0)]

    def _ec_sri_invoice_data(self,document):
        self.ensure_one()
        if self.state!='posted' or not self.invoice_date: raise UserError(_('Publique el documento contable antes de emitir.'))
        expected={'01':'out_invoice','03':'in_invoice','04':'out_refund','05':'out_invoice'}
        if self.move_type!=expected[document.document_type] or self.l10n_latam_document_type_id.code!=document.document_type:
            raise UserError(_('El tipo contable o documento latinoamericano no coincide con el comprobante SRI.'))
        if self.invoice_date!=document.date: raise UserError(_('La fecha contable de emisión debe coincidir con el comprobante.'))
        data=dict(date=self.invoice_date.strftime('%d/%m/%Y'),address=document.point_id.address,
                  partner=self.partner_id._ec_sri_partner_data(),lines=self._ec_sri_lines(),total=self.amount_total,
                  reason=document.reason or self.ec_sri_reason,plate=self.ec_sri_plate,
                  additional={'Email':self.partner_id.email} if self.partner_id.email else {})
        if document.document_type in ('04','05'):
            original=self.reversed_entry_id if document.document_type=='04' else self.debit_origin_id
            if not original or original.state!='posted' or not data['reason']:
                raise UserError(_('Vincule la factura de origen y registre el motivo de la nota.'))
            if original.company_id!=self.company_id or original.partner_id.commercial_partner_id!=self.partner_id.commercial_partner_id:
                raise UserError(_('La factura de origen debe pertenecer a la misma empresa y contacto.'))
            data['origin']=dict(code=original.l10n_latam_document_type_id.code,
                number='-'.join(original._ec_sri_number_parts()),date=original.invoice_date.strftime('%d/%m/%Y'))
        if document.document_type!='04': data['payments']=self._ec_sri_payments()
        return data

    def _ec_sri_support_data(self):
        self.ensure_one()
        if self.partner_id.country_id.code not in (False,'EC'):
            raise UserError(_('Retenciones al exterior requieren XML externo con los campos fiscales completos.'))
        if not self.ec_sri_supplier_authorization or not self.ec_sri_support_code:
            raise UserError(_('Registre autorización del proveedor y código de sustento ATS.'))
        if not re.fullmatch(r'[0-9]{10}|[0-9]{37}|[0-9]{49}',self.ec_sri_supplier_authorization):
            raise UserError(_('La autorización del proveedor debe tener 10, 37 o 49 dígitos (ficha técnica, Anexo 10); '
                'la de un comprobante electrónico es su clave de acceso de 49 dígitos.'))
        return dict(support_code=self.ec_sri_support_code,code=self.l10n_latam_document_type_id.code,
            number=''.join(self._ec_sri_number_parts()),date=self.invoice_date.strftime('%d/%m/%Y'),
            accounting_date=self.date.strftime('%d/%m/%Y'),authorization=self.ec_sri_supplier_authorization,
            subtotal=self.amount_untaxed,total=self.amount_total,taxes=aggregate_taxes(self._ec_sri_lines()),payments=self._ec_sri_payments())

    def action_ec_sri_document(self):
        self.ensure_one()
        code=self.l10n_latam_document_type_id.code
        if code not in ('01','03','04','05') or self.move_type not in ('out_invoice','out_refund','in_invoice'):
            raise UserError(_('No se emite este tipo de documento desde la factura.'))
        if self.move_type=='in_invoice' and code!='03': raise UserError(_('Las facturas de proveedor no se envían como propias. Use Crear retención.'))
        return self._ec_sri_open_document(code)

    def action_ec_sri_retention(self):
        self.ensure_one()
        if self.move_type!='in_invoice': raise UserError(_('Seleccione una factura de proveedor.'))
        return self._ec_sri_open_document('07')

    def _ec_sri_open_document(self,code):
        self.ensure_one();self.check_access('read')
        doc=self.ec_sri_document_ids.filtered(lambda d:d.document_type==code)[:1]
        if not doc:
            if code=='07':
                point=self.env['ec.sri.point'].search([('company_id','=',self.company_id.id),('line_ids.document_type','=',code),
                    ('environment','=',self.company_id.ec_sri_environment)],limit=1)
            else:
                point=self._ec_sri_document_point(code)
            doc=self.env['ec.sri.document'].create(dict(company_id=self.company_id.id,partner_id=self.partner_id.id,
                document_type=code,move_id=self.id,point_id=point.id,date=self.invoice_date if code!='07' else validators.today_ec(),
                reason=self.ec_sri_reason))
        return {'type':'ir.actions.act_window','res_model':'ec.sri.document','view_mode':'form','res_id':doc.id}

    def action_ec_sri_emit(self):
        """Crea (si falta) y ENVÍA el comprobante al SRI en un solo clic desde la factura."""
        self.ensure_one()
        code=self.l10n_latam_document_type_id.code
        if code not in ('01','03','04','05') or self.move_type not in ('out_invoice','out_refund','in_invoice'):
            raise UserError(_('No se emite este tipo de documento desde la factura.'))
        if self.move_type=='in_invoice' and code!='03':
            raise UserError(_('Las facturas de proveedor no se envían como propias. Use Crear retención.'))
        action=self._ec_sri_open_document(code)
        doc=self.env['ec.sri.document'].browse(action['res_id'])
        if doc.state=='draft':
            doc.action_queue()
        return action

    def _ec_sri_emit_keep(self):
        """Como action_ec_sri_emit, para procesos automáticos (cobros de la plataforma,
        reintentos): un fallo al preparar o firmar NO hace perder el comprobante.

        1. Crea el comprobante si falta (con la fecha de la factura).
        2. En un savepoint: comprobaciones previas y clave de acceso, que queda grabada.
        3. En otro savepoint: XML, firma y cola (action_queue reutiliza la clave).
        Si 2 o 3 fallan solo se deshace ese paso: el comprobante queda en Borrador (con su
        clave si se llegó a asignar) y el motivo en «Última respuesta» y en el historial.
        Así el reintento lo reutiliza con su fecha de emisión en vez de crearlo de nuevo, que
        en producción lo dejaba bloqueado por la regla «fecha de emisión = hoy».

        Devuelve (comprobante, motivo del fallo o False). Solo lanza si la factura no es
        emitible (tipo de documento) o si no se puede crear el comprobante."""
        self.ensure_one()
        code=self.l10n_latam_document_type_id.code
        if code not in ('01','03','04','05') or self.move_type not in ('out_invoice','out_refund','in_invoice'):
            raise UserError(_('No se emite este tipo de documento desde la factura.'))
        if self.move_type=='in_invoice' and code!='03':
            raise UserError(_('Las facturas de proveedor no se envían como propias. Use Crear retención.'))
        doc=self.env['ec.sri.document'].browse(self._ec_sri_open_document(code)['res_id'])
        if doc.state!='draft':
            return doc,False
        for step in ('key','queue'):
            try:
                with self.env.cr.savepoint():
                    if step=='key':
                        if doc._lock():
                            company,environment=doc._ec_sri_precheck()
                            if doc.mode=='native': doc._ec_sri_reserve_key(company,environment)
                    else:
                        doc.action_queue()
            except Exception as exc:  # noqa: BLE001 - el motivo queda en el comprobante
                message=exc.args[0] if isinstance(exc,UserError) and exc.args else '%s: %s' % (type(exc).__name__,exc)
                if not isinstance(exc,UserError):
                    _logger.warning('SRI: fallo inesperado al emitir %s: %s',self.display_name,exc)
                doc._set({'last_message':message})
                doc._event('prepare_error',message)
                return doc,message
        return doc,False

    def action_ec_sri_redate_today(self):
        """«Emitir con fecha de hoy» desde la factura (ver ec.sri.document.action_redate_today)."""
        for move in self:
            if not move.ec_sri_date_expired:
                raise UserError(_('%s no tiene la fecha vencida para el SRI.',move.display_name))
            code=move.l10n_latam_document_type_id.code
            doc=self.env['ec.sri.document'].browse(move._ec_sri_open_document(code)['res_id'])
            doc.action_redate_today()
        return True

    def _ec_sri_redate(self,today):
        """Vuelve a fechar a ``today`` una factura contabilizada cuyo comprobante nunca se
        transmitió: borrador, fecha nueva y contabilizar otra vez. Conserva el número
        (posted_before) y, en Odoo 19, las conciliaciones (button_draft no las deshace)."""
        self.ensure_one()
        if self.state!='posted' or self.invoice_date==today: return
        self.check_access('write')
        old=self.invoice_date
        self.button_draft()
        self.write({'invoice_date':today})
        self.action_post()
        self.message_post(body=_('Fecha de emisión cambiada de %(old)s a %(new)s para enviarla al SRI: el comprobante '
            'nunca se había transmitido (Res. NAC-DGERCGC25-00000014: se transmite el día en que se emite).',old=old,new=today))

    def action_ec_sri_open_documents(self):
        self.ensure_one()
        return {'type':'ir.actions.act_window','res_model':'ec.sri.document','name':_('Comprobantes SRI'),
                'view_mode':'list,form','domain':[('move_id','=',self.id)]}

    def button_draft(self):
        for move in self:
            if move.sudo().ec_sri_document_ids.filtered(lambda d:d.state not in ('draft','rejected','cancelled')):
                raise UserError(_('Hay un comprobante SRI enviado o autorizado. No puede restablecer la factura.'))
        return super().button_draft()

    def button_cancel(self):
        for move in self:
            if move.sudo().ec_sri_document_ids.filtered(lambda d:d.state not in ('draft','rejected','cancelled')):
                raise UserError(_('Primero gestione la anulación o nota de crédito ante el SRI.'))
        return super().button_cancel()

    def write(self,vals):
        fiscal={'partner_id','company_id','invoice_date','currency_id','invoice_line_ids','line_ids',
                'l10n_latam_document_type_id','l10n_latam_document_number','name','l10n_ec_sri_payment_id',
                'ec_sri_plate','ec_sri_reason','ec_sri_establishment_id','ec_sri_point_id'}
        if fiscal.intersection(vals):
            for move in self:
                if move.sudo().ec_sri_document_ids.filtered(lambda d:d.document_type!='07' and d.state not in ('draft','rejected','cancelled')):
                    raise UserError(_('El contenido fiscal está bloqueado por un comprobante SRI enviado o autorizado.'))
        return super().write(vals)
