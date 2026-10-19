"""Ajustes › CamaronMarket › Entregas y recepción: retrasos de entrega y
strikes (models/shrimp_transaction_delay.py, models/shrimp_seller_strike.py).

Se guardan con _shrimp_param_specs (también el 0) y el código los lee con
shrimp.settings, cayendo al valor por defecto si no existen."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettingsDelay(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_delay_max_notices = fields.Integer(
        string="Avisos de retraso por compra", default=2,
        help="Cuántas veces puede el vendedor notificar un retraso en una misma compra.")
    shrimp_delay_max_days = fields.Integer(
        string="Retraso total máximo (días)", default=15,
        help="La nueva fecha nunca puede pasar de la fecha original de la compra más estos días. "
             "Si el atraso real los supera sin despacho, el comprador puede cancelar sin costo y el "
             "vendedor recibe un strike.")
    shrimp_delay_response_hours = fields.Integer(
        string="Horas para responder un retraso", default=48,
        help="Plazo del comprador para aceptar o contraproponer (si no responde, se acepta) y del "
             "vendedor para responder la contrapropuesta (si no responde, cuenta como rechazada).")
    shrimp_delay_grace_days = fields.Integer(
        string="Días de gracia tras la fecha de entrega", default=2,
        help="Hasta cuántos días después de la fecha acordada se puede avisar el retraso. Pasados "
             "sin despacho ni aviso: strike al vendedor y el comprador puede cancelar sin costo.")
    shrimp_strike_expiry_days = fields.Integer(
        string="Vigencia de un strike (días)", default=90)
    shrimp_strike_warning = fields.Integer(
        string="Strikes para advertencia y penalización", default=3,
        help="Con estos strikes activos se advierte al vendedor, sus productos bajan en el orden del "
             "catálogo y no salen en destacados.")
    shrimp_strike_suspension = fields.Integer(
        string="Strikes para suspender la publicación", default=5,
        help="Con estos strikes activos se suspende la publicación del vendedor. Se levanta sola "
             "cuando los strikes activos bajan de este número (vencen o se anulan); un "
             "administrador también puede levantarla antes (ficha del socio › Cumplimiento de "
             "entregas).")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_delay_max_notices": ("shrimp_marketplace.delay_max_notices", 2, 0, 10),
            "shrimp_delay_max_days": ("shrimp_marketplace.delay_max_days", 15, 1, 180),
            "shrimp_delay_response_hours": ("shrimp_marketplace.delay_response_hours", 48, 1, 720),
            "shrimp_delay_grace_days": ("shrimp_marketplace.delay_grace_days", 2, 0, 60),
            "shrimp_strike_expiry_days": ("shrimp_marketplace.strike_expiry_days", 90, 1, 3650),
            "shrimp_strike_warning": ("shrimp_marketplace.strike_warning_threshold", 3, 1, 100),
            "shrimp_strike_suspension": ("shrimp_marketplace.strike_suspension_threshold", 5, 1, 100),
        })
        return specs

    def _shrimp_check_params(self):
        super()._shrimp_check_params()
        if "shrimp_strike_suspension" in self._fields and \
                (self.shrimp_strike_suspension or 0) < (self.shrimp_strike_warning or 0):
            raise ValidationError(_(
                "«%s» no puede ser menor que los strikes para advertencia.")
                % self._fields["shrimp_strike_suspension"].string)
