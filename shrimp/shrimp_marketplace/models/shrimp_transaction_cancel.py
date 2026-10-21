"""Cancelación general de una compra (hueco #5 de la auditoría de procesos).

UN SOLO SERVICIO: ``shrimp.transaction.shrimp_cancel(actor_kind, ...)``.
Reutiliza lo que ya hacía la cancelación sin costo por retraso
(models/shrimp_transaction_delay.py): devolución del stock al lote del
vendedor con un movimiento «Ajuste · Entra al lote» que apunta a la venta, y
anulación de los cobros de la plataforma (shrimp.charge.action_cancel_charge:
sin factura se anula; facturada, nota de crédito). Nunca se factura la
mercadería: la plataforma solo anula SUS servicios (facturación en dos pasos).

QUIÉN PUEDE CANCELAR Y CUÁNDO
  comprador   sin costo, solo en los casos previstos:
                · retraso (contrapropuesta rechazada, tope superado, atraso
                  sin aviso) y «No entregada» (venció el plazo de recepción
                  sin despacho): shrimp_transaction_delay.py;
                · verificación vencida (la verificadora no asignó técnico o
                  no emitió el informe a tiempo): shrimp_verification.
              Strike al vendedor solo en los casos de retraso / no entrega.
  vendedor    antes del despacho (compra directa) o antes de que la
              verificadora vaya a campo (verificada); con strike
              «Compra cancelada por el vendedor».
  mutuo      una parte lo pide, la otra acepta dentro del plazo de respuesta
              (Ajustes: horas para responder un retraso, 48); si no responde,
              la solicitud caduca. Sin strike.
  plataforma  back-office (Administrador), motivo obligatorio, cualquier
              estado no completado; el administrador indica si fue culpa del
              vendedor (strike «Cancelada por la plataforma»).
  sistema     cron: compra «No entregada» sin respuesta del comprador en N
              días (Ajustes) ─▶ cancelada sin costo, strike al vendedor.

EFECTOS (según el estado de la compra)
  confirmed                 stock de vuelta al vendedor + cobros anulados;
  pending_verification /    nada que devolver (el stock solo estaba
  pending_acceptance        reservado): se libera la reserva, la verificación
                            se cancela (shrimp_verification) y su honorario
                            se anula si no se llegó a hacer;
  draft                     solo se cancela.
  En todos: avisos de retraso abiertos y solicitudes de mutuo acuerdo se
  cierran, queda un registro «shrimp.transaction.cancellation» (quién, por
  qué, qué se devolvió y anuló) y se avisa a las dos partes.

INCIDENCIA (plataforma, ficha interna de la compra)
  «Dar por recibida» (ya existía) · «No se entregó: cancelar» (este servicio,
  con strike) · «Entrega parcial» (se recibe lo entregado; lo no entregado
  vuelve al lote del vendedor y la comisión se recalcula) · «Reabrir plazo».
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import sql
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

CANCEL_KINDS = [
    ("buyer", "Comprador"),
    ("seller", "Vendedor"),
    ("mutual", "Mutuo acuerdo"),
    ("platform", "Plataforma"),
    ("system", "Automática (plataforma)"),
]
CANCEL_REQUEST_STATES = [
    ("requested", "Solicitada (espera a la otra parte)"),
    ("rejected", "Rechazada"),
    ("expired", "Caducada"),
    ("withdrawn", "Retirada"),
    ("done", "Compra cancelada"),
]
GENERAL_CAUSES = [
    ("delay", "Retraso de entrega"),
    ("not_delivered", "No se entregó"),
    ("verification_overdue", "Verificación vencida"),
    # shrimp_verification (D-41): el informe declarado no se presentó a tiempo.
    ("declared_overdue", "Verificación declarada no presentada a tiempo"),
    ("seller_withdrawal", "El vendedor no puede cumplir"),
    ("mutual", "Mutuo acuerdo"),
    ("platform", "Decisión de la plataforma"),
    # Lote bloqueado por PCR positivo (D-30): el comprador cancela sin costo
    # y sin strike al vendedor (models/shrimp_product_pcr.py).
    ("pcr_positive", "PCR positivo"),
]
_CANCEL_TABLE = "shrimp_transaction_cancellation"


def shrimp_cancel_ready(env):
    """True si la tabla de cancelaciones ya existe (el -u ya corrió): el
    servicio vivo puede cargar este código antes de actualizar el módulo."""
    reg = env.registry
    if getattr(reg, "_shrimp_cancel_ready", False):
        return True
    listo = sql.table_exists(env.cr, _CANCEL_TABLE)
    if listo:
        reg._shrimp_cancel_ready = True
    return listo


class ShrimpTransactionCancellation(models.Model):
    _name = "shrimp.transaction.cancellation"
    _description = "Cancelación de una compra (registro y solicitudes de mutuo acuerdo)"
    _order = "create_date desc, id desc"

    name = fields.Char(string="Referencia", compute="_compute_name")
    transaction_id = fields.Many2one("shrimp.transaction", string="Compra", required=True,
                                     ondelete="cascade", index=True)
    seller_partner_id = fields.Many2one(related="transaction_id.seller_partner_id", store=True,
                                        string="Vendedor", index=True)
    buyer_partner_id = fields.Many2one(related="transaction_id.buyer_partner_id", store=True,
                                       string="Comprador", index=True)
    kind = fields.Selection(CANCEL_KINDS, string="Quién cancela", required=True, index=True)
    cause = fields.Selection(GENERAL_CAUSES, string="Causa", index=True)
    state = fields.Selection(CANCEL_REQUEST_STATES, string="Estado", required=True,
                             default="done", index=True)
    reason = fields.Text(string="Motivo")
    response_note = fields.Text(string="Respuesta")
    from_state = fields.Char(string="Estado de la compra al cancelar", readonly=True)
    requested_side = fields.Selection([("buyer", "Comprador"), ("seller", "Vendedor")],
                                      string="Pidió")
    requested_by_id = fields.Many2one("res.users", string="Pedida por", readonly=True)
    requested_at = fields.Datetime(string="Pedida el", readonly=True)
    response_deadline = fields.Datetime(string="Responder hasta", readonly=True)
    responded_by_id = fields.Many2one("res.users", string="Respondió", readonly=True)
    responded_at = fields.Datetime(string="Respondida el", readonly=True)
    done_at = fields.Datetime(string="Cancelada el", readonly=True)
    done_by_id = fields.Many2one("res.users", string="Canceló", readonly=True)
    seller_fault = fields.Boolean(string="Por culpa del vendedor", readonly=True)
    strike_id = fields.Many2one("shrimp.seller.strike", string="Strike", readonly=True,
                                ondelete="set null")
    stock_returned_qty = fields.Float(string="Cantidad devuelta al vendedor", readonly=True)
    charges_voided = fields.Integer(string="Cobros anulados", readonly=True)

    @api.depends("transaction_id.name", "kind", "state")
    def _compute_name(self):
        etiquetas = dict(CANCEL_KINDS)
        for rec in self:
            pref = _("Solicitud de cancelación") if rec.state != "done" else _("Cancelación")
            rec.name = "%s · %s · %s" % (pref, etiquetas.get(rec.kind, ""),
                                         rec.transaction_id.name or "")

    def shrimp_kind_label(self):
        self.ensure_one()
        return dict(CANCEL_KINDS).get(self.kind, "")

    def shrimp_cause_label(self):
        self.ensure_one()
        return dict(GENERAL_CAUSES).get(self.cause, "")

    def shrimp_state_label(self):
        self.ensure_one()
        return dict(CANCEL_REQUEST_STATES).get(self.state, "")


class ShrimpTransactionCancel(models.Model):
    _inherit = "shrimp.transaction"

    # One2many: sin columna en la tabla de la compra.
    cancellation_ids = fields.One2many("shrimp.transaction.cancellation", "transaction_id",
                                       string="Cancelación")

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def shrimp_cancellation(self):
        """La cancelación efectiva (registro «done»), si la hay."""
        self.ensure_one()
        if not self.id or not shrimp_cancel_ready(self.env):
            return self.env["shrimp.transaction.cancellation"]
        return self.sudo().cancellation_ids.filtered(lambda c: c.state == "done")[:1]

    def shrimp_cancel_request_open(self):
        """La solicitud de mutuo acuerdo que espera respuesta."""
        self.ensure_one()
        if not self.id or not shrimp_cancel_ready(self.env):
            return self.env["shrimp.transaction.cancellation"]
        return self.sudo().cancellation_ids.filtered(lambda c: c.state == "requested")[:1]

    def shrimp_cancel_label(self):
        """Texto corto de quién canceló (portal)."""
        self.ensure_one()
        reg = self.shrimp_cancellation()
        if not reg:
            return _("Compra cancelada")
        return {
            "buyer": _("Compra cancelada sin costo por el comprador"),
            "seller": _("Cancelada por el vendedor"),
            "mutual": _("Cancelada de mutuo acuerdo"),
            "platform": _("Cancelada por la plataforma"),
            "system": _("Cancelada automáticamente"),
        }.get(reg.kind, _("Compra cancelada"))

    @api.model
    def _shrimp_cancel_response_hours(self):
        return self._shrimp_delay_response_hours()

    def _shrimp_cancellable_state(self):
        self.ensure_one()
        return self.state not in ("done", "cancel")

    # ---- comprador ----------------------------------------------------
    def shrimp_buyer_cancel_cause(self):
        """Por qué el comprador puede cancelar sin costo ahora (o False):
        las causas de retraso / no entrega (delay) y las que agregan otros
        módulos (verificación vencida)."""
        self.ensure_one()
        if self.state == "confirmed":
            return self.shrimp_delay_cancel_cause() or False
        return False

    # ---- vendedor -----------------------------------------------------
    def shrimp_seller_cancel_problem(self):
        """'' si el vendedor puede cancelar ahora; si no, por qué."""
        self.ensure_one()
        if self.state == "confirmed":
            if not self._shrimp_is_direct_delivery():
                return _("Esta compra verificada solo la puede cancelar la plataforma o las dos "
                         "partes de mutuo acuerdo.")
            if self.dispatched_at:
                return _("La compra ya está despachada: el vendedor ya no puede cancelarla.")
            if self.reception_status == "incidence":
                return _("La compra tiene una incidencia en revisión: la resuelve la plataforma.")
            return ""
        return _("En este estado el vendedor no puede cancelar la compra.")

    def shrimp_seller_can_cancel(self):
        self.ensure_one()
        return self._shrimp_cancellable_state() and not self.shrimp_seller_cancel_problem()

    # ---- mutuo acuerdo -----------------------------------------------
    def shrimp_mutual_cancel_problem(self):
        self.ensure_one()
        if not shrimp_cancel_ready(self.env):
            return _("La cancelación de mutuo acuerdo todavía no está disponible.")
        if not self._shrimp_cancellable_state():
            return _("La compra ya está cerrada.")
        if self.state == "confirmed" and self.reception_status == "incidence":
            return _("La compra tiene una incidencia en revisión: la resuelve la plataforma.")
        if self.shrimp_cancel_request_open():
            return _("Ya hay una solicitud de cancelación esperando respuesta.")
        return ""

    # ------------------------------------------------------------------
    # Permisos
    # ------------------------------------------------------------------
    def _shrimp_check_platform_admin(self):
        user = self.env.user
        if not (self.env.su or user._is_superuser()
                or user.has_group("shrimp_marketplace.group_shrimp_manager")):
            raise AccessError(_("Solo la plataforma (Administrador de CamaronMarket) puede hacer "
                                "esto."))

    def _shrimp_side_of(self, actor):
        self.ensure_one()
        if actor and actor == self.seller_partner_id:
            return "seller"
        if actor and actor == self.buyer_partner_id:
            return "buyer"
        raise AccessError(_("No participas en esta compra."))

    # ------------------------------------------------------------------
    # Punto único
    # ------------------------------------------------------------------
    def shrimp_cancel(self, actor_kind, reason=None, actor=None, cause=None, seller_fault=None):
        """Cancela la compra. `actor_kind`: buyer, seller, platform o system
        (el mutuo acuerdo pasa por action_cancel_request / _respond).
        Comprueba quién y cuándo, aplica los efectos y devuelve el registro
        de la cancelación."""
        self.ensure_one()
        if not self._shrimp_cancellable_state():
            raise ValidationError(_("La compra %s ya está cerrada: no se puede cancelar.") % self.name)
        motivo_txt = (reason or "").strip()
        if actor_kind == "buyer":
            self._shrimp_check_party("buyer", actor)
            causa = self.shrimp_buyer_cancel_cause()
            if not causa:
                raise ValidationError(_(
                    "No puedes cancelar esta compra ahora: el comprador cancela sin costo solo por "
                    "retraso, por falta de entrega o si la verificación venció."))
            return self._shrimp_buyer_cancel(causa, motivo_txt)
        if actor_kind == "seller":
            self._shrimp_check_party("seller", actor)
            problema = self.shrimp_seller_cancel_problem()
            if problema:
                raise ValidationError(problema)
            if not motivo_txt:
                raise ValidationError(_("Explica al comprador por qué cancelas la venta."))
            return self._shrimp_cancel_apply(
                "seller", "seller_withdrawal", motivo_txt, seller_fault=True,
                strike_reason="seller_cancel")
        if actor_kind == "platform":
            self._shrimp_check_platform_admin()
            if not motivo_txt:
                raise ValidationError(_("La cancelación por la plataforma exige un motivo."))
            return self._shrimp_cancel_apply(
                "platform", cause or "platform", motivo_txt, seller_fault=bool(seller_fault),
                strike_reason="platform_cancel" if seller_fault else None)
        if actor_kind == "system":
            if not self.env.su:
                raise AccessError(_("Solo la plataforma cancela automáticamente."))
            return self._shrimp_cancel_apply(
                "system", cause or "platform", motivo_txt, seller_fault=bool(seller_fault),
                strike_reason="platform_cancel" if seller_fault else None)
        raise ValidationError(_("Tipo de cancelación desconocido: %s") % actor_kind)

    def _shrimp_buyer_cancel(self, causa, nota):
        """Comprador: las causas de retraso / no entrega usan la cancelación
        por retraso (strike incluido); otros módulos agregan las suyas."""
        self.ensure_one()
        if causa in dict(self.env["shrimp.transaction.delay"]._fields["cancel_cause"].selection):
            self._shrimp_cancel_for_delay(causa, nota)
            return self.shrimp_cancellation()
        raise ValidationError(_("Causa de cancelación no admitida: %s") % causa)

    # ------------------------------------------------------------------
    # Efectos
    # ------------------------------------------------------------------
    def _shrimp_cancel_text(self, kind, cause):
        etiqueta = dict(GENERAL_CAUSES).get(cause, cause or "")
        quien = dict(CANCEL_KINDS).get(kind, kind)
        return _("Cancelación de la compra %(r)s (%(q)s: %(c)s)") % {
            "r": self.name, "q": quien.lower(), "c": etiqueta.lower()}

    def _shrimp_cancel_release_hold(self, motivo, kind):
        """Compra que solo reservaba stock (verificación): la extiende
        shrimp_verification. Aquí, nada que devolver."""
        self.ensure_one()
        return True

    def _shrimp_cancel_apply(self, kind, cause, reason, seller_fault=False, strike_reason=None,
                             request=None, notify=True):
        """Aplica la cancelación (stock, cobros, estado, strike, registro y
        avisos). No comprueba permisos: lo hace quien llama."""
        self.ensure_one()
        tx = self.sudo()
        if not tx._shrimp_cancellable_state():
            raise ValidationError(_("La compra %s ya está cerrada.") % tx.name)
        estado_previo = tx.state
        motivo = tx._shrimp_cancel_text(kind, cause)
        devueltos = self.env["shrimp.stock.move"]
        if estado_previo == "confirmed":
            devueltos = tx._shrimp_return_stock_to_seller(motivo)
        elif estado_previo != "draft":
            tx._shrimp_cancel_release_hold(motivo, kind)
        anulados = tx._shrimp_void_charges_for_cancel(motivo)
        if tx.state != "cancel":
            tx.write({"state": "cancel", "reception_status": False})
        tx.product_id.sudo()._compute_available_qty()
        abiertos = tx.delay_ids.filtered(lambda d: d.state in ("proposed", "countered"))
        if abiertos:
            abiertos.write({"state": "cancelled", "cancelled_at": fields.Datetime.now(),
                            "cancelled_by_id": self.env.user.id})
        strike = self.env["shrimp.seller.strike"]
        ya_tenia = False
        if strike_reason and "shrimp.seller.strike" in self.env:
            ya_tenia = bool(strike.sudo().search_count([("transaction_id", "=", tx.id)]))
            strike = self.env["shrimp.seller.strike"].sudo()._shrimp_add_strike(tx, strike_reason)
        Cancel = self.env["shrimp.transaction.cancellation"].sudo()
        vals = {
            "state": "done", "kind": kind, "cause": cause, "from_state": estado_previo,
            "done_at": fields.Datetime.now(), "done_by_id": self.env.user.id,
            "seller_fault": bool(seller_fault), "strike_id": strike.id or False,
            "stock_returned_qty": sum(devueltos.mapped("qty")), "charges_voided": len(anulados),
        }
        if request:
            request.write(vals)
            registro = request
        else:
            registro = Cancel.create(dict(vals, transaction_id=tx.id, reason=reason or False))
        # Cualquier otra solicitud abierta queda sin efecto.
        tx.cancellation_ids.filtered(lambda c: c.state == "requested" and c != registro).write(
            {"state": "withdrawn"})
        resumen = []
        if devueltos:
            resumen.append(_("Se devolvieron %s al lote del vendedor.") % tx.shrimp_fmt_qty(
                sum(devueltos.mapped("qty")), tx.product_id.uom_id))
        elif estado_previo in ("pending_verification", "pending_acceptance"):
            resumen.append(_("Se liberó la reserva de stock."))
        resumen.append(_("Cobros de la plataforma anulados: %s.") % len(anulados))
        if strike:
            resumen.append(_("La compra ya tenía un strike del vendedor: no suma otro (máximo uno "
                             "por compra).") if ya_tenia else _("Strike al vendedor."))
        tx.message_post(body=Markup("<p>%s</p><p>%s</p>%s") % (
            motivo, " ".join(resumen),
            (Markup("<p>%s</p>") % (_("Motivo: %s") % reason)) if reason else ""))
        if notify:
            tx._shrimp_cancel_notify(registro)
        return registro

    def _shrimp_cancel_notify(self, registro):
        self.ensure_one()
        motivo = (" " + _("Motivo: %s") % registro.reason) if registro.reason else ""
        dinero = _("La plataforma anuló sus cobros de esta compra (si ya estaban facturados, emite "
                   "la nota de crédito). Si el comprador ya pagó la mercadería al vendedor, el "
                   "vendedor debe devolverle ese valor.")
        k = registro.kind
        if k == "seller":
            self._shrimp_notify("buyer", _("El vendedor canceló la compra %s") % self.name, _(
                "El vendedor canceló tu compra %(r)s.%(m)s No tiene costo para ti.") % {
                "r": self.name, "m": motivo} + " " + dinero)
            self._shrimp_notify("seller", _("Venta %s cancelada") % self.name, _(
                "Cancelaste la venta %(r)s. El producto volvió a tu lote y la comisión se anuló. "
                "La cancelación suma un strike a tu cuenta.") % {"r": self.name})
        elif k == "mutual":
            for side in ("buyer", "seller"):
                self._shrimp_notify(side, _("Compra %s cancelada de mutuo acuerdo") % self.name, _(
                    "Comprador y vendedor acordaron cancelar la compra %(r)s.%(m)s") % {
                    "r": self.name, "m": motivo} + " " + dinero)
        else:
            quien = _("La plataforma") if k == "platform" else _("La plataforma (automáticamente)")
            texto = _("%(q)s canceló la compra %(r)s.%(m)s") % {"q": quien, "r": self.name,
                                                                 "m": motivo}
            if registro.strike_id:
                texto_v = texto + " " + _("La cancelación suma un strike a tu cuenta.")
            else:
                texto_v = texto
            self._shrimp_notify("buyer", _("Compra %s cancelada") % self.name,
                                texto + " " + _("No tiene costo para ti.") + " " + dinero)
            self._shrimp_notify("seller", _("Venta %s cancelada") % self.name, texto_v + " " + dinero)

    # ------------------------------------------------------------------
    # Mutuo acuerdo
    # ------------------------------------------------------------------
    def action_cancel_request(self, reason, actor=None):
        """Una parte pide cancelar de mutuo acuerdo."""
        self.ensure_one()
        user = self.env.user
        actor = actor or user.shrimp_actor_partner_id
        lado = self._shrimp_side_of(actor)
        self._shrimp_check_party(lado, actor)
        problema = self.shrimp_mutual_cancel_problem()
        if problema:
            raise ValidationError(problema)
        motivo = (reason or "").strip()
        if not motivo:
            raise ValidationError(_("Explica por qué quieres cancelar la compra."))
        ahora = fields.Datetime.now()
        plazo = ahora + timedelta(hours=self._shrimp_cancel_response_hours())
        req = self.env["shrimp.transaction.cancellation"].sudo().create({
            "transaction_id": self.id, "kind": "mutual", "cause": "mutual", "state": "requested",
            "reason": motivo, "requested_side": lado, "requested_by_id": user.id,
            "requested_at": ahora, "response_deadline": plazo,
        })
        otro = "seller" if lado == "buyer" else "buyer"
        self._shrimp_notify(otro, _("Piden cancelar de mutuo acuerdo la compra %s") % self.name, _(
            "El %(q)s pide cancelar de mutuo acuerdo la compra %(r)s. Motivo: %(m)s. Tienes hasta "
            "el %(l)s para aceptar o rechazar; si no respondes, la solicitud caduca y la compra "
            "sigue.") % {"q": _("comprador") if lado == "buyer" else _("vendedor"), "r": self.name,
                         "m": motivo, "l": self.shrimp_fmt_dt(plazo)})
        return req

    def action_cancel_request_respond(self, accept, note=None, actor=None):
        """La otra parte acepta (se cancela, sin strike) o rechaza."""
        self.ensure_one()
        req = self.shrimp_cancel_request_open()
        if not req:
            raise ValidationError(_("No hay una solicitud de cancelación esperando respuesta."))
        actor = actor or self.env.user.shrimp_actor_partner_id
        lado = self._shrimp_side_of(actor)
        if lado == req.requested_side:
            raise ValidationError(_("La solicitud la responde la otra parte."))
        self._shrimp_check_party(lado, actor)
        req.write({"responded_by_id": self.env.user.id, "responded_at": fields.Datetime.now(),
                   "response_note": (note or "").strip() or False})
        if accept:
            return self._shrimp_cancel_apply("mutual", "mutual", req.reason, request=req)
        req.write({"state": "rejected"})
        self._shrimp_notify(req.requested_side, _("Solicitud de cancelación rechazada: %s") % self.name, _(
            "La otra parte no aceptó cancelar la compra %s: la compra sigue.") % self.name)
        return req

    def action_cancel_request_withdraw(self, actor=None):
        self.ensure_one()
        req = self.shrimp_cancel_request_open()
        actor = actor or self.env.user.shrimp_actor_partner_id
        if not req or self._shrimp_side_of(actor) != req.requested_side:
            raise ValidationError(_("No tienes una solicitud de cancelación abierta."))
        self._shrimp_check_party(req.requested_side, actor)
        req.write({"state": "withdrawn"})
        return req

    # ------------------------------------------------------------------
    # Retraso (comprador) ─▶ registro de la cancelación
    # ------------------------------------------------------------------
    def _shrimp_cancel_for_delay(self, causa, note=None):
        registro = super()._shrimp_cancel_for_delay(causa, note)
        if shrimp_cancel_ready(self.env):
            tx = self.sudo()
            # D-42: un strike por compra; puede ser el de un atraso previo.
            strike = self.env["shrimp.seller.strike"].sudo().search(
                [("transaction_id", "=", tx.id)], order="id asc", limit=1)
            self.env["shrimp.transaction.cancellation"].sudo().create({
                "transaction_id": tx.id, "kind": "buyer",
                "cause": "not_delivered" if causa == "not_delivered" else "delay",
                "state": "done", "from_state": "confirmed", "reason": (note or "").strip() or False,
                "done_at": fields.Datetime.now(), "done_by_id": self.env.user.id,
                "seller_fault": True, "strike_id": strike.id or False,
                "stock_returned_qty": sum(tx.stock_move_ids.filtered(
                    lambda m: m.move_type == "adjustment" and m.direction == "in").mapped("qty")),
                "charges_voided": len(tx.charge_ids.filtered(
                    lambda c: c.state in ("cancelled", "to_credit", "credited"))),
            })
            tx.cancellation_ids.filtered(lambda c: c.state == "requested").write(
                {"state": "withdrawn"})
        return registro

    def _shrimp_cancel_not_delivered_auto(self):
        """Cron: «No entregada» sin respuesta del comprador en N días."""
        self.ensure_one()
        return self.sudo()._shrimp_cancel_apply(
            "system", "not_delivered",
            _("Venció el plazo de recepción sin despacho y el comprador no confirmó la recepción "
              "en %s días: se cancela sin costo para el comprador.")
            % self._shrimp_not_delivered_cancel_days(),
            seller_fault=True, strike_reason="platform_cancel")

    # ------------------------------------------------------------------
    # Incidencia (plataforma)
    # ------------------------------------------------------------------
    def action_incidence_not_delivered(self, reason=None):
        """Plataforma: «No se entregó → cancelar» (con strike al vendedor)."""
        self._shrimp_check_platform_admin()
        for rec in self:
            if rec.state != "confirmed":
                raise ValidationError(_("Solo se resuelve la incidencia de una compra confirmada."))
            nota = (reason or rec.incidence_resolution or "").strip() or _(
                "Incidencia resuelta por la plataforma: la mercadería no se entregó.")
            rec.sudo().write({"incidence_resolution": nota})
            rec.shrimp_cancel("platform", reason=nota, cause="not_delivered", seller_fault=True)
        return True

    def action_incidence_partial(self, delivered_qty, reason=None):
        """Plataforma: entrega parcial. El comprador recibe `delivered_qty`;
        lo no entregado vuelve al lote del vendedor y la comisión se
        recalcula sobre lo entregado (la anterior se anula: nota de crédito
        si ya estaba facturada)."""
        self._shrimp_check_platform_admin()
        self.ensure_one()
        tx = self.sudo()
        if tx.state != "confirmed" or not tx._shrimp_is_direct_delivery():
            raise ValidationError(_("La entrega parcial se registra en una compra directa "
                                    "confirmada."))
        total = tx.transaction_qty or 0.0
        entregado = float(delivered_qty or 0.0)
        if float_compare(entregado, 0.0, precision_digits=6) <= 0 \
                or float_compare(entregado, total, precision_digits=6) >= 0:
            raise ValidationError(_("La cantidad entregada tiene que ser mayor que 0 y menor que "
                                    "la comprada (%s).") % tx.shrimp_fmt_qty(total, tx.product_id.uom_id))
        faltante = total - entregado
        nota = (reason or "").strip() or _("Entrega parcial")
        uom = tx.product_id.uom_id
        motivo = _("Entrega parcial de %(r)s: llegaron %(e)s de %(t)s") % {
            "r": tx.name, "e": tx.shrimp_fmt_qty(entregado, uom), "t": tx.shrimp_fmt_qty(total, uom)}
        tx.write({"incidence_resolution": "%s. %s" % (motivo, nota)})
        ventas = tx.stock_move_ids.filtered(
            lambda m: (m.move_type or "transfer") == "transfer"
            and m.source_partner_id == tx.seller_partner_id)
        tx.with_context(shrimp_reception_skip_date=True, shrimp_reception_quiet=True).action_receive()
        # 1) Lo no entregado sale del lote del comprador...
        lotes = self.env["shrimp.stock.lot"].sudo().search([
            ("origin_move_id", "in", ventas.ids), ("owner_id", "=", tx.buyer_partner_id.id)],
            order="id desc")
        resta = faltante
        for lote in lotes:
            q = min(lote.available_qty, resta)
            if q > 0:
                lote._shrimp_internal_move("adjustment", q, motivo, direction="out",
                                           transaction_id=tx.id)
                resta -= q
            if resta <= 1e-6:
                break
        # 2) ... y vuelve al lote del vendedor (apuntando a la venta).
        resta = faltante
        for venta in ventas.sorted("id", reverse=True):
            q = min(venta.qty, resta)
            lote = venta.lot_id
            if q > 0 and lote and lote.owner_id == tx.seller_partner_id:
                lote._shrimp_internal_move("adjustment", q, motivo, direction="in",
                                           transaction_id=tx.id, parent_move_id=venta.id)
                resta -= q
            if resta <= 1e-6:
                break
        tx.product_id.sudo()._compute_available_qty()
        tx.product_id.sudo()._update_state_from_stock()
        # 3) Comisión sobre lo entregado.
        comisiones = tx.charge_ids.filtered(
            lambda c: c.charge_type == "commission"
            and c.state not in ("cancelled", "credited", "to_credit"))
        if comisiones:
            anterior = comisiones[:1]
            comisiones.action_cancel_charge(motivo)
            Charge = self.env["shrimp.charge"].sudo()
            nueva = Charge._register_commission(tx, qty=entregado)
            if not nueva or nueva == anterior:
                # La anterior sigue «por acreditar» (nota de crédito pendiente):
                # la nueva se registra igual, por lo entregado.
                tasa = anterior.rate_cents or 0.0
                Charge._register_charge({
                    "charge_type": "commission", "transaction_id": tx.id,
                    "seller_partner_id": tx.seller_partner_id.id,
                    "buyer_partner_id": tx.buyer_partner_id.id,
                    "payer_partner_id": tx.seller_partner_id.id,
                    "product_id": tx.product_id.id, "qty": entregado,
                    "uom_id": tx.product_id.uom_id.id, "rate_cents": tasa,
                    # Una línea de 1 × importe redondeado (ver shrimp.charge).
                    "invoice_qty": 1.0,
                    "amount": round(entregado * tasa / 100.0, 2), "origin": tx.name,
                    "description": anterior.description or _("Comisión marketplace"),
                })
        tx.message_post(body=Markup("<p>%s</p><p>%s</p>") % (motivo, nota))
        texto = _("La plataforma resolvió la incidencia de la compra %(r)s como entrega parcial: "
                  "llegaron %(e)s de %(t)s. %(n)s") % {
            "r": tx.name, "e": tx.shrimp_fmt_qty(entregado, uom), "t": tx.shrimp_fmt_qty(total, uom),
            "n": nota}
        tx._shrimp_notify("buyer", _("Incidencia de %s resuelta: entrega parcial") % tx.name,
                          texto + " " + _("Lo entregado ya está en tu inventario."))
        tx._shrimp_notify("seller", _("Venta %s: entrega parcial") % tx.name,
                          texto + " " + _("Lo no entregado volvió a tu lote y la comisión se "
                                          "recalculó sobre lo entregado. Factura solo lo "
                                          "entregado."))
        return True

    # ------------------------------------------------------------------
    # Back-office: asistentes
    # ------------------------------------------------------------------
    def action_open_cancel_wizard(self):
        self.ensure_one()
        self._shrimp_check_platform_admin()
        ctx = {"default_transaction_id": self.id}
        if self.env.context.get("shrimp_cancel_mode"):
            ctx["default_mode"] = self.env.context["shrimp_cancel_mode"]
        return {
            "type": "ir.actions.act_window", "res_model": "shrimp.transaction.cancel.wizard",
            "view_mode": "form", "target": "new", "context": ctx,
            "name": _("Cancelar compra %s") % self.name,
        }

    # ------------------------------------------------------------------
    # Cron: solicitudes de mutuo acuerdo sin respuesta
    # ------------------------------------------------------------------
    @api.model
    def _cron_reception_and_delivery(self, limit=500):
        if shrimp_cancel_ready(self.env):
            try:
                self._shrimp_cron_cancel_requests(limit)
            except Exception:  # noqa: BLE001
                _logger.exception("Solicitudes de cancelación: fallo del cron")
        return super()._cron_reception_and_delivery(limit=limit)

    @api.model
    def _shrimp_cron_cancel_requests(self, limit=500):
        vencidas = self.env["shrimp.transaction.cancellation"].sudo().search([
            ("state", "=", "requested"), ("response_deadline", "<=", fields.Datetime.now()),
        ], limit=limit)
        for req in vencidas:
            req.write({"state": "expired"})
            tx = req.transaction_id
            tx._shrimp_notify(req.requested_side, _("Solicitud de cancelación caducada: %s") % tx.name, _(
                "La otra parte no respondió a tiempo tu solicitud de cancelar la compra %s: "
                "la solicitud caducó y la compra sigue.") % tx.name)
        return vencidas


class ShrimpTransactionCancelWizard(models.TransientModel):
    _name = "shrimp.transaction.cancel.wizard"
    _description = "Cancelar una compra (plataforma)"

    transaction_id = fields.Many2one("shrimp.transaction", string="Compra", required=True,
                                     readonly=True)
    mode = fields.Selection([
        ("cancel", "Cancelar la compra"),
        ("not_delivered", "Incidencia: no se entregó → cancelar"),
        ("partial", "Incidencia: entrega parcial"),
    ], string="Acción", required=True, default="cancel")
    state = fields.Selection(related="transaction_id.state", string="Estado de la compra")
    transaction_qty = fields.Float(related="transaction_id.transaction_qty",
                                   string="Cantidad comprada")
    delivered_qty = fields.Float(string="Cantidad entregada")
    reason = fields.Text(string="Motivo", required=True,
                         help="Obligatorio: queda en la compra y se les envía a las partes.")
    seller_fault = fields.Boolean(
        string="Por culpa del vendedor (strike)",
        help="Marca si la cancelación se debe a un incumplimiento del vendedor: suma un strike.")

    @api.onchange("mode")
    def _onchange_mode(self):
        if self.mode == "not_delivered":
            self.seller_fault = True

    def action_confirm(self):
        self.ensure_one()
        tx = self.transaction_id
        tx._shrimp_check_platform_admin()
        if self.mode == "partial":
            tx.action_incidence_partial(self.delivered_qty, self.reason)
        elif self.mode == "not_delivered":
            tx.action_incidence_not_delivered(self.reason)
        else:
            tx.shrimp_cancel("platform", reason=self.reason, seller_fault=self.seller_fault)
        return {"type": "ir.actions.act_window_close"}
