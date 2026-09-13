import re
from odoo import fields, models, _
from odoo.exceptions import UserError
from ..services.xml_utils import money, decimal
from ..services.builders import aggregate_taxes

class AccountMove(models.Model):
    _inherit='account.move'
    ec_sri_document_ids=fields.One2many('ec.sri.document','move_id',string='Comprobantes SRI')
    ec_sri_supplier_authorization=fields.Char('Autorización proveedor')
    ec_sri_support_code=fields.Char('Código sustento ATS',help='Configure según catálogo SRI. No se asigna un código fiscal por defecto.')
    ec_sri_received_vat=fields.Monetary('Retención IVA recibida',currency_field='currency_id')
    ec_sri_received_income=fields.Monetary('Retención renta recibida',currency_field='currency_id')
    ec_sri_plate=fields.Char('Placa SRI')
    ec_sri_reason=fields.Char('Motivo de nota de crédito/débito')
    ec_sri_ats_treatment=fields.Selection([('include','Reportar en ATS'),('electronic_exclusion','No reportar: electrónico con requisitos de exclusión')],
        string='Tratamiento ATS',help='Clasificación explícita del responsable contable según la ficha ATS; no se deduce solo por tener clave de acceso.')
    ec_sri_ats_exclusion_reason=fields.Char('Sustento de exclusión ATS')

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
            point=self.env['ec.sri.point'].search([('company_id','=',self.company_id.id),('document_type','=',code),
                ('environment','=',self.company_id.ec_sri_environment)],limit=1)
            doc=self.env['ec.sri.document'].create(dict(company_id=self.company_id.id,partner_id=self.partner_id.id,
                document_type=code,move_id=self.id,point_id=point.id,date=self.invoice_date if code!='07' else fields.Date.context_today(self),
                reason=self.ec_sri_reason))
        return {'type':'ir.actions.act_window','res_model':'ec.sri.document','view_mode':'form','res_id':doc.id}

    def button_draft(self):
        for move in self:
            if move.ec_sri_document_ids.filtered(lambda d:d.state not in ('draft','rejected','cancelled')):
                raise UserError(_('Hay un comprobante SRI enviado o autorizado. No puede restablecer la factura.'))
        return super().button_draft()

    def button_cancel(self):
        for move in self:
            if move.ec_sri_document_ids.filtered(lambda d:d.state not in ('draft','rejected','cancelled')):
                raise UserError(_('Primero gestione la anulación o nota de crédito ante el SRI.'))
        return super().button_cancel()

    def write(self,vals):
        fiscal={'partner_id','company_id','invoice_date','currency_id','invoice_line_ids','line_ids',
                'l10n_latam_document_type_id','l10n_latam_document_number','name','l10n_ec_sri_payment_id',
                'ec_sri_plate','ec_sri_reason'}
        if fiscal.intersection(vals):
            for move in self:
                if move.ec_sri_document_ids.filtered(lambda d:d.document_type!='07' and d.state not in ('draft','rejected','cancelled')):
                    raise UserError(_('El contenido fiscal está bloqueado por un comprobante SRI enviado o autorizado.'))
        return super().write(vals)
