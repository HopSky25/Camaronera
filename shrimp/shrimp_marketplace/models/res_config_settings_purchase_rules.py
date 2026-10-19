"""Ajustes › CamaronMarket › Facturación: plazo, recordatorios y bloqueo de la
factura del vendedor (D-43; models/shrimp_purchase_rules.py)."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettingsPurchaseRules(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_seller_invoice_days = fields.Integer(
        string="Días para registrar la factura del vendedor", default=7,
        help="Desde que la compra se completa, el vendedor tiene estos días para registrar la "
             "factura de la mercadería. Después, la venta aparece como «factura vencida».")
    shrimp_seller_invoice_lock_days = fields.Integer(
        string="Días en que la factura se puede modificar", default=30,
        help="Pasados estos días desde que la compra se completó, la factura registrada ya no se "
             "puede cambiar (ni desde el portal ni desde el back-office).")
    shrimp_seller_invoice_reminder_1 = fields.Integer(
        string="Primer recordatorio (día)", default=3,
        help="Días después de completada la compra (0 = sin este recordatorio).")
    shrimp_seller_invoice_reminder_2 = fields.Integer(
        string="Segundo recordatorio (día)", default=6)
    shrimp_seller_invoice_reminder_3 = fields.Integer(
        string="Tercer recordatorio (día)", default=7)

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_seller_invoice_days": ("shrimp_marketplace.seller_invoice_days", 7, 1, 365),
            "shrimp_seller_invoice_lock_days": ("shrimp_marketplace.seller_invoice_lock_days", 30, 1,
                                                3650),
            "shrimp_seller_invoice_reminder_1": ("shrimp_marketplace.seller_invoice_reminder_1", 3,
                                                 0, 365),
            "shrimp_seller_invoice_reminder_2": ("shrimp_marketplace.seller_invoice_reminder_2", 6,
                                                 0, 365),
            "shrimp_seller_invoice_reminder_3": ("shrimp_marketplace.seller_invoice_reminder_3", 7,
                                                 0, 365),
        })
        return specs

    def _shrimp_check_params(self):
        super()._shrimp_check_params()
        if "shrimp_seller_invoice_lock_days" in self._fields and \
                (self.shrimp_seller_invoice_lock_days or 0) < (self.shrimp_seller_invoice_days or 0):
            raise ValidationError(_(
                "«%s» no puede ser menor que los días para registrar la factura.")
                % self._fields["shrimp_seller_invoice_lock_days"].string)
        for fname in ("shrimp_seller_invoice_reminder_1", "shrimp_seller_invoice_reminder_2",
                      "shrimp_seller_invoice_reminder_3"):
            if fname in self._fields and (self[fname] or 0) > (self.shrimp_seller_invoice_days or 0):
                raise ValidationError(_(
                    "«%s» tiene que caer dentro del plazo para registrar la factura.")
                    % self._fields[fname].string)
