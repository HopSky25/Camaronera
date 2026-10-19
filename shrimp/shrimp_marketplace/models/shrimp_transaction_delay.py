"""«Notificar retraso de entrega»: aviso del vendedor, negociación de una
ronda con el comprador y cancelación sin costo.

FLUJO
-----
Solo en las compras con confirmación de recepción (matriz, columna
«Confirmación de recepción»: naupliero → laboratorio, laboratorio →
camaronera y las del mismo nivel), confirmadas, sin despachar y sin
incidencia; desde la confirmación hasta N días (gracia, 2) después de la
fecha de entrega acordada:

  vendedor: «Notificar retraso» (fecha nueva, motivo, nota)
      │   como mucho 2 avisos por compra; la fecha nueva no pasa de la
      │   fecha ORIGINAL + 15 días (tope total)
      ▼
  comprador (48 h):  «Aceptar nueva fecha» ─────────────▶ fecha cambiada
                     no responde ───────────────────────▶ aceptación tácita
                     «Proponer otra fecha» (una vez, dentro del tope)
                         ▼
                     vendedor (48 h): acepta ───────────▶ fecha = la del comprador
                                      rechaza / no responde ▶ «rechazada»: el
                                      comprador puede «Cancelar compra» sin costo

El comprador también puede cancelar sin costo, en cualquier momento antes del
despacho, cuando el atraso total pasa del tope (fecha original + 15 días) o
cuando pasó la fecha acordada + la gracia sin despacho ni aviso.

Si el vendedor no responde la contrapropuesta en 48 h se trata como RECHAZADA
(no como aceptada): el comprador propuso para protegerse y quien tiene que
cumplir es el vendedor; quedarse callado no puede obligar al comprador a
seguir esperando. Se avisa a las dos partes.

Al aceptar (expresa o tácitamente) cambia la fecha comprometida
(desired_date -> delivery_date): los avisos de entrega se vuelven a programar
para la fecha nueva (las marcas son por fecha y se limpian las de la fecha
anterior) y el plazo de recepción se recalcula solo.

CANCELACIÓN SIN COSTO PARA EL COMPRADOR (``_shrimp_cancel_for_delay``)
  * stock: cada movimiento de venta (transfer) de la compra se devuelve a SU
    lote del vendedor con un movimiento interno «Ajuste / Entra al lote» que
    apunta al movimiento de venta (parent_move_id) y a la compra
    (transaction_id): el mismo patrón que anular una salida o deshacer una
    siembra. Nada se borra: la trazabilidad muestra la venta y su reversa.
  * comisión del vendedor: shrimp.charge.action_cancel_charge (sin facturar:
    se anula; facturada: nota de crédito, o la reintenta el cron).
  * honorario de verificación del comprador: se anula igual si la
    verificación no llegó a hacerse.
  * la compra queda «Cancelada», con el motivo en el historial de retrasos.

Los strikes del vendedor viven en models/shrimp_seller_strike.py.
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import sql
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

# Ajustes › CamaronMarket › Entregas y recepción (res_config_settings.py).
DELAY_MAX_NOTICES_PARAM = "shrimp_marketplace.delay_max_notices"
DELAY_MAX_NOTICES_DEFAULT = 2
DELAY_MAX_DAYS_PARAM = "shrimp_marketplace.delay_max_days"
DELAY_MAX_DAYS_DEFAULT = 15
DELAY_RESPONSE_HOURS_PARAM = "shrimp_marketplace.delay_response_hours"
DELAY_RESPONSE_HOURS_DEFAULT = 48
DELAY_GRACE_DAYS_PARAM = "shrimp_marketplace.delay_grace_days"
DELAY_GRACE_DAYS_DEFAULT = 2
# Desde qué día cuentan los atrasos para strikes y para cancelar sin costo
# (lo fija la instalación / el -u de esta versión): las compras viejas que
# nadie marcó como despachadas no generan una tanda de strikes ni se pueden
# cancelar por un atraso anterior a esta regla.
DELAY_SINCE_PARAM = "shrimp_marketplace.delay_rules_since"

DELAY_REASONS = [
    ("aguaje", "Aguaje / clima"),
    ("production", "Producción"),
    ("sanitary", "Sanitario"),
    ("transport", "Transporte"),
    ("other", "Otro"),
]
DELAY_STATES = [
    ("proposed", "Propuesto (espera al comprador)"),
    ("countered", "Contrapropuesta (espera al vendedor)"),
    ("accepted", "Aceptado"),
    ("tacit", "Aceptado tácitamente"),
    ("rejected", "Contrapropuesta rechazada"),
    ("cancelled", "Compra cancelada"),
]
OPEN_STATES = ("proposed", "countered")
# Por qué el comprador puede cancelar sin costo.
CANCEL_CAUSES = [
    ("rejected", "El vendedor rechazó (o no respondió) la contrapropuesta"),
    ("over_cap", "El atraso total superó el tope"),
    ("unnotified", "Pasó la fecha acordada sin despacho ni aviso"),
    ("not_delivered", "Venció el plazo de recepción sin despacho"),
]

# Tablas que crea el -u de este cambio. Mientras el servicio vivo corre este
# código sin haber actualizado el módulo, los ganchos que se ejecutan en
# flujos existentes (cron, catálogo, publicar, Mi panel) no las tocan.
_DELAY_TABLES = ("shrimp_transaction_delay", "shrimp_seller_strike", "shrimp_seller_suspension")


def shrimp_delay_ready(env):
    """True si las tablas de retrasos y strikes ya existen en la base."""
    reg = env.registry
    if getattr(reg, "_shrimp_delay_ready", False):
        return True
    listo = all(sql.table_exists(env.cr, t) for t in _DELAY_TABLES)
    if listo:
        reg._shrimp_delay_ready = True
    return listo


class ShrimpTransactionDelay(models.Model):
    _name = "shrimp.transaction.delay"
    _description = "Retraso de entrega (aviso y negociación)"
    _order = "notice_date desc, id desc"

    name = fields.Char(string="Referencia", compute="_compute_name")
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Compra", required=True, ondelete="cascade", index=True)
    seller_partner_id = fields.Many2one(
        related="transaction_id.seller_partner_id", store=True, string="Vendedor", index=True)
    buyer_partner_id = fields.Many2one(
        related="transaction_id.buyer_partner_id", store=True, string="Comprador")
    kind = fields.Selection(
        [("notice", "Aviso de retraso"), ("cancel", "Cancelación por retraso")],
        string="Tipo", required=True, default="notice", index=True,
        help="«Cancelación por retraso»: el comprador canceló sin que hubiera un aviso abierto "
             "(atraso sin aviso o atraso total mayor al tope).")
    notice_date = fields.Datetime(string="Avisado el", default=fields.Datetime.now, required=True)
    notified_by_id = fields.Many2one("res.users", string="Avisó", readonly=True)
    original_date = fields.Date(string="Fecha original de la compra", readonly=True)
    old_date = fields.Date(string="Fecha anterior", readonly=True)
    new_date = fields.Date(string="Fecha propuesta", readonly=True)
    reason = fields.Selection(DELAY_REASONS, string="Motivo")
    note = fields.Text(string="Nota del vendedor")
    state = fields.Selection(DELAY_STATES, string="Estado", required=True, default="proposed",
                             index=True)
    buyer_deadline = fields.Datetime(string="El comprador responde hasta")
    buyer_response_at = fields.Datetime(string="Respuesta del comprador")
    buyer_response_by_id = fields.Many2one("res.users", string="Respondió (comprador)")
    counter_date = fields.Date(string="Fecha contrapropuesta")
    counter_note = fields.Text(string="Nota del comprador")
    seller_deadline = fields.Datetime(string="El vendedor responde hasta")
    seller_response_at = fields.Datetime(string="Respuesta del vendedor")
    seller_response_by_id = fields.Many2one("res.users", string="Respondió (vendedor)")
    rejected_auto = fields.Boolean(
        string="Rechazada por falta de respuesta",
        help="El vendedor no respondió la contrapropuesta en el plazo: cuenta como rechazada.")
    final_date = fields.Date(string="Fecha acordada")
    cancel_cause = fields.Selection(CANCEL_CAUSES, string="Causa de la cancelación")
    cancelled_at = fields.Datetime(string="Cancelada el")
    cancelled_by_id = fields.Many2one("res.users", string="Canceló")
    cancel_note = fields.Text(string="Nota de la cancelación")

    @api.depends("transaction_id.name", "kind")
    def _compute_name(self):
        for rec in self:
            etiqueta = _("Cancelación") if rec.kind == "cancel" else _("Retraso")
            rec.name = "%s · %s" % (etiqueta, rec.transaction_id.name or "")

    def shrimp_reason_label(self):
        self.ensure_one()
        return dict(DELAY_REASONS).get(self.reason, "")

    def shrimp_state_label(self):
        self.ensure_one()
        return dict(self._fields["state"]._description_selection(self.env)).get(self.state, "")

    def shrimp_events(self):
        """Pasos de este aviso para la línea de tiempo del portal, en orden:
        [{'date', 'icon', 'tone', 'title', 'detail', 'note'}]."""
        self.ensure_one()
        tx = self.transaction_id
        fmt = lambda d: d.strftime("%d/%m/%Y") if d else "—"  # noqa: E731
        ev = []
        if self.kind == "notice":
            ev.append({
                "date": self.notice_date, "icon": "fa-clock-o", "tone": "warn",
                "title": _("El vendedor avisó un retraso: del %(a)s al %(b)s") % {
                    "a": fmt(self.old_date), "b": fmt(self.new_date)},
                "detail": _("Motivo: %s") % self.shrimp_reason_label(),
                "note": self.note or "",
            })
            if self.buyer_response_at and self.counter_date:
                ev.append({"date": self.buyer_response_at, "icon": "fa-exchange", "tone": "info",
                           "title": _("El comprador propuso otra fecha: %s") % fmt(self.counter_date),
                           "detail": "", "note": self.counter_note or ""})
            elif self.buyer_response_at:
                ev.append({"date": self.buyer_response_at, "icon": "fa-check", "tone": "ok",
                           "title": _("El comprador aceptó la nueva fecha"),
                           "detail": _("Entrega el %s.") % fmt(self.final_date or self.new_date),
                           "note": ""})
            elif self.final_date:
                ev.append({"date": self.buyer_deadline, "icon": "fa-hourglass-end", "tone": "info",
                           "title": _("Aceptado tácitamente"),
                           "detail": _("El comprador no respondió en el plazo: la entrega pasa al %s.")
                           % fmt(self.final_date), "note": ""})
            elif self.state == "proposed":
                ev.append({"date": self.buyer_deadline, "icon": "fa-hourglass-half", "tone": "current",
                           "title": _("Esperando la respuesta del comprador"),
                           "detail": _("Si no responde a tiempo, la nueva fecha se da por aceptada."),
                           "note": "", "pending": True})
            if self.state == "countered":
                ev.append({"date": self.seller_deadline, "icon": "fa-hourglass-half", "tone": "current",
                           "title": _("Esperando la respuesta del vendedor"),
                           "detail": _("Si no responde a tiempo, la contrapropuesta cuenta como "
                                       "rechazada."), "note": "", "pending": True})
            if self.counter_date and (self.seller_response_at or self.rejected_auto):
                if self.seller_response_at and self.final_date == self.counter_date:
                    ev.append({"date": self.seller_response_at, "icon": "fa-check", "tone": "ok",
                               "title": _("El vendedor aceptó la fecha del comprador"),
                               "detail": _("Entrega el %s.") % fmt(self.counter_date), "note": ""})
                elif self.rejected_auto:
                    ev.append({"date": self.seller_deadline, "icon": "fa-times", "tone": "danger",
                               "title": _("El vendedor no respondió la contrapropuesta"),
                               "detail": _("Cuenta como rechazada: el comprador puede cancelar sin "
                                           "costo."), "note": ""})
                else:
                    ev.append({"date": self.seller_response_at, "icon": "fa-times", "tone": "danger",
                               "title": _("El vendedor rechazó la contrapropuesta"),
                               "detail": _("Se mantiene la entrega del %s; el comprador puede "
                                           "cancelar sin costo.") % fmt(self.old_date), "note": ""})
        if self.state == "cancelled" or self.kind == "cancel":
            ev.append({"date": self.cancelled_at, "icon": "fa-ban", "tone": "danger",
                       "title": _("El comprador canceló la compra sin costo"),
                       "detail": dict(CANCEL_CAUSES).get(self.cancel_cause, ""),
                       "note": self.cancel_note or ""})
        for e in ev:
            e["date_label"] = tx.shrimp_fmt_dt(e["date"]) if e["date"] else ""
        return ev


class ShrimpTransactionDelayMixin(models.Model):
    _inherit = "shrimp.transaction"

    # One2many y calculados sin columna: no tocan la tabla de la compra.
    delay_ids = fields.One2many("shrimp.transaction.delay", "transaction_id",
                                string="Retrasos de entrega")
    delay_count = fields.Integer(string="Avisos de retraso", compute="_compute_delay_info")
    delay_original_date = fields.Date(string="Fecha de entrega original",
                                      compute="_compute_delay_info")
    delay_cap_date = fields.Date(string="Fecha máxima (tope)", compute="_compute_delay_info")
    delay_open_state = fields.Selection(DELAY_STATES, string="Negociación abierta",
                                        compute="_compute_delay_info")

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_delay_max_notices(self):
        return self.env["shrimp.settings"].get_int(
            DELAY_MAX_NOTICES_PARAM, DELAY_MAX_NOTICES_DEFAULT, minimum=0, maximum=10)

    @api.model
    def _shrimp_delay_max_days(self):
        return self.env["shrimp.settings"].get_int(
            DELAY_MAX_DAYS_PARAM, DELAY_MAX_DAYS_DEFAULT, minimum=1, maximum=180)

    @api.model
    def _shrimp_delay_response_hours(self):
        return self.env["shrimp.settings"].get_int(
            DELAY_RESPONSE_HOURS_PARAM, DELAY_RESPONSE_HOURS_DEFAULT, minimum=1, maximum=720)

    @api.model
    def _shrimp_delay_grace_days(self):
        return self.env["shrimp.settings"].get_int(
            DELAY_GRACE_DAYS_PARAM, DELAY_GRACE_DAYS_DEFAULT, minimum=0, maximum=60)

    @api.model
    def _shrimp_delay_since(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(DELAY_SINCE_PARAM)
        try:
            return fields.Date.to_date(raw) if raw else False
        except (TypeError, ValueError):
            return False

    @api.model
    def _shrimp_delay_rule_applies(self, vencimiento):
        """¿Cuenta un atraso cuyo plazo terminó en `vencimiento`? Solo si
        terminó desde que existe la regla (DELAY_SINCE_PARAM)."""
        desde = self._shrimp_delay_since()
        if desde and not (vencimiento and vencimiento >= desde):
            return False
        # D-26: las compras directas que recibieron el plazo de recepción en
        # la migración (marca «d26_from:AAAA-MM-DD») no suman atrasos
        # anteriores a ese día: antes no estaban sujetas a la regla.
        piso = self._shrimp_mark_date("d26_from") if len(self) == 1 else False
        return not (piso and vencimiento and vencimiento < piso)

    # ------------------------------------------------------------------
    # Estado de la negociación
    # ------------------------------------------------------------------
    def _shrimp_delay_notices(self):
        self.ensure_one()
        return self.sudo().delay_ids.filtered(lambda d: d.kind == "notice").sorted(
            lambda d: (d.notice_date, d.id))

    def shrimp_delay_open(self):
        """El aviso que espera respuesta (del comprador o del vendedor)."""
        self.ensure_one()
        return self._shrimp_delay_notices().filtered(lambda d: d.state in OPEN_STATES)[-1:]

    def shrimp_delay_last(self):
        self.ensure_one()
        return self._shrimp_delay_notices()[-1:]

    def shrimp_delay_original_date(self):
        """La fecha comprometida al comprar (antes de cualquier retraso)."""
        self.ensure_one()
        primero = self._shrimp_delay_notices()[:1]
        if primero and primero.original_date:
            return primero.original_date
        return self.delivery_date

    def shrimp_delay_cap_date(self):
        original = self.shrimp_delay_original_date()
        return original + timedelta(days=self._shrimp_delay_max_days()) if original else False

    @api.depends("delay_ids.state", "delivery_date")
    def _compute_delay_info(self):
        for rec in self:
            if not rec.id or not shrimp_delay_ready(self.env):
                rec.delay_count = 0
                rec.delay_original_date = rec.delivery_date
                rec.delay_cap_date = False
                rec.delay_open_state = False
                continue
            rec.delay_count = len(rec._shrimp_delay_notices())
            rec.delay_original_date = rec.shrimp_delay_original_date()
            rec.delay_cap_date = rec.shrimp_delay_cap_date()
            rec.delay_open_state = rec.shrimp_delay_open().state or False

    def _shrimp_delay_applies(self):
        """Compra en la que existe el aviso de retraso (ver docstring)."""
        self.ensure_one()
        return bool(self.state == "confirmed" and self.reception_required
                    and self._shrimp_is_direct_delivery() and self.delivery_date)

    def shrimp_delay_notify_problem(self):
        """'' si el vendedor puede avisar un retraso ahora; si no, por qué."""
        self.ensure_one()
        if not self._shrimp_delay_applies():
            return _("Esta compra no admite avisos de retraso.")
        if self.dispatched_at:
            return _("La compra ya está despachada.")
        if self.reception_status == "incidence":
            return _("La compra tiene una incidencia en revisión.")
        if self.reception_status == "not_delivered":
            return _("Venció el plazo de recepción sin despacho: la compra quedó «No entregada».")
        if self.shrimp_delay_open():
            return _("Ya hay un aviso de retraso esperando respuesta.")
        ultimo = self.shrimp_delay_last()
        if ultimo and ultimo.state == "rejected":
            return _("El comprador ya puede cancelar: no se puede volver a avisar después de "
                     "rechazar su contrapropuesta.")
        maximo = self._shrimp_delay_max_notices()
        if len(self._shrimp_delay_notices()) >= maximo:
            return _("Ya usaste los %s avisos de retraso de esta compra.") % maximo
        hoy = self._shrimp_today()
        limite = self.delivery_date + timedelta(days=self._shrimp_delay_grace_days())
        if hoy > limite:
            return _("El aviso se podía enviar hasta el %s.") % limite.strftime("%d/%m/%Y")
        tope = self.shrimp_delay_cap_date()
        if not tope or tope <= self.delivery_date:
            return _("La entrega ya está en la fecha máxima permitida (%s).") % (
                tope.strftime("%d/%m/%Y") if tope else "—")
        return ""

    def shrimp_delay_can_notify(self):
        self.ensure_one()
        return not self.shrimp_delay_notify_problem()

    def shrimp_delay_notices_left(self):
        self.ensure_one()
        return max(self._shrimp_delay_max_notices() - len(self._shrimp_delay_notices()), 0)

    def shrimp_delay_min_new_date(self):
        """Primera fecha que se puede proponer: el día siguiente a la fecha
        acordada (y nunca antes de hoy)."""
        self.ensure_one()
        return max(self.delivery_date + timedelta(days=1), self._shrimp_today())

    def shrimp_delay_cancel_cause(self):
        """Por qué el comprador puede cancelar sin costo ahora (o False)."""
        self.ensure_one()
        if self.state == "confirmed" and self.reception_status == "not_delivered" \
                and not self.dispatched_at and self._shrimp_is_direct_delivery():
            # Venció el plazo de recepción sin despacho (no se confirma sola).
            return "not_delivered"
        if not self._shrimp_delay_applies() or self.dispatched_at \
                or self.reception_status == "incidence":
            return False
        if self.shrimp_delay_open():
            return False
        hoy = self._shrimp_today()
        ultimo = self.shrimp_delay_last()
        if ultimo and ultimo.state == "rejected":
            return "rejected"
        tope = self.shrimp_delay_cap_date()
        if tope and hoy > tope and self._shrimp_delay_rule_applies(tope):
            return "over_cap"
        gracia = self.delivery_date + timedelta(days=self._shrimp_delay_grace_days())
        if hoy > gracia and self._shrimp_delay_rule_applies(gracia) \
                and not self._shrimp_delay_notice_for(self.delivery_date):
            return "unnotified"
        return False

    def _shrimp_delay_notice_for(self, fecha):
        """Aviso de retraso hecho cuando `fecha` era la fecha acordada."""
        self.ensure_one()
        return self._shrimp_delay_notices().filtered(lambda d: d.old_date == fecha)

    def shrimp_delay_cancel_cause_label(self):
        causa = self.shrimp_delay_cancel_cause()
        return dict(CANCEL_CAUSES).get(causa, "") if causa else ""

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def _shrimp_delay_validate_date(self, fecha, minimo, campo):
        tope = self.shrimp_delay_cap_date()
        if not fecha:
            raise ValidationError(_("Indica la %s.") % campo)
        fecha = fields.Date.to_date(fecha)
        if fecha < minimo:
            raise ValidationError(_("La %(c)s tiene que ser desde el %(f)s.") % {
                "c": campo, "f": minimo.strftime("%d/%m/%Y")})
        if tope and fecha > tope:
            raise ValidationError(_(
                "La %(c)s no puede pasar del %(f)s: el retraso total no puede superar %(n)s días "
                "sobre la fecha original (%(o)s).") % {
                "c": campo, "f": tope.strftime("%d/%m/%Y"), "n": self._shrimp_delay_max_days(),
                "o": self.shrimp_delay_original_date().strftime("%d/%m/%Y")})
        return fecha

    def action_notify_delay(self, new_date, reason, note=None, actor=None):
        """El vendedor avisa que no llega a la fecha acordada."""
        self.ensure_one()
        self._shrimp_check_party("seller", actor)
        problema = self.shrimp_delay_notify_problem()
        if problema:
            raise ValidationError(problema)
        if reason not in dict(DELAY_REASONS):
            raise ValidationError(_("Elige el motivo del retraso."))
        fecha = self._shrimp_delay_validate_date(new_date, self.shrimp_delay_min_new_date(),
                                                 _("fecha nueva de entrega"))
        ahora = fields.Datetime.now()
        plazo = ahora + timedelta(hours=self._shrimp_delay_response_hours())
        aviso = self.env["shrimp.transaction.delay"].sudo().create({
            "transaction_id": self.id,
            "notice_date": ahora,
            "notified_by_id": self.env.user.id,
            "original_date": self.shrimp_delay_original_date(),
            "old_date": self.delivery_date,
            "new_date": fecha,
            "reason": reason,
            "note": (note or "").strip() or False,
            "state": "proposed",
            "buyer_deadline": plazo,
        })
        nota = (" " + _("Nota del vendedor: %s") % aviso.note) if aviso.note else ""
        self._shrimp_notify("buyer", _("Retraso de entrega en tu compra %s") % self.name, _(
            "El vendedor avisó que la entrega de tu compra %(r)s (%(p)s) pasa del %(a)s al %(b)s. "
            "Motivo: %(m)s.%(n)s Tienes hasta el %(l)s para responder: aceptar la nueva fecha o "
            "proponer otra (una sola vez). Si no respondes, la nueva fecha se da por aceptada.") % {
            "r": self.name, "p": self.product_id.name or "", "a": aviso.old_date.strftime("%d/%m/%Y"),
            "b": fecha.strftime("%d/%m/%Y"), "m": aviso.shrimp_reason_label(), "n": nota,
            "l": self.shrimp_fmt_dt(plazo, self.buyer_partner_id)})
        return aviso

    def _shrimp_delay_apply_date(self, fecha):
        """Cambia la fecha comprometida y reprograma los avisos de entrega.

        Las marcas de avisos son por fecha («dlv_pre:2026-10-20»): se quitan
        las de la fecha anterior (y los recordatorios del plazo de recepción
        viejo) para que la fecha nueva tenga su ciclo completo de avisos."""
        self.ensure_one()
        anterior = self.delivery_date
        marcas = {m for m in self._shrimp_marks()
                  if not (anterior and m.startswith("dlv_") and m.endswith(":" + anterior.isoformat()))}
        self.sudo().write({"desired_date": fecha,
                           "reception_notify_marks": " ".join(sorted(marcas)) or False})
        self.invalidate_recordset(["delivery_date", "reception_start_date"])

    def _shrimp_delay_check_buyer(self, aviso, actor):
        self._shrimp_check_party("buyer", actor)
        if not aviso or aviso.state != "proposed":
            raise ValidationError(_("No hay un aviso de retraso esperando tu respuesta."))

    def action_delay_buyer_accept(self, actor=None):
        """El comprador acepta la fecha nueva."""
        self.ensure_one()
        aviso = self.shrimp_delay_open()
        self._shrimp_delay_check_buyer(aviso, actor)
        aviso.sudo().write({"state": "accepted", "buyer_response_at": fields.Datetime.now(),
                            "buyer_response_by_id": self.env.user.id, "final_date": aviso.new_date})
        self._shrimp_delay_apply_date(aviso.new_date)
        f = aviso.new_date.strftime("%d/%m/%Y")
        self._shrimp_notify("seller", _("Venta %s: el comprador aceptó la nueva fecha") % self.name,
                            _("El comprador aceptó entregar la compra %(r)s el %(f)s.") % {
                                "r": self.name, "f": f})
        self._shrimp_notify("buyer", _("Nueva fecha de entrega de %s") % self.name,
                            _("Aceptaste la nueva fecha de entrega de tu compra %(r)s: %(f)s.") % {
                                "r": self.name, "f": f})
        return aviso

    def action_delay_buyer_counter(self, counter_date, note=None, actor=None):
        """El comprador propone otra fecha (una sola vez)."""
        self.ensure_one()
        aviso = self.shrimp_delay_open()
        self._shrimp_delay_check_buyer(aviso, actor)
        minimo = max(aviso.old_date, self._shrimp_today())
        fecha = self._shrimp_delay_validate_date(counter_date, minimo, _("fecha que propones"))
        if fecha == aviso.new_date:
            raise ValidationError(_("Esa es la fecha que propuso el vendedor: acéptala en vez de "
                                    "contraproponer."))
        ahora = fields.Datetime.now()
        plazo = ahora + timedelta(hours=self._shrimp_delay_response_hours())
        aviso.sudo().write({
            "state": "countered", "counter_date": fecha,
            "counter_note": (note or "").strip() or False,
            "buyer_response_at": ahora, "buyer_response_by_id": self.env.user.id,
            "seller_deadline": plazo,
        })
        nota = (" " + _("Nota del comprador: %s") % aviso.counter_note) if aviso.counter_note else ""
        self._shrimp_notify("seller", _("Venta %s: el comprador propone otra fecha") % self.name, _(
            "El comprador no aceptó el %(b)s y propone entregar la compra %(r)s el %(f)s.%(n)s "
            "Tienes hasta el %(l)s para aceptar o rechazar. Si la rechazas o no respondes, el "
            "comprador podrá cancelar la compra sin costo y, si la cancela, recibirás un "
            "strike.") % {
            "b": aviso.new_date.strftime("%d/%m/%Y"), "r": self.name,
            "f": fecha.strftime("%d/%m/%Y"), "n": nota,
            "l": self.shrimp_fmt_dt(plazo, self.seller_partner_id)})
        return aviso

    def _shrimp_delay_check_seller(self, aviso, actor):
        self._shrimp_check_party("seller", actor)
        if not aviso or aviso.state != "countered":
            raise ValidationError(_("No hay una contrapropuesta esperando tu respuesta."))

    def action_delay_seller_accept(self, actor=None):
        """El vendedor acepta la fecha que propuso el comprador."""
        self.ensure_one()
        aviso = self.shrimp_delay_open()
        self._shrimp_delay_check_seller(aviso, actor)
        aviso.sudo().write({"state": "accepted", "seller_response_at": fields.Datetime.now(),
                            "seller_response_by_id": self.env.user.id,
                            "final_date": aviso.counter_date})
        self._shrimp_delay_apply_date(aviso.counter_date)
        f = aviso.counter_date.strftime("%d/%m/%Y")
        self._shrimp_notify("buyer", _("El vendedor aceptó tu fecha para %s") % self.name,
                            _("El vendedor aceptó entregar tu compra %(r)s el %(f)s.") % {
                                "r": self.name, "f": f})
        return aviso

    def action_delay_seller_reject(self, actor=None, auto=False):
        """El vendedor rechaza la contrapropuesta (o venció su plazo, `auto`):
        la fecha acordada no cambia y el comprador puede cancelar sin costo."""
        self.ensure_one()
        aviso = self.shrimp_delay_open()
        if auto:
            if not aviso or aviso.state != "countered":
                return aviso
        else:
            self._shrimp_delay_check_seller(aviso, actor)
        vals = {"state": "rejected", "rejected_auto": bool(auto)}
        if not auto:
            vals.update(seller_response_at=fields.Datetime.now(),
                        seller_response_by_id=self.env.user.id)
        aviso.sudo().write(vals)
        f = self.delivery_date.strftime("%d/%m/%Y")
        if auto:
            texto = _("El vendedor no respondió en el plazo a tu propuesta de entregar la compra "
                      "%(r)s el %(c)s: cuenta como rechazada.") % {
                "r": self.name, "c": aviso.counter_date.strftime("%d/%m/%Y")}
            self._shrimp_notify("seller", _("Venta %s: contrapropuesta sin respuesta") % self.name, _(
                "No respondiste la contrapropuesta del comprador para la compra %(r)s: cuenta como "
                "rechazada. La entrega sigue siendo el %(f)s y el comprador puede cancelar la compra "
                "sin costo (si la cancela, recibirás un strike).") % {"r": self.name, "f": f})
        else:
            texto = _("El vendedor rechazó tu propuesta de entregar la compra %(r)s el %(c)s.") % {
                "r": self.name, "c": aviso.counter_date.strftime("%d/%m/%Y")}
        texto += " " + _("La entrega acordada sigue siendo el %s. Puedes esperarla o cancelar la "
                         "compra sin costo desde la compra.") % f
        self._shrimp_notify("buyer", _("Puedes cancelar sin costo la compra %s") % self.name, texto)
        return aviso

    def _shrimp_delay_tacit_accept(self):
        """Venció el plazo del comprador sin respuesta: la fecha nueva vale."""
        self.ensure_one()
        aviso = self.shrimp_delay_open()
        if not aviso or aviso.state != "proposed":
            return aviso
        aviso.sudo().write({"state": "tacit", "final_date": aviso.new_date})
        self._shrimp_delay_apply_date(aviso.new_date)
        f = aviso.new_date.strftime("%d/%m/%Y")
        self._shrimp_notify("buyer", _("Nueva fecha de entrega de %s") % self.name, _(
            "No respondiste el aviso de retraso de tu compra %(r)s en el plazo: la nueva fecha de "
            "entrega (%(f)s) se dio por aceptada.") % {"r": self.name, "f": f})
        self._shrimp_notify("seller", _("Venta %s: nueva fecha aceptada") % self.name, _(
            "El comprador no respondió en el plazo: la nueva fecha de entrega de la compra %(r)s "
            "(%(f)s) quedó aceptada.") % {"r": self.name, "f": f})
        return aviso

    def action_cancel_for_delay(self, note=None, actor=None):
        """El comprador cancela la compra sin costo por el retraso."""
        self.ensure_one()
        self._shrimp_check_party("buyer", actor)
        causa = self.shrimp_delay_cancel_cause()
        if not causa:
            raise ValidationError(_(
                "Esta compra no se puede cancelar por retraso: solo si el vendedor rechazó (o no "
                "respondió) tu contrapropuesta, si el atraso total supera %(n)s días, si pasaron "
                "%(g)s días de la fecha acordada sin despacho ni aviso o si venció el plazo de "
                "recepción sin despacho.") % {
                "n": self._shrimp_delay_max_days(), "g": self._shrimp_delay_grace_days()})
        return self._shrimp_cancel_for_delay(causa, note)

    # ------------------------------------------------------------------
    # Cancelación sin costo: stock, cobros, historial, strike y avisos
    # ------------------------------------------------------------------
    def _shrimp_cancel_for_delay(self, causa, note=None):
        self.ensure_one()
        tx = self.sudo()
        if tx.state != "confirmed":
            raise ValidationError(_("Solo se cancela una compra confirmada que espera su entrega."))
        motivo = _("Cancelación de la compra %(r)s por retraso de entrega (%(c)s)") % {
            "r": tx.name, "c": dict(CANCEL_CAUSES).get(causa, causa)}
        ahora = fields.Datetime.now()
        # 1) El historial: el aviso rechazado (o uno nuevo de cancelación).
        ultimo = tx.shrimp_delay_last()
        vals_cancel = {"cancel_cause": causa, "cancelled_at": ahora,
                       "cancelled_by_id": self.env.user.id,
                       "cancel_note": (note or "").strip() or False}
        if causa == "rejected" and ultimo:
            ultimo.write(dict(vals_cancel, state="cancelled"))
            registro = ultimo
        else:
            registro = self.env["shrimp.transaction.delay"].sudo().create(dict(
                vals_cancel, transaction_id=tx.id, kind="cancel", state="cancelled",
                notice_date=ahora, original_date=tx.shrimp_delay_original_date(),
                old_date=tx.delivery_date))
        # 2) El stock vuelve al vendedor.
        devueltos = tx._shrimp_return_stock_to_seller(motivo)
        # 3) Cobros: comisión del vendedor y verificación no realizada.
        anulados = tx._shrimp_void_charges_for_cancel(motivo)
        # 4) La compra queda cancelada (y cualquier aviso abierto se cierra).
        tx.write({"state": "cancel", "reception_status": False})
        tx.delay_ids.filtered(lambda d: d.state in OPEN_STATES).write({"state": "cancelled"})
        # 5) Strike al vendedor (una vez por compra y causa).
        if "shrimp.seller.strike" in self.env:
            self.env["shrimp.seller.strike"].sudo()._shrimp_add_strike(tx, "buyer_cancel_delay")
        resumen = _("Se devolvieron %(q)s al lote del vendedor y se anularon %(c)s cobro(s) de la "
                    "plataforma.") % {"q": tx.shrimp_fmt_qty(sum(m.qty for m in devueltos),
                                                             tx.product_id.uom_id),
                                      "c": len(anulados)}
        tx.message_post(body=Markup("<p>%s</p><p>%s</p>") % (motivo, resumen))
        tx._shrimp_notify("buyer", _("Compra %s cancelada sin costo") % tx.name, _(
            "Cancelaste la compra %(r)s por el retraso de la entrega. No tiene costo para ti: si "
            "pagaste una verificación que no se hizo, se anula.") % {"r": tx.name})
        tx._shrimp_notify("seller", _("Venta %s cancelada por el comprador") % tx.name, _(
            "El comprador canceló la compra %(r)s por el retraso de la entrega. El producto volvió "
            "a tu lote y la comisión de la plataforma se anuló. Esta cancelación suma un strike a tu "
            "cuenta.") % {"r": tx.name})
        return registro

    def _shrimp_return_stock_to_seller(self, motivo):
        """Devuelve a los lotes del vendedor lo que salió con esta compra.

        Por cada movimiento de venta (transfer) de la compra, un movimiento
        interno «Ajuste · Entra al lote» en el MISMO lote de origen, que
        apunta a la venta (parent_move_id) y a la compra. Si el lote ya no
        existe (datos antiguos sin lot_id), se crea uno nuevo del vendedor con
        el mismo origen. Devuelve los movimientos de devolución."""
        self.ensure_one()
        Move = self.env["shrimp.stock.move"].sudo()
        Lot = self.env["shrimp.stock.lot"].sudo()
        devueltos = Move
        ventas = self.stock_move_ids.sudo().filtered(
            lambda m: (m.move_type or "transfer") == "transfer"
            and m.source_partner_id == self.seller_partner_id)
        ya = self.stock_move_ids.sudo().filtered(
            lambda m: m.move_type == "adjustment" and m.direction == "in")
        if ya:
            return ya  # idempotente: ya se devolvió
        for venta in ventas:
            lote = venta.lot_id
            if not (lote and lote.owner_id == self.seller_partner_id):
                # Sin lote de origen: uno nuevo del vendedor, con el mismo
                # origen que tenía lo vendido (la cadena aguas arriba sigue).
                lote = Lot.create({
                    "product_id": venta.product_id.id,
                    "owner_id": self.seller_partner_id.id,
                    "origin_move_id": venta.parent_move_id.id or False,
                    "initial_qty": 0.0, "available_qty": 0.0,
                    "uom_id": venta.product_id.uom_id.id,
                    "state": "consumed",
                })
            # «Entra al lote»: sube el disponible (y la inicial solo si hiciera
            # falta) y deja el movimiento que lo documenta.
            devueltos |= lote._shrimp_internal_move(
                "adjustment", venta.qty, motivo, direction="in",
                transaction_id=self.id, parent_move_id=venta.id)
        producto = self.product_id.sudo()
        producto._compute_available_qty()
        producto._update_state_from_stock()
        return devueltos

    def _shrimp_void_charges_for_cancel(self, motivo):
        """Anula la comisión del vendedor y el honorario de verificación que
        no se llegó a hacer, con el mecanismo de shrimp.charge (sin factura:
        anulado; con factura contabilizada: nota de crédito)."""
        self.ensure_one()
        anulados = self.env["shrimp.charge"].sudo()
        hechas = ("done", "approved", "approved_obs", "rejected", "declared")
        for cobro in self.sudo().charge_ids.filtered(
                lambda c: c.state not in ("cancelled", "credited", "to_credit")):
            if cobro.charge_type == "commission":
                anulados |= cobro
            elif cobro.charge_type == "verification_fee":
                ver = cobro.verification_id if "verification_id" in cobro._fields else False
                if not ver or ver.state not in hechas:
                    anulados |= cobro
        if anulados:
            anulados.action_cancel_charge(motivo)
        return anulados

    # ------------------------------------------------------------------
    # Cron (se suma a «entregas y recepción», cada hora)
    # ------------------------------------------------------------------
    @api.model
    def _cron_reception_and_delivery(self, limit=500):
        if shrimp_delay_ready(self.env):
            try:
                self._shrimp_cron_delays(limit)
            except Exception:  # noqa: BLE001 — los retrasos no frenan la recepción
                _logger.exception("Retrasos de entrega: fallo del cron")
        return super()._cron_reception_and_delivery(limit=limit)

    @api.model
    def _shrimp_cron_delays(self, limit=500):
        """Vence los plazos de respuesta y pone los strikes por atraso."""
        ahora = fields.Datetime.now()
        hoy = self._shrimp_today()
        Delay = self.env["shrimp.transaction.delay"].sudo()
        for aviso in Delay.search([("state", "=", "proposed"), ("buyer_deadline", "<=", ahora)],
                                  limit=limit):
            try:
                with self.env.cr.savepoint():
                    aviso.transaction_id._shrimp_delay_tacit_accept()
            except Exception:  # noqa: BLE001
                _logger.exception("No se pudo aceptar tácitamente el retraso %s", aviso.id)
        for aviso in Delay.search([("state", "=", "countered"), ("seller_deadline", "<=", ahora)],
                                  limit=limit):
            try:
                with self.env.cr.savepoint():
                    aviso.transaction_id.action_delay_seller_reject(auto=True)
            except Exception:  # noqa: BLE001
                _logger.exception("No se pudo vencer la contrapropuesta %s", aviso.id)
        # Atraso sin aviso y atraso total mayor al tope.
        gracia = self._shrimp_delay_grace_days()
        candidatas = self.sudo().search([
            ("state", "=", "confirmed"), ("reception_required", "=", True),
            ("dispatched_at", "=", False), ("delivery_date", "!=", False),
            ("delivery_date", "<", hoy), ("reception_status", "!=", "incidence"),
        ], limit=limit, order="delivery_date asc, id asc")
        Strike = self.env["shrimp.seller.strike"].sudo()
        for tx in candidatas:
            if not tx._shrimp_is_direct_delivery() or tx.shrimp_delay_open():
                continue
            try:
                with self.env.cr.savepoint():
                    fin_gracia = tx.delivery_date + timedelta(days=gracia)
                    if hoy > fin_gracia and tx._shrimp_delay_rule_applies(fin_gracia) \
                            and not tx._shrimp_delay_notice_for(tx.delivery_date):
                        if Strike._shrimp_add_strike(tx, "unnotified_delay", only_new=True):
                            tx._shrimp_notify("buyer", _("Puedes cancelar sin costo la compra %s")
                                              % tx.name, _(
                                "La entrega de tu compra %(r)s era el %(f)s y el vendedor no la "
                                "despachó ni avisó un retraso. Puedes esperarla o cancelar la "
                                "compra sin costo desde la compra.") % {
                                "r": tx.name, "f": tx.delivery_date.strftime("%d/%m/%Y")})
                    tope = tx.shrimp_delay_cap_date()
                    if tope and hoy > tope and tx._shrimp_delay_rule_applies(tope):
                        Strike._shrimp_add_strike(tx, "delay_over_cap", only_new=True)
            except Exception:  # noqa: BLE001
                _logger.exception("Strikes por atraso de %s", tx.name)
        return True
