from odoo import api, fields, models


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    verification_id = fields.Many2one(
        "shrimp.verification", string="Verificación", index=True, ondelete="set null")

    @api.model
    def _service_product_specs(self):
        specs = super()._service_product_specs()
        # Mismo xmlid que usaba la facturación propia de la verificación, para
        # no duplicar el producto en las bases que ya lo tienen.
        specs["verification_fee"] = (
            "shrimp_verification.product_field_verification",
            "Verificación de camarón en campo", "VERIF-CAMPO")
        return specs
