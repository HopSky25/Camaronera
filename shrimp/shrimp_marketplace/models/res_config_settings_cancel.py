"""Ajustes › CamaronMarket › Entregas y recepción: compra «No entregada»
(models/shrimp_transaction_reception.py, shrimp_transaction_cancel.py)."""
from odoo import api, fields, models


class ResConfigSettingsCancel(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_not_delivered_cancel_days = fields.Integer(
        string="Días para cancelar una compra no entregada", default=7,
        help="Si vence el plazo de recepción y el vendedor nunca marcó el despacho, la compra NO se "
             "confirma sola: queda «No entregada» y el comprador puede confirmarla (si la recibió) o "
             "cancelarla sin costo. Si no hace nada en estos días, se cancela automáticamente sin "
             "costo (stock de vuelta al vendedor, comisión anulada, strike al vendedor). "
             "0 = nunca: la resuelve el comprador o la plataforma.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_not_delivered_cancel_days": (
                "shrimp_marketplace.not_delivered_cancel_days", 7, 0, 365),
        })
        return specs
