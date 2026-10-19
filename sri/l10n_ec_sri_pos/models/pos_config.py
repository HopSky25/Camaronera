# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError


class EcSriPoint(models.Model):
    _inherit = 'ec.sri.point'

    # POS cuyo ec_sri_point_id apunta a este punto (solo lectura en la vista).
    pos_config_ids = fields.One2many('pos.config', 'ec_sri_point_id', string='Puntos de venta')

    def action_ec_sri_add_pos(self):
        """Abre el modal para vincular un POS existente a este punto."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Agregar punto de venta'),
            'res_model': 'ec.sri.point.pos.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_point_id': self.id},
        }


class EcSriPointPosWizard(models.TransientModel):
    _name = 'ec.sri.point.pos.wizard'
    _description = 'Asistente: vincular POS a punto de emisión'

    point_id = fields.Many2one('ec.sri.point', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='point_id.company_id')
    pos_config_id = fields.Many2one(
        'pos.config', required=True, string='Punto de venta',
        domain="[('company_id','=',company_id)]")

    def action_link(self):
        self.ensure_one()
        pos = self.pos_config_id
        if pos.ec_sri_point_id == self.point_id:
            raise UserError(_('El punto de venta "%s" ya está agregado a este punto de emisión.') % pos.name)
        if pos.ec_sri_point_id:
            raise UserError(_('El punto de venta "%s" ya está asignado al punto de emisión "%s". '
                              'Quítalo de allá antes de reasignarlo.') % (pos.name, pos.ec_sri_point_id.name))
        pos.ec_sri_point_id = self.point_id
        return {'type': 'ir.actions.act_window_close'}


class PosConfig(models.Model):
    _inherit = 'pos.config'

    ec_sri_point_id = fields.Many2one(
        'ec.sri.point', string='Punto de emisión SRI',
        domain="[('line_ids.document_type','=','01'),('company_id','=',company_id)]",
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
            # La factura se numera desde el punto; el diario solo debe usar documentos del SRI.
            if not config.invoice_journal_id.l10n_latam_use_documents:
                raise ValidationError(_(
                    'El diario de facturas %s de la caja debe tener "Usar documentos" para emitir al SRI.',
                    config.invoice_journal_id.display_name))
            consumer = config.ec_sri_final_consumer_id
            if consumer and consumer.commercial_partner_id._ec_sri_identification_code() != '07':
                raise ValidationError(_(
                    'El cliente consumidor final debe tener Identificación SRI = Consumidor final.'))
