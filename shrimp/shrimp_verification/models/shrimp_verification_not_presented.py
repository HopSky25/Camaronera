"""D-16: veredicto sin trabajo de campo (auditoría de procesos, CR-16).

ANTES: _close solo impedía dictaminar una verificación ya cerrada; desde
«Recibida» o «Asignada» se podía RECHAZAR sin informe y el honorario se le
cargaba al vendedor.

AHORA (decisión del usuario, definitiva):

* Aprobar, aprobar con observaciones o rechazar por el informe exigen el
  trabajo de campo: estado «En campo» o «Informe completo».
* La única excepción es el rechazo por «Producto no presentado»: el técnico
  llegó a la planta y el producto no estaba. Exige evidencia (al menos una
  foto, la hora de llegada a la planta y la explicación). Puede emitirse
  desde «Recibida», «Asignada», «En campo» o «Informe completo».
  Efectos: el honorario lo paga el VENDEDOR, el vendedor recibe un strike
  («Producto no presentado a la verificación»), la compra se cancela en el
  acto (sin segunda opinión: no hay producto que volver a verificar) y a la
  verificadora se le liquida: «Producto no presentado» ES un veredicto (fue a
  la planta y documentó la ausencia con evidencia), así que cuenta como
  trabajo hecho para la regla de pago (23-oct-2026, _shrimp_trabajo_hecho).
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

FIELD_STATES = ("in_field", "done")
NOT_PRESENTED_STATES = ("received", "assigned", "in_field", "done")


class ShrimpVerificationNotPresented(models.Model):
    _inherit = "shrimp.verification"

    rejection_kind = fields.Selection(
        [("field", "Producto no conforme (informe de campo)"),
         ("not_presented", "Producto no presentado")],
        string="Motivo del rechazo", copy=False, tracking=True,
        help="«Producto no presentado» es el único veredicto que se emite sin trabajo de campo: "
             "exige fotos, la hora de llegada a la planta y la explicación. El honorario lo paga "
             "el vendedor y suma un strike.")
    not_presented_arrival = fields.Datetime(
        string="Llegada del técnico a la planta", copy=False, tracking=True,
        help="Evidencia del rechazo por «Producto no presentado».")

    def _shrimp_check_verdict_allowed(self, state, kind):
        self.ensure_one()
        if self.verification_mode != "platform":
            return
        if state == "rejected" and kind == "not_presented":
            if self.state not in NOT_PRESENTED_STATES:
                raise UserError(_("Esta verificación ya no admite ese veredicto."))
            faltan = []
            if not self.photo_ids:
                faltan.append(_("al menos una foto"))
            if not self.not_presented_arrival:
                faltan.append(_("la hora de llegada a la planta"))
            if faltan:
                raise UserError(_(
                    "Para rechazar por «Producto no presentado» hace falta la evidencia: %s.")
                    % ", ".join(faltan))
            if self.not_presented_arrival > fields.Datetime.now():
                raise UserError(_("La hora de llegada a la planta no puede ser futura."))
            return
        if self.state not in FIELD_STATES:
            raise UserError(_(
                "Para emitir el veredicto la verificación tiene que estar en campo o con el "
                "informe completo. Sin trabajo de campo solo se puede rechazar por «Producto no "
                "presentado», con fotos y la hora de llegada a la planta."))

    def action_not_presented(self, arrival, notes):
        """Rechazo por «Producto no presentado» (portal, API y back-office)."""
        self.ensure_one()
        # Por RPC, solo quien dictamina (antes del write en sudo).
        self._shrimp_check_verifier_caller(verdict=True)
        texto = (notes or "").strip()
        if not texto:
            raise UserError(_("Explica qué encontraste al llegar a la planta."))
        vals = {"rejection_kind": "not_presented"}
        if arrival:
            vals["not_presented_arrival"] = arrival
        self.sudo().write(vals)
        self._close("rejected", notes=texto, rejection_kind="not_presented")
        return True

    def _shrimp_not_presented_effects(self):
        """Honorario al vendedor + strike + compra cancelada + liquidación."""
        self.ensure_one()
        tx = self.transaction_id.sudo()
        if "shrimp.seller.strike" in self.env:
            self.env["shrimp.seller.strike"].sudo()._shrimp_add_strike(tx, "product_not_presented")
        self.sudo()._message_log(body=_(
            "Rechazo por «Producto no presentado» (llegada a la planta: %s): el honorario lo paga "
            "el vendedor y suma un strike.") % tx.shrimp_fmt_dt(self.not_presented_arrival))


class ShrimpSellerStrikeNotPresented(models.Model):
    _inherit = "shrimp.seller.strike"

    reason = fields.Selection(
        selection_add=[("product_not_presented", "Producto no presentado a la verificación")],
        ondelete={"product_not_presented": "cascade"})

    def _shrimp_how_to_avoid(self):
        if self.reason == "product_not_presented":
            return _("Presenta el producto en la planta en la fecha y hora acordadas con la "
                     "verificadora; si no vas a poder, avisa un retraso o cancela antes de la "
                     "visita.")
        return super()._shrimp_how_to_avoid()
