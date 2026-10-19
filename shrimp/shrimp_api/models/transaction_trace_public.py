"""D-29 (auditoría de procesos): qué compras tienen página pública /t/<token>.

* Solo las compras confirmadas o completadas. Nunca un borrador ni una
  compra en verificación o por firmar (antes /t/ publicaba la cadena de
  cualquier compra con token, también las canceladas y en borrador).
* Una compra cancelada que llegó a confirmarse conserva la página, con el
  sello «Compra cancelada» bien visible (página y API pública:
  "cancelled": true). Si se canceló sin confirmarse nunca, no hay página.
* Cualquiera de las dos partes puede RETIRAR la página (el QR deja de
  funcionar) o RENOVAR el enlace (token nuevo: los QR impresos dejan de
  funcionar y la página vuelve a publicarse) desde la compra en el portal.

El retiro vive en una tabla propia (shrimp.trace.withdrawal): ninguna columna
nueva en shrimp_transaction, así el servicio vivo puede cargar este código
antes del -u sin romper (mientras la tabla no exista, nada está retirado).

API: el token, su rotación (POST /transactions/{id}/trace-token:rotate, que
ahora también vuelve a publicar una página retirada) y public_url siguen
igual. La API pública responde 404 en los mismos casos que la página.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import sql

_TABLE = "shrimp_trace_withdrawal"
PUBLIC_STATES = ("confirmed", "done")


def shrimp_trace_ready(env):
    reg = env.registry
    if getattr(reg, "_shrimp_trace_ready", False):
        return True
    listo = sql.table_exists(env.cr, _TABLE)
    if listo:
        reg._shrimp_trace_ready = True
    return listo


class ShrimpTraceWithdrawal(models.Model):
    _name = "shrimp.trace.withdrawal"
    _description = "Página pública de trazabilidad retirada"
    _order = "id desc"

    transaction_id = fields.Many2one("shrimp.transaction", string="Compra", required=True,
                                     ondelete="cascade", index=True)
    withdrawn_by_id = fields.Many2one("res.users", string="Retirada por", readonly=True)
    withdrawn_partner_id = fields.Many2one("res.partner", string="Parte que la retiró",
                                           readonly=True)
    note = fields.Char(string="Nota")

    _shrimp_trace_withdrawal_uniq = models.Constraint(
        "unique(transaction_id)", "La página pública de esta compra ya está retirada.")


class ShrimpTransactionTracePublic(models.Model):
    _inherit = "shrimp.transaction"

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def _shrimp_trace_withdrawal(self):
        self.ensure_one()
        if not self.id or not shrimp_trace_ready(self.env):
            return self.env["shrimp.trace.withdrawal"]
        return self.env["shrimp.trace.withdrawal"].sudo().search(
            [("transaction_id", "=", self.id)], limit=1)

    def _shrimp_was_confirmed(self):
        """¿La compra llegó a confirmarse? (tiene la venta en movimientos)."""
        self.ensure_one()
        return bool(self.sudo().stock_move_ids.filtered(
            lambda m: (m.move_type or "transfer") == "transfer"))

    def shrimp_trace_public_status(self):
        """'public' | 'cancelled' (con sello) | 'withdrawn' | 'unavailable'."""
        self.ensure_one()
        tx = self.sudo()
        if tx.state in PUBLIC_STATES:
            estado = "public"
        elif tx.state == "cancel" and tx._shrimp_was_confirmed():
            estado = "cancelled"
        else:
            return "unavailable"
        if tx._shrimp_trace_withdrawal():
            return "withdrawn"
        return estado

    def shrimp_trace_public_visible(self):
        self.ensure_one()
        return self.shrimp_trace_public_status() in ("public", "cancelled")

    @api.model
    def _shrimp_trace_by_token(self, token):
        """La compra del token si su página pública se puede mostrar."""
        if not token or len(token) < 16 or len(token) > 64:
            return self.browse()
        tx = self.sudo().search([("trace_token", "=", token)], limit=1)
        if not tx or not tx.shrimp_trace_public_visible():
            return self.browse()
        return tx

    def _public_traceability_data(self):
        data = super()._public_traceability_data()
        cancelada = self.sudo().state == "cancel"
        data["cancelled"] = cancelada
        if cancelada:
            data["notice"] = _("Compra cancelada: esta operación no se completó. La cadena se "
                               "muestra solo como constancia.")
        return data

    def traceability_qr_uri(self):
        """Sin página pública (borrador, en verificación o retirada) el
        certificado no lleva un QR que no abre nada."""
        self.ensure_one()
        if not self.shrimp_trace_public_visible():
            return False
        return super().traceability_qr_uri()

    # ------------------------------------------------------------------
    # Retirar / renovar (portal de las partes, back-office y API)
    # ------------------------------------------------------------------
    def _shrimp_trace_check_party(self, actor=None):
        """Las partes de la compra (con su permiso de comprar / vender) o la
        plataforma."""
        self.ensure_one()
        user = self.env.user
        if user._is_internal() or user._is_superuser():
            return True
        actor = actor or user.shrimp_actor_partner_id
        lado = "seller" if actor == self.seller_partner_id else "buyer"
        return self._shrimp_check_party(lado, actor)

    def action_withdraw_public_trace(self, actor=None):
        self.ensure_one()
        self._shrimp_trace_check_party(actor)
        if not shrimp_trace_ready(self.env):
            raise ValidationError(_("Esta función todavía no está disponible."))
        if self._shrimp_trace_withdrawal():
            return True
        actor = actor or self.env.user.shrimp_actor_partner_id
        self.env["shrimp.trace.withdrawal"].sudo().create({
            "transaction_id": self.id, "withdrawn_by_id": self.env.user.id,
            "withdrawn_partner_id": actor.id if actor else False})
        self.sudo().message_post(body=_(
            "Se retiró la página pública de trazabilidad (QR) de la compra %(r)s (%(q)s). Los QR "
            "impresos ya no la abren.") % {"r": self.name,
                                           "q": actor.name if actor else self.env.user.name})
        return True

    def action_renew_public_trace(self, actor=None):
        """Token nuevo (los QR impresos dejan de funcionar) y la página se
        vuelve a publicar si estaba retirada."""
        self.ensure_one()
        self._shrimp_trace_check_party(actor)
        self._shrimp_trace_withdrawal().unlink()
        self.sudo().action_rotate_trace_token()
        actor = actor or self.env.user.shrimp_actor_partner_id
        self.sudo().message_post(body=_(
            "Se renovó el enlace público de trazabilidad (QR) de la compra %(r)s (%(q)s): los QR "
            "anteriores dejan de funcionar.") % {
            "r": self.name, "q": actor.name if actor else self.env.user.name})
        return True

    def action_rotate_trace_token(self):
        # La rotación (back-office y API) también vuelve a publicar una
        # página retirada: es la operación «renovar» de siempre.
        for rec in self:
            rec.sudo()._shrimp_trace_withdrawal().unlink()
        return super().action_rotate_trace_token()
