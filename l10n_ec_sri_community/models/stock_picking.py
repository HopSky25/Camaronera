from odoo import fields, models, _
from odoo.exceptions import UserError

class Picking(models.Model):
    _inherit='stock.picking'
    ec_sri_carrier_id=fields.Many2one('res.partner',string='Transportista SRI',check_company=True)
    ec_sri_plate=fields.Char('Placa')
    ec_sri_departure=fields.Char('Dirección partida')
    ec_sri_destination=fields.Char('Dirección destino')
    ec_sri_start=fields.Date('Inicio transporte')
    ec_sri_end=fields.Date('Fin transporte')
    ec_sri_route=fields.Char('Ruta')
    ec_sri_reason=fields.Char('Motivo traslado',default='VENTA')
    ec_sri_document_ids=fields.One2many('ec.sri.document','picking_id',string='Guías SRI')

    def action_ec_sri_guide(self):
        self.ensure_one()
        doc=self.ec_sri_document_ids[:1]
        if not doc:
            if not self.partner_id or not self.ec_sri_start: raise UserError(_('Indique destinatario e inicio del transporte.'))
            point=self.env['ec.sri.point'].search([('company_id','=',self.company_id.id),('document_type','=','06'),
                ('environment','=',self.company_id.ec_sri_environment)],limit=1)
            doc=self.env['ec.sri.document'].create(dict(company_id=self.company_id.id,partner_id=self.partner_id.id,
                picking_id=self.id,document_type='06',point_id=point.id,date=self.ec_sri_start))
        return {'type':'ir.actions.act_window','res_model':'ec.sri.document','view_mode':'form','res_id':doc.id}

    def _ec_sri_delivery_data(self,document):
        self.ensure_one()
        if self.state not in ('assigned','done'): raise UserError(_('La transferencia debe estar preparada o realizada.'))
        if not all([self.ec_sri_carrier_id,self.ec_sri_plate,self.ec_sri_departure,self.ec_sri_destination,self.ec_sri_start,self.ec_sri_end,self.ec_sri_reason]):
            raise UserError(_('Complete los datos de transporte en la transferencia.'))
        if self.ec_sri_end<self.ec_sri_start or document.date!=self.ec_sri_start:
            raise UserError(_('Revise las fechas del transporte y emisión.'))
        p=self.partner_id._ec_sri_partner_data()
        lines=[]
        for move in self.move_ids.filtered(lambda m:m.state!='cancel'):
            quantity=move.quantity if self.state=='done' else move.product_uom_qty
            if quantity:
                lines.append(dict(code=move.product_id.default_code or str(move.product_id.id),description=move.product_id.display_name,quantity=quantity))
        return dict(address=document.point_id.address,departure=self.ec_sri_departure,
            carrier=self.ec_sri_carrier_id._ec_sri_partner_data(),plate=self.ec_sri_plate,
            start=self.ec_sri_start.strftime('%d/%m/%Y'),end=self.ec_sri_end.strftime('%d/%m/%Y'),
            recipients=[dict(vat=p['vat'],name=p['name'],address=self.ec_sri_destination,reason=self.ec_sri_reason,route=self.ec_sri_route,lines=lines)])
