"""Webhooks: suscripciones de los socios y cola de entregas.

Reglas que no se negocian:

* NUNCA se hace HTTP dentro de la transacción de negocio. Un evento solo
  encola una fila en ``shrimp.webhook.delivery`` (que se confirma o se
  deshace junto con el negocio) y el cron la manda después.
* Solo se avisa a quien tiene derecho a ver el recurso: la regla de
  audiencia del evento Y las reglas de acceso del usuario dueño de la
  suscripción (``has_access('read')``). Las dos tienen que dar sí.
* La carga es mínima (id, tipo, fecha, recurso {type, uuid}): el receptor
  pide el detalle a la API con su clave. Así un webhook interceptado no
  filtra datos comerciales y el detalle siempre pasa por los permisos.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

BACKOFF_MINUTES = [1, 5, 15, 60, 180, 360, 720, 1440]
MAX_ATTEMPTS = len(BACKOFF_MINUTES)
AUTO_DISABLE_AFTER = 50          # fallos definitivos seguidos
TIMEOUT = (3.05, 7)


class EventSpec:
    def __init__(self, code, label, resource_type, audience, check_access=True, ident=None):
        self.code = code
        self.label = label
        self.resource_type = resource_type
        self.audience = audience          # (record_sudo, partner) -> bool
        self.check_access = check_access
        # Identificador público del recurso: el uuid, salvo en modelos que ya
        # tienen uno propio (la clave de acceso de un comprobante SRI).
        self.ident = ident or (lambda rec: rec.uuid_ref)


EVENTS = {}


def register_event(code, label, resource_type, audience, check_access=True, ident=None):
    """Lo usan este módulo y los que amplían la API (shrimp_api_sri)."""
    EVENTS[code] = EventSpec(code, label, resource_type, audience, check_access, ident)


def _parties(*names):
    def audience(rec, partner):
        return any(rec[n] == partner for n in names if rec[n])
    return audience


def _lot_published_audience(product, partner):
    if not product.active or product.state != "published":
        return False
    if partner == product.seller_partner_id:
        return True
    return not product.motivo_no_comprable(partner)


register_event("transaction.state_changed", "Cambio de estado de una compra/venta",
               "transaction", _parties("buyer_partner_id", "seller_partner_id"))
register_event("check_request.created", "Nueva solicitud de chequeo",
               "check_request", _parties("buyer_partner_id", "seller_partner_id"))
register_event("verification.assigned", "Verificación asignada (empresa o técnico)",
               "verification", _parties("verifier_partner_id", "technician_partner_id"))
register_event("verification.verdict_issued", "Veredicto de verificación emitido",
               "verification", _parties("buyer_partner_id", "seller_partner_id", "verifier_partner_id"))
register_event("verification.acceptance_decided", "Una parte aceptó/rechazó/contraofertó el informe",
               "verification", _parties("buyer_partner_id", "seller_partner_id", "verifier_partner_id"))
register_event("dispatch.eta_changed", "Cita de llegada a planta fijada o movida",
               "dispatch", _parties("seller_partner_id", "buyer_partner_id",
                                    "verifier_partner_id", "technician_partner_id"))
register_event("dispatch.arrived", "Llegada real a planta registrada",
               "dispatch", _parties("seller_partner_id", "buyer_partner_id",
                                    "verifier_partner_id", "technician_partner_id"))
register_event("price_list.published", "Lista de precios publicada",
               "price_list", lambda rec, p: rec.visible_para(p))
register_event("forecast.published", "Cosecha declarada publicada",
               "forecast", lambda rec, p: rec.visible_para(p))
register_event("commitment.accepted", "Compromiso de compra aceptado",
               "commitment", _parties("farmer_partner_id", "packer_partner_id"))
register_event("commitment.broken", "Compromiso incumplido",
               "commitment", _parties("farmer_partner_id", "packer_partner_id"))
register_event("commitment.settled", "Compromiso liquidado (cumplido)",
               "commitment", _parties("farmer_partner_id", "packer_partner_id"))
register_event("harvest.confirmation_required", "La cosecha salió de la banda: hace falta firmar",
               "commitment", _parties("farmer_partner_id", "packer_partner_id"))
register_event("lot.published", "Lote publicado en el marketplace",
               "product", _lot_published_audience, check_access=False)
register_event("copack.offer_received", "Oferta de empaque recibida",
               "copack_offer", _parties("client_partner_id"))
register_event("copack.order_state_changed", "Cambio de estado de una orden de empaque",
               "copack_order", _parties("client_partner_id", "copacker_partner_id"))
register_event("copack.acta_signed", "Acta de empaque firmada por las dos partes",
               "copack_order", _parties("client_partner_id", "copacker_partner_id"))
# Una parte deshizo su decisión (vuelve a «pendiente») mientras el proceso
# seguía abierto. El detalle (quién, qué, motivo) está en el recurso.
register_event("verification.acceptance_reverted",
               "Una parte deshizo su aceptación/rechazo/contraoferta del informe",
               "verification", _parties("buyer_partner_id", "seller_partner_id", "verifier_partner_id"))
# Verificación DECLARADA por las partes: una de ellas presentó el informe y
# la otra tiene que confirmarlo (aceptar, rechazar o contraofertar).
register_event("verification.declared_submitted",
               "Una parte presentó el informe de verificación declarado (la otra debe confirmarlo)",
               "verification", _parties("buyer_partner_id", "seller_partner_id"))
register_event("copack.signature_reverted", "Una parte deshizo su firma del acta de empaque",
               "copack_order", _parties("client_partner_id", "copacker_partner_id"))
register_event("harvest.confirmation_reverted",
               "Una parte deshizo su firma de la cosecha fuera de banda",
               "commitment", _parties("farmer_partner_id", "packer_partner_id"))
register_event("export.registered", "Salida / exportación registrada (último eslabón de la cadena)",
               "export", _parties("partner_id"))
register_event("ping", "Prueba de la suscripción", "webhook_subscription",
               lambda rec, p: rec.partner_id == p, check_access=False)


def _is_testing():
    from odoo.modules import module as odoo_module
    return bool(getattr(threading.current_thread(), "testing", False)
                or getattr(odoo_module, "current_test", None))


class ShrimpWebhookSubscription(models.Model):
    _name = "shrimp.webhook.subscription"
    _description = "Suscripción a webhooks de la API externa"
    _inherit = ["shrimp.uuid.mixin"]
    _order = "id desc"

    name = fields.Char(string="Nombre", required=True)
    partner_id = fields.Many2one("res.partner", string="Socio", required=True,
                                 ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Usuario", required=True, ondelete="cascade",
        help="Con sus permisos se decide qué eventos puede recibir.")
    url = fields.Char(string="URL (https)", required=True)
    event_types = fields.Char(
        string="Eventos", required=True, default="*",
        help="Códigos separados por espacio o coma. «*» = todos los permitidos.")
    secret = fields.Char(string="Secreto de firma", copy=False, groups="base.group_system",
                         help="Se usa para firmar (HMAC-SHA256). Solo se muestra al crearla.")
    active = fields.Boolean(default=True)
    failure_count = fields.Integer(string="Fallos seguidos", readonly=True)
    last_success_at = fields.Datetime(string="Última entrega correcta", readonly=True)
    last_failure_at = fields.Datetime(string="Último fallo", readonly=True)
    last_error = fields.Char(string="Último error", readonly=True)
    disabled_reason = fields.Char(string="Motivo de desactivación", readonly=True)
    delivery_ids = fields.One2many("shrimp.webhook.delivery", "subscription_id",
                                   string="Entregas")

    @api.model
    def _new_secret(self):
        return "whsec_" + secrets.token_urlsafe(32)

    @api.model
    def _allow_insecure(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "shrimp_api.webhook_allow_insecure") in ("1", "True", "true")

    @api.constrains("url")
    def _check_url(self):
        for rec in self:
            parts = urlsplit(rec.url or "")
            if parts.scheme != "https" and not rec._allow_insecure():
                raise ValidationError(_("La URL del webhook tiene que ser https://."))
            if not parts.hostname:
                raise ValidationError(_("La URL del webhook no tiene servidor."))
            if parts.username or parts.password:
                raise ValidationError(_("La URL no puede llevar usuario ni contraseña."))

    @api.constrains("event_types")
    def _check_events(self):
        for rec in self:
            for code in rec._event_list():
                if code != "*" and code not in EVENTS:
                    raise ValidationError(_("Evento desconocido: %s") % code)

    def _event_list(self):
        self.ensure_one()
        return [e for e in (self.event_types or "").replace(",", " ").split() if e]

    def _wants(self, event_type):
        events = self._event_list()
        return event_type == "ping" or "*" in events or event_type in events

    def action_rotate_secret(self):
        """Devuelve el secreto nuevo (solo se puede ver aquí)."""
        self.ensure_one()
        secret = self._new_secret()
        self.sudo().secret = secret
        return secret

    def action_ping(self):
        self.ensure_one()
        return self.env["shrimp.webhook.delivery"].sudo()._enqueue(self, "ping", self)

    def action_reactivate(self):
        self.sudo().write({"active": True, "failure_count": 0, "disabled_reason": False})
        return True


class ShrimpWebhookDelivery(models.Model):
    _name = "shrimp.webhook.delivery"
    _description = "Entrega de webhook (cola)"
    _inherit = ["shrimp.uuid.mixin"]
    _order = "id desc"

    subscription_id = fields.Many2one("shrimp.webhook.subscription", required=True,
                                      ondelete="cascade", index=True)
    partner_id = fields.Many2one(related="subscription_id.partner_id", store=True, index=True)
    event_type = fields.Char(string="Evento", required=True, index=True)
    resource_type = fields.Char(string="Tipo de recurso")
    resource_uuid = fields.Char(string="Recurso (uuid)")
    occurred_at = fields.Datetime(string="Ocurrió el", required=True)
    payload = fields.Text(string="Carga", required=True)
    state = fields.Selection(
        [("pending", "Pendiente"), ("retrying", "Reintentando"), ("success", "Entregada"),
         ("dead", "Fallida (sin más reintentos)")],
        default="pending", required=True, index=True)
    attempts = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(index=True)
    last_status_code = fields.Integer(readonly=True)
    last_error = fields.Char(readonly=True)
    delivered_at = fields.Datetime(readonly=True)
    duration_ms = fields.Integer(readonly=True)

    # ------------------------------------------------------------------
    # Encolado
    # ------------------------------------------------------------------
    @api.model
    def _emit(self, event_type, records):
        """Punto de entrada de los modelos de negocio. Nunca rompe el negocio."""
        if not records or event_type not in EVENTS:
            return
        try:
            subs = self.env["shrimp.webhook.subscription"].sudo().search([("active", "=", True)])
            subs = subs.filtered(lambda s: s._wants(event_type))
            if not subs:
                return
            with self.env.cr.savepoint():
                created = self.browse()
                for rec in records:
                    for sub in subs:
                        created |= self._enqueue(sub, event_type, rec, trigger=False)
            if created:
                self._trigger_cron()
        except Exception:  # noqa: BLE001 - un webhook nunca tumba una venta
            _logger.exception("No se pudo encolar el evento %s", event_type)

    @api.model
    def _entitled(self, sub, spec, rec):
        partner = sub.partner_id
        if not partner or not sub.user_id.active:
            return False
        try:
            if not spec.audience(rec.sudo(), partner):
                return False
            if spec.check_access and not rec.with_user(sub.user_id).has_access("read"):
                return False
        except Exception:  # noqa: BLE001
            _logger.debug("Audiencia no evaluable para %s", spec.code, exc_info=True)
            return False
        return True

    @api.model
    def _enqueue(self, sub, event_type, rec, trigger=True):
        spec = EVENTS[event_type]
        if not self._entitled(sub, spec, rec):
            return self.browse()
        event_id = str(uuid.uuid4())
        now = fields.Datetime.now()
        payload = {
            "id": event_id,
            "type": event_type,
            "api_version": "v1",
            "occurred_at": now.replace(microsecond=0).isoformat() + "Z",
            "resource": {"type": spec.resource_type, "uuid": spec.ident(rec.sudo())},
        }
        delivery = self.sudo().create({
            "uuid_ref": event_id,
            "subscription_id": sub.id,
            "event_type": event_type,
            "resource_type": spec.resource_type,
            "resource_uuid": spec.ident(rec.sudo()),
            "occurred_at": now,
            "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            "state": "pending",
            "next_attempt_at": now,
        })
        if trigger:
            self._trigger_cron()
        return delivery

    @api.model
    def _trigger_cron(self):
        cron = self.env.ref("shrimp_api.ir_cron_webhook_send", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------
    # Envío (solo desde el cron)
    # ------------------------------------------------------------------
    @api.model
    def _sign(self, secret, timestamp, body):
        mac = hmac.new(secret.encode(), ("%s." % timestamp).encode() + body, hashlib.sha256)
        return "t=%s,v1=%s" % (timestamp, mac.hexdigest())

    @api.model
    def _host_is_public(self, host):
        """Evita SSRF: un webhook no puede apuntar a la red interna."""
        if self.env["shrimp.webhook.subscription"]._allow_insecure():
            return True
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                return False
        return True

    def _send_one(self):
        self.ensure_one()
        sub = self.subscription_id.sudo()
        now = fields.Datetime.now()
        if not sub.active:
            self.write({"state": "dead", "last_error": "Suscripción inactiva", "next_attempt_at": False})
            return False
        body = self.payload.encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Trazul-Webhooks/1.0",
            "X-Trazul-Event": self.event_type,
            "X-Trazul-Delivery": self.uuid_ref,
            "X-Trazul-Signature": self._sign(sub.secret or "", int(time.time()), body),
        }
        status, error, started = 0, False, time.monotonic()
        host = urlsplit(sub.url).hostname
        if not self._host_is_public(host):
            error = "Destino no permitido (red privada o no resoluble)"
        else:
            try:
                resp = requests.post(sub.url, data=body, headers=headers, timeout=TIMEOUT,
                                     allow_redirects=False)
                status = resp.status_code
                if not 200 <= status < 300:
                    error = "HTTP %s" % status
            except requests.RequestException as e:
                error = type(e).__name__
        duration = int((time.monotonic() - started) * 1000)
        attempts = self.attempts + 1
        if not error:
            self.write({"state": "success", "attempts": attempts, "last_status_code": status,
                        "last_error": False, "delivered_at": now, "duration_ms": duration,
                        "next_attempt_at": False})
            sub.write({"failure_count": 0, "last_success_at": now})
            return True
        dead = attempts >= MAX_ATTEMPTS
        self.write({
            "state": "dead" if dead else "retrying",
            "attempts": attempts,
            "last_status_code": status,
            "last_error": error[:200],
            "duration_ms": duration,
            "next_attempt_at": False if dead else now + timedelta(
                minutes=BACKOFF_MINUTES[min(attempts - 1, MAX_ATTEMPTS - 1)]),
        })
        vals = {"last_failure_at": now, "last_error": error[:200]}
        if dead:
            vals["failure_count"] = sub.failure_count + 1
            if vals["failure_count"] >= AUTO_DISABLE_AFTER:
                vals.update(active=False, disabled_reason=_(
                    "Desactivada tras %s entregas fallidas seguidas.") % AUTO_DISABLE_AFTER)
        sub.write(vals)
        return False

    @api.model
    def _cron_send(self, batch=100):
        self.env.cr.execute("""
            SELECT id FROM shrimp_webhook_delivery
             WHERE state IN ('pending', 'retrying')
               AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
             ORDER BY next_attempt_at NULLS FIRST, id
             LIMIT %s
             FOR UPDATE SKIP LOCKED
        """, (fields.Datetime.now(), batch))
        ids = [r[0] for r in self.env.cr.fetchall()]
        for delivery in self.sudo().browse(ids):
            try:
                delivery._send_one()
            except Exception:  # noqa: BLE001
                _logger.exception("Fallo inesperado entregando el webhook %s", delivery.id)
            if not _is_testing():
                self.env.cr.commit()
        return len(ids)

    @api.model
    def _cron_cleanup(self):
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        self.sudo().search([("state", "in", ("success", "dead")),
                            ("create_date", "<", limit)]).unlink()
        return True

    def action_retry(self):
        self.sudo().write({"state": "pending", "next_attempt_at": fields.Datetime.now()})
        self._trigger_cron()
        return True
