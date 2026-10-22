from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class ShrimpCopackCancellation(models.Model):
    """La planta quiere dejar un trabajo con el camarón del cliente ya dentro.

    Decisión D-34 de la auditoría de procesos: con el camarón recibido en
    planta, el maquilador NO cancela sin el consentimiento del cliente. Tiene
    dos caminos y los dos dejan papel:

    * **Acta de devolución** (``kind = return``): declara cuántas libras
      devuelve, cuándo y en qué condiciones; el cliente la firma (conforme) o
      la rechaza. Firmada, la orden se cancela y la solicitud se reabre a las
      demás plantas.
    * **Aprobación de la plataforma** (``kind = platform``): queda pendiente
      con una actividad para los administradores, que aprueban (orden
      cancelada, solicitud reabierta) o rechazan (el trabajo sigue).

    Mientras haya una pendiente, la orden no avanza (ni recepción ni empaque).
    """

    _name = "shrimp.copack.cancellation"
    _description = "Cancelación de una orden de empaque con el camarón en planta"
    _inherit = ["shrimp.uuid.mixin"]
    _order = "id desc"

    order_id = fields.Many2one(
        "shrimp.copack.order", string="Orden", required=True, ondelete="cascade", index=True)
    kind = fields.Selection(
        [("return", "Acta de devolución"), ("platform", "Aprobación de la plataforma")],
        string="Camino", required=True)
    state = fields.Selection(
        [("pending", "Pendiente"), ("approved", "Aprobada"),
         ("rejected", "Rechazada"), ("withdrawn", "Retirada")],
        string="Estado", default="pending", required=True, index=True)
    requested_by_partner_id = fields.Many2one(
        "res.partner", string="Pedida por", required=True, ondelete="restrict")
    requested_by_uid = fields.Many2one("res.users", string="Usuario", readonly=True)
    requested_at = fields.Datetime(string="Pedida el", readonly=True)
    reason = fields.Text(string="Motivo", required=True)
    returned_lb = fields.Float(string="Libras que se devuelven", digits=(16, 2))
    returned_date = fields.Date(string="Fecha de la devolución")
    return_notes = fields.Text(string="Condiciones de la devolución")
    decided_by_uid = fields.Many2one("res.users", string="Decidida por", readonly=True)
    decided_at = fields.Datetime(string="Decidida el", readonly=True)
    decision_note = fields.Text(string="Nota de la decisión", readonly=True)

    @api.model
    def _shrimp_request(self, order, kind, reason, actor=None, returned_lb=0.0, notes=None):
        order = order.sudo()
        actor = actor or self.env.user.shrimp_actor_partner_id
        if order.self_packing or actor != order.copacker_partner_id:
            raise AccessError(_("Esta solicitud la hace la planta que tiene el trabajo."))
        if order.state != "received":
            raise ValidationError(_(
                "El acta de devolución y la aprobación de la plataforma son para cuando el "
                "camarón ya está en tu planta y el empaque no se registró. Antes de recibirlo "
                "puedes cancelar directamente (con motivo)."))
        if order.pending_cancellation_id:
            raise ValidationError(_("Ya hay una solicitud de cancelación pendiente."))
        motivo = (reason or "").strip()
        if not motivo:
            raise ValidationError(_("Explica por qué no puedes hacer el trabajo."))
        vals = {
            "order_id": order.id, "kind": kind, "reason": motivo,
            "requested_by_partner_id": actor.id, "requested_by_uid": self.env.user.id,
            "requested_at": fields.Datetime.now(),
        }
        if kind == "return":
            try:
                libras = float(returned_lb or 0.0)
            except (TypeError, ValueError):
                libras = 0.0
            if libras <= 0:
                raise ValidationError(_("Indica cuántas libras devuelves al cliente."))
            if libras > (order.received_lb or 0.0) + 0.005:
                raise ValidationError(_(
                    "No se devuelven más libras de las recibidas (%s lb).")
                    % "{:,.2f}".format(order.received_lb or 0.0))
            vals.update({"returned_lb": libras,
                         "returned_date": fields.Date.context_today(self),
                         "return_notes": (notes or "").strip() or False})
        rec = self.sudo().create(vals)
        if kind == "return":
            order.message_post(body=_(
                "Acta de devolución registrada por la planta: devuelve %(lb)s lb. Motivo: %(m)s")
                % {"lb": "{:,.2f}".format(rec.returned_lb), "m": motivo})
            order._cpk_notify(
                order.client_partner_id, _("Firma el acta de devolución de %s") % order.name,
                _("%(maq)s no puede hacer el trabajo y propone devolverte %(lb)s lb. Motivo: "
                  "%(m)s. Firma el acta de devolución si estás conforme (la orden se cancela "
                  "y tu solicitud vuelve a salir a otras plantas) o recházala.") % {
                    "maq": order.copacker_partner_id.name or "",
                    "lb": "{:,.2f}".format(rec.returned_lb), "m": motivo})
        else:
            order.message_post(body=_(
                "La planta pide a la plataforma cancelar la orden. Motivo: %s") % motivo)
            order._cpk_notify(
                order.client_partner_id, _("La planta pide cancelar %s") % order.name,
                _("%(maq)s pidió a la plataforma cancelar la orden %(o)s, con tu camarón en su "
                  "planta. Motivo: %(m)s. La plataforma lo revisa; te avisaremos.") % {
                    "maq": order.copacker_partner_id.name or "", "o": order.name, "m": motivo})
            order._cpk_notify_platform(
                _("%(maq)s pide cancelar la orden de empaque %(o)s con el camarón de %(cli)s "
                  "en su planta. Motivo: %(m)s. Aprueba (la orden se cancela y la solicitud "
                  "se reabre) o rechaza desde la orden.") % {
                    "maq": order.copacker_partner_id.name or "", "o": order.name,
                    "cli": order.client_partner_id.name or "", "m": motivo},
                _("Cancelación de empaque por aprobar: %s") % order.name,
                _("Aprobar o rechazar la cancelación de %s") % order.name)
        return rec

    def _shrimp_decide(self, approve, note):
        """Aplica la decisión (firma del cliente o resolución de la plataforma)."""
        self.ensure_one()
        order = self.order_id.sudo()
        self.sudo().write({
            "state": "approved" if approve else "rejected",
            "decided_by_uid": self.env.user.id, "decided_at": fields.Datetime.now(),
            "decision_note": (note or "").strip() or False,
        })
        order._cpk_close_platform_activities(_("Cancelación resuelta."))
        if approve:
            if self.kind == "return":
                motivo = _("Acta de devolución firmada por el cliente (%(lb)s lb devueltas). "
                           "%(m)s") % {"lb": "{:,.2f}".format(self.returned_lb), "m": self.reason}
            else:
                motivo = _("Cancelación aprobada por la plataforma. %s") % self.reason
            order._shrimp_do_cancel("copacker" if self.kind == "return" else "platform",
                                    motivo, reopen=True)
        else:
            quien = _("El cliente") if self.kind == "return" else _("La plataforma")
            order.message_post(body=_("%(q)s rechazó la cancelación.%(n)s") % {
                "q": quien, "n": (" " + note) if note else ""})
            order._cpk_notify(
                order.copacker_partner_id, _("Cancelación rechazada: %s") % order.name,
                _("%(q)s rechazó la cancelación de la orden %(o)s.%(n)s El trabajo sigue: "
                  "registra el empaque%(alt)s.") % {
                    "q": quien, "o": order.name,
                    "n": (" " + _("Nota: %s") % note) if note else "",
                    "alt": (_(" o pide la aprobación de la plataforma")
                            if self.kind == "return" else "")})
            if self.kind == "platform":
                order._cpk_notify(order.client_partner_id,
                                  _("La orden %s sigue en curso") % order.name,
                                  _("La plataforma rechazó la cancelación que pidió la planta: "
                                    "el trabajo sigue."))
        return True

    def action_client_sign(self, accept, note=None, actor=None):
        """El cliente firma (acepta) o rechaza el acta de devolución."""
        self.ensure_one()
        # Por RPC del portal: el socio del propio usuario, cliente de la orden
        # y con permiso de comprar si es subusuario (_shrimp_decide va en sudo).
        actor = self.order_id._shrimp_copack_rpc_actor("client", actor)
        actor = actor or self.env.user.shrimp_actor_partner_id
        if actor != self.order_id.client_partner_id:
            raise AccessError(_("El acta de devolución la firma el cliente."))
        if self.kind != "return" or self.state != "pending":
            raise ValidationError(_("No hay un acta de devolución pendiente de tu firma."))
        return self._shrimp_decide(bool(accept), note)

    def action_platform_decide(self, approve, note=None):
        """La plataforma resuelve (también un acta de devolución que el
        cliente no contesta: mediación)."""
        self.ensure_one()
        self.order_id._shrimp_check_platform()
        if self.state != "pending":
            raise ValidationError(_("Esta solicitud de cancelación ya se resolvió."))
        if not (note or "").strip():
            raise ValidationError(_("Explica la decisión: es lo que leen las dos partes."))
        return self._shrimp_decide(bool(approve), note)

    def action_withdraw(self, actor=None):
        self.ensure_one()
        # Por RPC del portal: la planta de la orden y, si es subusuario, con
        # permiso de vender (lo que sigue va en sudo).
        actor = self.order_id._shrimp_copack_rpc_actor("copacker", actor)
        actor = actor or self.env.user.shrimp_actor_partner_id
        if actor != self.requested_by_partner_id:
            raise AccessError(_("La retira quien la pidió."))
        if self.state != "pending":
            raise ValidationError(_("Solo se retira una solicitud pendiente."))
        self.sudo().state = "withdrawn"
        self.order_id.sudo()._cpk_close_platform_activities(_("Solicitud retirada."))
        self.order_id.sudo().message_post(body=_("La planta retiró su solicitud de cancelación."))
        return True
