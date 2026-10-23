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

    def _shrimp_fee_work_done(self):
        """Regla de pago (23-oct-2026): el honorario se conserva solo si la
        verificadora emitió su veredicto (_shrimp_trabajo_hecho)."""
        self.ensure_one()
        if self.charge_type != "verification_fee":
            return super()._shrimp_fee_work_done()
        ver = self.verification_id
        return bool(ver) and ver._shrimp_trabajo_hecho()

    def _shrimp_backlog_should_cancel(self):
        """Backlog de cobros: el honorario de una compra CANCELADA solo se
        factura si la verificación se hizo; si no, se anula.

        «Hecha» = con veredicto de la verificadora (regla de pago de la
        dueña, 23-oct-2026). Antes (decisión del 14-oct) bastaba la visita
        en campo iniciada; ahora una visita sin veredicto no es trabajo
        hecho, igual que en la cancelación de la compra
        (shrimp.transaction._shrimp_void_charges_for_cancel)."""
        res = super()._shrimp_backlog_should_cancel()
        if res or self.charge_type != "verification_fee":
            return res
        tx = self.transaction_id
        if not tx or tx.state not in ("cancel", "cancelled"):
            return False
        return not self._shrimp_fee_work_done()
