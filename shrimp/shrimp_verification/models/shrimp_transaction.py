from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShrimpTransaction(models.Model):
    _name = "shrimp.transaction"
    _inherit = "shrimp.transaction"

    # Estado intermedio: la compra ya está grabada, pero todavía no consumió
    # lotes. El stock queda RESERVADO hasta que el verificador emita veredicto.
    state = fields.Selection(
        selection_add=[
            ("pending_verification", "Pendiente de verificación"),
            # El veredicto no cierra la venta: la deja a la firma de las dos
            # partes. Solo cuando comprador y vendedor aceptan el informe se
            # consumen lotes y se cobra comisión.
            ("pending_acceptance", "Esperando aceptación de las partes"),
            ("confirmed",),
        ],
        ondelete={"pending_verification": "set default",
                  "pending_acceptance": "set default"},
    )

    verification_id = fields.One2many(
        "shrimp.verification", "transaction_id", string="Verificación")

    verification_state = fields.Selection(
        related="verification_id.state", string="Estado de la verificación", readonly=True)

    verifier_partner_id = fields.Many2one(
        related="verification_id.verifier_partner_id", string="Verificador", readonly=True, store=True)

    # ------------------------------------------------------------------
    # Seguimiento del despacho
    # ------------------------------------------------------------------
    # Uno por compra (lo garantiza un unique en shrimp.dispatch), pero el
    # Many2one vive del lado del despacho, así que aquí entra como inverso.
    # El Many2one calculado y almacenado es lo que permite escribir dominios
    # y campos relacionados desde la verificación sin recorrer la colección.
    dispatch_ids = fields.One2many(
        "shrimp.dispatch", "transaction_id", string="Despachos")
    dispatch_id = fields.Many2one(
        "shrimp.dispatch", string="Despacho",
        compute="_compute_dispatch_id", store=True, readonly=True)

    @api.depends("dispatch_ids")
    def _compute_dispatch_id(self):
        for rec in self:
            rec.dispatch_id = rec.dispatch_ids[:1]

    def _ensure_dispatch(self):
        """Devuelve el seguimiento del despacho, creándolo si aún no existe.

        Se llama al crear la verificación y también desde el portal, porque las
        compras que ya estaban verificándose cuando se instaló esto no pasaron
        por aquel create y aun así necesitan su pantalla.
        """
        Dispatch = self.env["shrimp.dispatch"].sudo()
        salida = Dispatch.browse()
        for rec in self:
            if rec.dispatch_ids:
                salida |= rec.dispatch_ids[:1]
                continue
            salida |= Dispatch.create({"transaction_id": rec.id})
        return salida

    needs_verification = fields.Boolean(
        string="Requiere verificación", default=False, readonly=True, copy=False,
        help="Marcada cuando la compra quedó sujeta a verificación en campo.",
    )

    can_be_completed = fields.Boolean(
        string="Lista para concluir", compute="_compute_can_be_completed",
        help="La verificación fue aprobada y el comprador puede concluir la compra.",
    )

    @api.depends("state", "verification_id.state", "verification_id.acceptance_state")
    def _compute_can_be_completed(self):
        for rec in self:
            rec.can_be_completed = (
                rec.state in ("pending_verification", "pending_acceptance")
                and rec.verification_id
                and rec.verification_id.state in ("approved", "approved_obs")
                # Un veredicto favorable ya no basta: hace falta que las dos
                # partes hayan firmado que aceptan el informe.
                and rec.verification_id.acceptance_state == "closed"
            )

    def action_await_acceptance(self):
        """Pasa la compra a la firma de las partes tras un veredicto favorable."""
        for rec in self:
            if rec.state == "pending_verification":
                rec.write({"state": "pending_acceptance"})
        return True

    def action_confirm(self):
        """Permite confirmar también las compras que venían de verificación.

        El flujo base solo procesa las transacciones en borrador; aquí las
        aprobadas por el verificador se pasan a borrador para que el mismo
        código base consuma lotes y genere la trazabilidad.
        """
        ready = self.filtered(
            lambda t: t.state in ("pending_verification", "pending_acceptance")
            and t.can_be_completed)
        if ready:
            super(ShrimpTransaction, ready).write({"state": "draft"})
        return super().action_confirm()

    def action_complete_after_verification(self):
        """Botón del comprador: concluir la compra ya verificada."""
        for rec in self:
            if rec.state not in ("pending_verification", "pending_acceptance"):
                raise UserError(_("Esta compra no está pendiente de verificación."))
            if not rec.can_be_completed:
                raise UserError(_(
                    "La compra todavía no está lista: hace falta el veredicto "
                    "aprobado y que comprador y vendedor acepten el informe."))
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
            if rec.state in ("pending_verification", "pending_acceptance"):
                rec.write({"state": "cancel"})
