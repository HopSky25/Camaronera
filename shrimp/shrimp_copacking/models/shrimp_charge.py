from odoo import api, fields, models


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    copack_order_id = fields.Many2one(
        "shrimp.copack.order", string="Orden de empaque", index=True, ondelete="set null")

    @api.model
    def _service_product_specs(self):
        specs = super()._service_product_specs()
        specs["copack_platform"] = (
            "shrimp_copacking.product_copack_platform",
            "Comisión de la plataforma por empaque", "COMISION-EMPAQUE")
        return specs
