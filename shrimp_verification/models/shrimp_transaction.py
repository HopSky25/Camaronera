from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShrimpTransaction(models.Model):
    _name = "shrimp.transaction"
    _inherit = "shrimp.transaction"

    # Estado intermedio: la compra ya está grabada, pero todavía no consumió
    # lotes. El stock queda RESERVADO hasta que el verificador emita veredicto.
    state = fields.Selection(
        selection_add=[("pending_verification", "Pendiente de verificación"), ("confirmed",)],
        ondelete={"pending_verification": "set default"},
    )

    verification_id = fields.One2many(
        "shrimp.verification", "transaction_id", string="Verificación")

    verification_state = fields.Selection(
        related="verification_id.state", string="Estado de la verificación", readonly=True)

    verifier_partner_id = fields.Many2one(
        related="verification_id.verifier_partner_id", string="Verificador", readonly=True, store=True)

    needs_verification = fields.Boolean(
        string="Requiere verificación", default=False, readonly=True, copy=False,
        help="Marcada cuando la compra quedó sujeta a verificación en campo.",
    )

    can_be_completed = fields.Boolean(
        string="Lista para concluir", compute="_compute_can_be_completed",
        help="La verificación fue aprobada y el comprador puede concluir la compra.",
    )

    @api.depends("state", "verification_id.state")
    def _compute_can_be_completed(self):
        for rec in self:
            rec.can_be_completed = (
                rec.state == "pending_verification"
                and rec.verification_id
                and rec.verification_id.state in ("approved", "approved_obs")
            )

    def action_confirm(self):
        """Permite confirmar también las compras que venían de verificación.

        El flujo base solo procesa las transacciones en borrador; aquí las
        aprobadas por el verificador se pasan a borrador para que el mismo
        código base consuma lotes y genere la trazabilidad.
        """
        ready = self.filtered(lambda t: t.state == "pending_verification" and t.can_be_completed)
        if ready:
            super(ShrimpTransaction, ready).write({"state": "draft"})
        return super().action_confirm()

    def action_complete_after_verification(self):
        """Botón del comprador: concluir la compra ya verificada."""
        for rec in self:
            if rec.state != "pending_verification":
                raise UserError(_("Esta compra no está pendiente de verificación."))
            if not rec.can_be_completed:
                raise UserError(_("La verificación todavía no está aprobada."))
            rec.action_confirm()
            # Comisión del marketplace, igual que en la compra directa.
            self.env["shrimp.charge"].sudo().register_for_transaction(rec, rec.transaction_qty)
        return True

    def action_cancel_for_verification(self):
        """Cancela la compra pendiente y libera la reserva de stock.

        No hay lotes que devolver: en el flujo con verificación nunca llegaron a
        consumirse, solo estaban reservados.
        """
        for rec in self:
            if rec.state == "pending_verification":
                rec.write({"state": "cancel"})
