from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpCopackPlatformWizard(models.TransientModel):
    """Resoluciones de la plataforma sobre una orden de empaque (D-14, D-34).

    Una sola ventana con motivo obligatorio para las cuatro decisiones que la
    plataforma toma en una maquila: mediar un acta en disputa (darla por buena
    o reabrirla para rectificar) y aprobar o rechazar la cancelación que pide
    una planta con el camarón del cliente dentro.
    """

    _name = "shrimp.copack.platform.wizard"
    _description = "Resolución de la plataforma en una orden de empaque"

    order_id = fields.Many2one("shrimp.copack.order", string="Orden", required=True)
    action = fields.Selection(
        [("uphold", "Dar por buena el acta"),
         ("reopen", "Reabrir el acta para rectificar"),
         ("approve_cancel", "Aprobar la cancelación"),
         ("reject_cancel", "Rechazar la cancelación")],
        string="Resolución", required=True)
    note = fields.Text(string="Motivo / nota para las partes", required=True)
    dispute_reason = fields.Text(string="Disconformidad", compute="_compute_info")
    cancellation_reason = fields.Text(string="Motivo de la planta", compute="_compute_info")

    @api.depends("order_id", "action")
    def _compute_info(self):
        for rec in self:
            orden = rec.order_id.sudo()
            rechazo = orden.acceptance_ids.filtered(
                lambda f: f.active and f.decision == "rejected")[:1]
            rec.dispute_reason = rechazo.reason or False
            rec.cancellation_reason = orden.pending_cancellation_id.reason or False

    def action_confirm(self):
        self.ensure_one()
        orden = self.order_id
        orden._shrimp_check_platform()
        if self.action == "uphold":
            orden.action_mediation_uphold(self.note)
        elif self.action == "reopen":
            orden.action_mediation_reopen(self.note)
        else:
            pendiente = orden.pending_cancellation_id
            if not pendiente:
                raise ValidationError(_("No hay una cancelación pendiente en esta orden."))
            pendiente.action_platform_decide(self.action == "approve_cancel", self.note)
        return {"type": "ir.actions.act_window_close"}
