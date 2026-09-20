import base64
import calendar
import csv
import io
import zipfile
from collections import defaultdict
from datetime import date
from decimal import Decimal
from lxml import etree
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ..services import ats, xml_utils

class Reports(models.TransientModel):
    _name='ec.sri.report.wizard'
    _description='Reportes Ecuador Community'
    company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company)
    date_from=fields.Date('Desde',required=True,default=lambda s:fields.Date.context_today(s).replace(day=1))
    date_to=fields.Date('Hasta',required=True,default=fields.Date.context_today)
    kind=fields.Selection([('ats','ATS XML mensual'),('103','Auxiliar casilleros 103'),('104','Auxiliar casilleros 104'),
        ('sales','Libro de ventas'),('purchases','Libro de compras'),('withholdings','Retenciones emitidas'),
        ('status','Estado de comprobantes'),('balance','Balance de comprobación')],required=True,default='ats',string='Reporte')
    reviewed=fields.Boolean('He revisado las obligaciones y la cobertura del periodo',
        help='ATS automático: operaciones nacionales ordinarias en USD. No cubre importaciones, exportaciones, reembolsos, dividendos, fideicomisos ni regímenes sectoriales.')
    data=fields.Binary('Archivo',readonly=True,attachment=False)
    filename=fields.Char(readonly=True)

    def _moves(self):
        return self.env['account.move'].search([('company_id','=',self.company_id.id),('state','=','posted'),
            ('date','>=',self.date_from),('date','<=',self.date_to),('move_type','in',['out_invoice','out_refund','in_invoice','in_refund'])],order='date,id')

    def _ats(self):
        start,end=self.date_from,self.date_to
        if start.day!=1 or (start.year,start.month)!=(end.year,end.month) or end.day!=calendar.monthrange(start.year,start.month)[1]:
            raise UserError(_('ATS requiere un mes calendario completo.'))
        if not self.reviewed: raise UserError(_('Revise la cobertura y marque la confirmación del periodo.'))
        purchases=[];sales=[];errors=[]
        docs=self.env['ec.sri.document'].search([('company_id','=',self.company_id.id),('date','>=',start),('date','<=',end),('document_type','!=','06')])
        if docs.filtered(lambda d:d.state!='cancelled' and (not d.move_id or (d.document_type=='07' and d.mode=='xml'))):
            raise UserError(_('Hay XML externos sin cobertura contable para ATS. Vincule los movimientos y complete esos casos fuera del generador nativo.'))
        for move in self._moves():
            try:
                if not move.ec_sri_ats_treatment:
                    raise UserError(_('Defina el tratamiento ATS: reportar o exclusión electrónica sustentada.'))
                if move.ec_sri_ats_treatment=='electronic_exclusion':
                    if not move.ec_sri_ats_exclusion_reason:
                        raise UserError(_('La exclusión electrónica requiere sustento registrado.'))
                    if move.move_type.startswith('out_'):
                        if not move.ec_sri_document_ids.filtered(lambda d:d.document_type==move.l10n_latam_document_type_id.code and d.state=='authorized' and d.environment=='2'):
                            raise UserError(_('La venta excluida no tiene autorización electrónica de producción.'))
                    else:
                        xml_utils.validate_key(move.ec_sri_supplier_authorization or '')
                    continue
                if move.partner_id.country_id.code not in (False,'EC'): raise UserError(_('Operación exterior no cubierta por ATS nativo.'))
                code=move.l10n_latam_document_type_id.code
                if code not in ('01','03','04','05'): raise UserError(_('Tipo de comprobante no cubierto por ATS nativo.'))
                p=move.partner_id._ec_sri_partner_data()
                if p['type'] not in ('04','05','06','07'): raise UserError(_('Identificación exterior requiere ATS específico.'))
                amount=ats.bases(move._ec_sri_lines());number=move._ec_sri_number_parts()
                pay=[row['code'] for row in move._ec_sri_payments()] if code!='04' else []
                common=dict(code=code,vat=p['vat'],related=p['related'],number=number,bases=amount,payments=pay,subtotal=move.amount_untaxed)
                if move.move_type.startswith('out_'):
                    document=move.ec_sri_document_ids.filtered(lambda d:d.document_type==code and d.state=='authorized' and d.environment=='2')
                    if not document: raise UserError(_('La venta no tiene autorización SRI en producción.'))
                    common.update(id_type=p['type'],vat_ret=move.ec_sri_received_vat,income_ret=move.ec_sri_received_income)
                    sales.append(common)
                else:
                    if not move.ec_sri_supplier_authorization or not move.ec_sri_support_code: raise UserError(_('Falta autorización o sustento tributario.'))
                    common.update(id_type={'04':'01','05':'02','06':'03'}[p['type']],support_code=move.ec_sri_support_code,
                        accounting_date=move.date.strftime('%d/%m/%Y'),date=move.invoice_date.strftime('%d/%m/%Y'),authorization=move.ec_sri_supplier_authorization)
                    retention=move.ec_sri_document_ids.filtered(lambda d:d.document_type=='07' and d.state!='cancelled')
                    if retention:
                        if retention.state!='authorized' or retention.environment!='2': raise UserError(_('La retención todavía no está autorizada en producción.'))
                        if retention.mode!='native': raise UserError(_('Retención XML externa requiere completar ATS fuera del generador nativo.'))
                        if any(l.tax_type=='6' for l in retention.retention_ids): raise UserError(_('Retención ISD requiere reporte específico.'))
                        common['retentions']=[dict(code=l.tax_type,retention_code=l.retention_code,base=l.base,rate=l.rate,amount=l.amount) for l in retention.retention_ids]
                        common['retention']=dict(key=retention.access_key,date=retention.date.strftime('%d/%m/%Y'))
                    if code in ('04','05'):
                        origin=move.reversed_entry_id if code=='04' else move.debit_origin_id
                        if not origin or not origin.ec_sri_supplier_authorization: raise UserError(_('Falta factura original autorizada de la nota.'))
                        common['origin']=dict(code=origin.l10n_latam_document_type_id.code,number=origin._ec_sri_number_parts(),authorization=origin.ec_sri_supplier_authorization)
                    purchases.append(common)
            except (UserError,ValueError,KeyError) as exc:
                errors.append('%s: %s' % (move.name,str(exc)))
        if errors: raise UserError(_('No se genera ATS con omisiones:\n%s','\n'.join(errors)))
        cancelled=self.env['ec.sri.document'].search([('company_id','=',self.company_id.id),('state','=','cancelled'),('environment','=','2'),
            ('cancellation_date','>=',start),('cancellation_date','<=',end)])
        company=dict(vat=self.company_id.vat,name=self.company_id.name,establishments=self.company_id.ec_sri_establishments)
        return ats.build(company,start.year,start.month,purchases,sales,[dict(key=d.access_key) for d in cancelled])

    def _csv(self,rows):
        stream=io.StringIO(newline='');writer=csv.writer(stream,delimiter=';')
        for row in rows:
            writer.writerow([("'"+value) if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value for value in row])
        return stream.getvalue().encode('utf-8-sig')

    def _report_rows(self):
        if self.kind in ('sales','purchases'):
            yield ['Fecha contable','Fecha emisión','Documento','Identificación','Contacto','Base USD','Impuestos USD','Total USD']
            prefix='out_' if self.kind=='sales' else 'in_'
            for move in self._moves().filtered(lambda m:m.move_type.startswith(prefix)):
                if move.currency_id.name!='USD': raise UserError(_('El libro requiere convertir movimientos de otras monedas.'))
                sign=-1 if move.move_type.endswith('refund') else 1
                yield [move.date,move.invoice_date,move.name,move.partner_id.vat,move.partner_id.name,
                       xml_utils.money(sign*move.amount_untaxed),xml_utils.money(sign*move.amount_tax),xml_utils.money(sign*move.amount_total)]
        elif self.kind in ('status','withholdings'):
            docs=self.env['ec.sri.document'].search([('company_id','=',self.company_id.id),('date','>=',self.date_from),('date','<=',self.date_to)],order='date,id')
            if self.kind=='status':
                yield ['Fecha','Documento','Ambiente','Estado','Clave','Autorización','Intentos','Mensaje']
                for d in docs: yield [d.date,d.name,d.environment,d.state,d.access_key,d.authorization_number,d.attempts,d.last_message]
            else:
                yield ['Fecha','Retención','Ambiente','Estado','Sujeto','Impuesto','Código','Base','Porcentaje','Retenido']
                for d in docs.filtered(lambda d:d.document_type=='07'):
                    if d.mode=='xml': raise UserError(_('El auxiliar de retenciones requiere registros nativos; consulte el XML externo por separado.'))
                    for line in d.retention_ids: yield [d.date,d.name,d.environment,d.state,d.partner_id.vat,line.tax_type,line.retention_code,xml_utils.money(line.base),line.rate,xml_utils.money(line.amount)]
        elif self.kind=='balance':
            if self.company_id.currency_id.name!='USD': raise UserError(_('Este reporte está expresado en USD.'))
            lines=self.env['account.move.line'].search([('company_id','=',self.company_id.id),('parent_state','=','posted'),('date','<=',self.date_to)])
            amounts={}
            for line in lines:
                key=line.account_id
                values=amounts.setdefault(key,[0,0,0])
                if line.date<self.date_from: values[0]+=line.balance
                else: values[1]+=line.debit;values[2]+=line.credit
            yield ['Cuenta','Nombre','Saldo inicial USD','Debe USD','Haber USD','Saldo final USD']
            for account,values in sorted(amounts.items(),key=lambda row:row[0].code):
                opening,debit,credit=values
                yield [account.code,account.name,*map(xml_utils.money,[opening,debit,credit,opening+debit-credit])]
        else:
            if self.company_id.currency_id.name!='USD': raise UserError(_('Este auxiliar está expresado en USD.'))
            lines=self.env['account.move.line'].search([('company_id','=',self.company_id.id),('parent_state','=','posted'),
                ('date','>=',self.date_from),('date','<=',self.date_to),('tax_line_id','!=',False)])
            amounts=defaultdict(Decimal);seen=set();missing=[]
            for line in lines:
                tax=line.tax_line_id
                if tax.ec_sri_form!=self.kind: continue
                if not tax.ec_sri_tax_box or not tax.ec_sri_base_box: missing.append(tax.display_name);continue
                sign=-1 if line.move_id.move_type.startswith('out_') else 1
                amounts[tax.ec_sri_tax_box]+=xml_utils.decimal(sign*line.balance)
                key=(line.move_id.id,tax.id)
                if key not in seen:
                    seen.add(key)
                    base_sign=-1 if line.move_id.move_type.endswith('refund') else 1
                    amounts[tax.ec_sri_base_box]+=xml_utils.decimal(base_sign*line.tax_base_amount)
            if self.kind=='103':
                documents=self.env['ec.sri.document'].search([('company_id','=',self.company_id.id),('document_type','=','07'),
                    ('state','=','authorized'),('environment','=','2'),('date','>=',self.date_from),('date','<=',self.date_to)])
                for document in documents:
                    if document.mode!='native' or not document.retention_move_id:
                        raise UserError(_('El auxiliar 103 requiere retenciones nativas contabilizadas.'))
                    for detail in document.retention_ids.filtered(lambda l:l.tax_type=='1'):
                        if not detail.base_box or not detail.tax_box:
                            missing.append(document.name+' / '+detail.retention_code)
                            continue
                        amounts[detail.base_box]+=xml_utils.decimal(detail.base)
                        amounts[detail.tax_box]+=xml_utils.decimal(detail.amount)
            if missing: raise UserError(_('Faltan casilleros para: %s',', '.join(sorted(set(missing)))))
            if not amounts: raise UserError(_('No hay impuestos configurados para este auxiliar en el periodo.'))
            yield ['AUXILIAR DE TRABAJO; no es declaración presentada','Formulario','Casillero','Importe USD']
            for box,amount in sorted(amounts.items()): yield ['Requiere conciliación y ajustes fiscales',self.kind,box,xml_utils.money(amount)]

    def action_generate(self):
        self.ensure_one()
        if self.company_id not in self.env.companies: raise UserError(_('Empresa no habilitada en la sesión.'))
        self=self.with_company(self.company_id)
        if self.date_from>self.date_to: raise UserError(_('Periodo inválido.'))
        try: content=self._ats() if self.kind=='ats' else self._csv(self._report_rows())
        except (ValueError,etree.LxmlError) as exc: raise UserError(_('Validación del reporte: %s',str(exc))) from exc
        filename='%s_%s_%s.csv' % (self.kind,self.date_from,self.date_to)
        if self.kind=='ats':
            stem='AT%02d%04d' % (self.date_from.month,self.date_from.year)
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(stem+'.xml',content)
            content=stream.getvalue();filename=stem+'.zip'
        self.write({'data':base64.b64encode(content),'filename':filename})
        return {'type':'ir.actions.act_url','url':'/web/content/ec.sri.report.wizard/%s/data/%s?download=true' % (self.id,self.filename),'target':'self'}
