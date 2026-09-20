# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = 'pos.config'

    ec_sri_point_id = fields.Many2one(
        'ec.sri.point', string='Punto de emisión SRI',
        domain="[('document_type','=','01'),('company_id','=',company_id)]",
        help='Al asignarlo, cada venta de esta caja emite Factura electrónica '
             'al SRI (comprobante 01) de forma automática. Debe apuntar a un '
             'punto de emisión de tipo Factura de la misma compañía.')
    ec_sri_final_consumer_id = fields.Many2one(
        'res.partner', string='Cliente consumidor final',
        help='Se asigna a las órdenes sin cliente. Debe tener '
             'Identificación SRI = Consumidor final (07).')

    @api.constrains('ec_sri_point_id', 'invoice_journal_id', 'ec_sri_final_consumer_id')
    def _check_ec_sri_pos(self):
        for config in self:
            if not config.ec_sri_point_id:
                continue
            if not config.invoice_journal_id:
                raise ValidationError(_(
                    'Configure un diario de facturas en la caja para emitir al SRI.'))
            if config.ec_sri_point_id.environment != config.company_id.ec_sri_environment:
                raise ValidationError(_(
                    'El punto de emisión debe estar en el mismo ambiente SRI que la compañía.'))
            consumer = config.ec_sri_final_consumer_id
            if consumer and consumer.commercial_partner_id.ec_sri_identification_type != '07':
                raise ValidationError(_(
                    'El cliente consumidor final debe tener Identificación SRI = Consumidor final.'))
