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

    def _shrimp_backlog_should_cancel(self):
        """Backlog de cobros (decisión del usuario, 2026-10-14): el honorario
        de una compra CANCELADA solo se factura si la verificación se hizo
        (visita en campo iniciada o veredicto emitido); si no, se anula."""
        res = super()._shrimp_backlog_should_cancel()
        if res or self.charge_type != "verification_fee":
            return res
        tx = self.transaction_id
        if not tx or tx.state not in ("cancel", "cancelled"):
            return False
        ver = self.verification_id
        hecha = ver and (ver.field_start_date or ver.verified_date or ver.state in (
            "in_field", "done", "approved", "approved_obs", "rejected"))
        return not hecha
