"""Páginas web: «Integraciones (API)» en /my, la trazabilidad pública /t/<token>
y el cierre del certificado personal que se servía sin sesión."""

import logging
from datetime import timedelta
from urllib.parse import quote

from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from odoo.addons.shrimp_marketplace.controllers.marketplace import ShrimpMarketplacePublicController

from ..models.api_scope import SCOPES
from ..models.webhook import EVENTS, RESENDABLE_STATES

_logger = logging.getLogger(__name__)

EXPIRY_CHOICES = [(30, "30 días"), (90, "90 días"), (180, "180 días"), (365, "1 año")]
DELIVERIES_SHOWN = 50
MAX_ACTIVE_KEYS = 10


class ShrimpApiPortal(http.Controller):

    # ------------------------------------------------------------------
    def _api_user_ok(self):
        user = request.env.user
        return user.share and not user._is_public()

    def _may_manage(self):
        """ME-06 / CR-12: claves y webhooks solo el administrador de la cuenta
        o un usuario con el permiso «Gestionar cuenta»."""
        return request.env.user._shrimp_has_perm("manage_account")

    _NO_MANAGE = ("Las claves de la API y los webhooks los gestiona el administrador de la cuenta "
                  "o un usuario con el permiso «Gestionar cuenta». Pídeselo al administrador.")

    def _no_store(self, response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    def _keys(self):
        return request.env["shrimp.api.key"].sudo().with_context(active_test=False).search(
            [("user_id", "=", request.env.user.id), ("key_prefix", "!=", False)], order="id desc")

    def _render_keys(self, **extra):
        values = {
            "page_name": "api_keys",
            "keys": self._keys(),
            "devices": self._devices(),
            "scopes": SCOPES,
            "expiry_choices": EXPIRY_CHOICES,
            "allowed": self._api_user_ok(),
            "account_block": self._account_block(),
            "error": extra.pop("error", None),
            "secret": extra.pop("secret", None),
            "created": extra.pop("created", None),
            # D-46: caducidad por defecto (365 días, configurable) y avisos.
            "lifetime_days": request.env["shrimp.api.key"].sudo()._lifetime_days(),
            "api_notices": self._api_notices(),
            "message": extra.pop("message", None),
        }
        values["expiry_choices"] = self._expiry_choices(values["lifetime_days"])
        values.update(extra)
        return self._no_store(request.render("shrimp_api.portal_api_keys", values))

    def _expiry_choices(self, lifetime):
        choices = [c for c in EXPIRY_CHOICES if c[0] < lifetime]
        return choices + [(lifetime, "%s días (máximo)" % lifetime if lifetime != 365 else "1 año")]

    def _api_notices(self):
        if not self._api_user_ok():
            return []
        return request.env["shrimp.api.key"].sudo()._shrimp_portal_api_notices(request.env.user)

    def _account_block(self):
        """Motivo por el que no se pueden crear claves (cuenta pendiente o
        rechazada, documentos legales MAYORES sin aceptar) o False."""
        if not self._api_user_ok():
            return False
        if not self._may_manage():
            return _(self._NO_MANAGE)
        motivo = request.env["shrimp.api.key"].sudo()._shrimp_account_block_reason(request.env.user)
        if motivo:
            return motivo
        from .framework import legal_pending_documents
        pendientes = legal_pending_documents(request.env, request.env.user)
        if pendientes:
            return _("Antes de crear claves acepta las versiones nuevas de: %s (Mi cuenta › "
                     "Documentos legales, /my/legal/accept).") % ", ".join(pendientes)
        return False

    # ------------------------------------------------------------------
    # Claves
    # ------------------------------------------------------------------
    @http.route("/my/api-keys", type="http", auth="user", website=True, methods=["GET"])
    def my_api_keys(self, **kw):
        return self._render_keys()

    @http.route("/my/api-keys/new", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def my_api_key_new(self, **post):
        if not self._api_user_ok():
            return self._render_keys(error=_("Las claves de la API externa son para socios del portal."))
        motivo = self._account_block()
        if motivo:
            return self._render_keys(error=motivo)
        # Las de «actuar como» de un supervisor (modo supervisor de la app)
        # no cuentan: no las creó el socio y caducan en horas.
        active = self._keys().filtered(lambda k: k.status == "active" and not k.supervisor_key_id)
        if len(active) >= MAX_ACTIVE_KEYS:
            return self._render_keys(error=_("Tienes %s claves activas: revoca alguna antes de crear otra.")
                                     % MAX_ACTIVE_KEYS)
        form = request.httprequest.form
        codes = [c for c in form.getlist("scopes") if c in dict(SCOPES)]
        lifetime = request.env["shrimp.api.key"].sudo()._lifetime_days()
        try:
            days = int(post.get("expiry_days") or lifetime)
        except ValueError:
            days = lifetime
        days = min(max(days, 1), lifetime)
        try:
            with request.env.cr.savepoint():
                key, secret = request.env["shrimp.api.key"].sudo().api_create_key(
                    request.env.user, post.get("name"), codes,
                    expires_at=fields.Datetime.now() + timedelta(days=days),
                    ip_allowlist=(post.get("ip_allowlist") or "").strip() or False,
                    allowed_origins=(post.get("allowed_origins") or "").strip() or False)
        except ValidationError as e:
            return self._render_keys(error=e.args[0] if e.args else str(e))
        # Se pinta en la misma respuesta (no se redirige): el secreto no debe
        # viajar en una URL ni quedar en el historial.
        return self._render_keys(secret=secret, created=key)

    @http.route("/my/api-keys/<string:ref>/revoke", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_api_key_revoke(self, ref, **post):
        key = self._keys().filtered(lambda k: k.uuid_ref == ref)[:1]
        if not key:
            raise NotFound()
        key.action_revoke()
        return request.redirect("/my/api-keys")

    @http.route("/my/api-keys/<string:ref>/renew", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_api_key_renew(self, ref, **post):
        """D-46: alarga la MISMA clave otro periodo (la integración no cambia)."""
        key = self._keys().filtered(lambda k: k.uuid_ref == ref)[:1]
        if not key:
            raise NotFound()
        motivo = self._account_block()
        if motivo:
            return self._render_keys(error=motivo)
        try:
            with request.env.cr.savepoint():
                key.action_renew()
        except ValidationError as e:
            return self._render_keys(error=e.args[0] if e.args else str(e))
        return self._render_keys(message=_("Clave «%s» renovada: ahora caduca el %s.") % (
            key.name, fields.Date.to_string(key.expires_at.date())))

    # ------------------------------------------------------------------
    # Dispositivos de la app móvil (los que firman las operaciones sensibles)
    # ------------------------------------------------------------------
    def _devices(self):
        return request.env["shrimp.api.device"].sudo().with_context(active_test=False).search(
            [("user_id", "=", request.env.user.id)], order="active desc, id desc", limit=50)

    @http.route("/my/api-keys/devices/<string:ref>/revoke", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_api_device_revoke(self, ref, **post):
        device = self._devices().filtered(lambda d: d.uuid_ref == ref)[:1]
        if not device:
            raise NotFound()
        # Igual que en la API: el dispositivo y su clave de la app.
        device._api_revoke(revoke_key=True)
        _logger.info("Portal: dispositivo %s revocado por %s", device.uuid_ref, request.env.user.login)
        return request.redirect("/my/api-keys")

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    def _subs(self):
        return request.env["shrimp.webhook.subscription"].sudo().with_context(active_test=False).search(
            [("partner_id", "=", request.env.user.shrimp_actor_partner_id.id)], order="id desc")

    def _render_webhooks(self, **extra):
        installed = request.env.registry._init_modules
        events = [(code, spec.label) for code, spec in sorted(EVENTS.items())
                  if code != "ping" and (code != "invoice.authorized" or "shrimp_api_sri" in installed)]
        values = {
            "page_name": "api_webhooks",
            "subs": self._subs(),
            "events": events,
            "allowed": self._api_user_ok() and self._may_manage(),
            "manage_block": _(self._NO_MANAGE) if self._api_user_ok() and not self._may_manage() else False,
            "error": extra.pop("error", None),
            "secret": extra.pop("secret", None),
            "created": extra.pop("created", None),
            "message": extra.pop("message", None),
            "api_notices": self._api_notices(),
            "resend_days": request.env["shrimp.webhook.subscription"].sudo()._resend_days(),
        }
        values.update(extra)
        return self._no_store(request.render("shrimp_api.portal_webhooks", values))

    @http.route("/my/webhooks", type="http", auth="user", website=True, methods=["GET"])
    def my_webhooks(self, **kw):
        return self._render_webhooks(message=kw.get("message"))

    @http.route("/my/webhooks/new", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def my_webhook_new(self, **post):
        if not self._api_user_ok():
            return self._render_webhooks(error=_("Los webhooks son para socios del portal."))
        if len(self._subs()) >= 10:
            return self._render_webhooks(error=_("Máximo 10 suscripciones por socio."))
        events = [e for e in request.httprequest.form.getlist("events") if e in EVENTS and e != "ping"]
        if post.get("all_events"):
            events = ["*"]
        if not events:
            return self._render_webhooks(error=_("Elige al menos un evento."))
        Sub = request.env["shrimp.webhook.subscription"].sudo()
        secret = Sub._new_secret()
        try:
            with request.env.cr.savepoint():
                sub = Sub.create({
                    "name": (post.get("name") or "").strip() or _("Webhook"),
                    "url": (post.get("url") or "").strip(),
                    "event_types": " ".join(events),
                    "partner_id": request.env.user.shrimp_actor_partner_id.id,
                    "user_id": request.env.user.id,
                    "secret": secret,
                })
        except ValidationError as e:
            return self._render_webhooks(error=e.args[0] if e.args else str(e))
        return self._render_webhooks(secret=secret, created=sub)

    def _my_sub(self, ref):
        sub = self._subs().filtered(lambda s: s.uuid_ref == ref)[:1]
        if not sub:
            raise NotFound()
        return sub

    @http.route("/my/webhooks/<string:ref>/delete", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_webhook_delete(self, ref, **post):
        self._my_sub(ref).unlink()
        return request.redirect("/my/webhooks")

    @http.route("/my/webhooks/<string:ref>/toggle", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_webhook_toggle(self, ref, **post):
        sub = self._my_sub(ref)
        if sub.active:
            sub.active = False
            return request.redirect("/my/webhooks")
        resent = sub.action_reactivate()
        return request.redirect("/my/webhooks?message=%s" % quote(
            _("Suscripción reactivada: %s evento(s) no entregado(s) de los últimos %s días vuelven a la cola.")
            % (resent, sub._resend_days())))

    @http.route("/my/webhooks/<string:ref>/ping", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_webhook_ping(self, ref, **post):
        self._my_sub(ref).action_ping()
        return request.redirect("/my/webhooks?message=%s" % quote(_("Ping encolado.")))

    @http.route("/my/webhooks/<string:ref>", type="http", auth="user", website=True, methods=["GET"])
    def my_webhook_deliveries(self, ref, **kw):
        """D-44: historial de entregas con «Reenviar»."""
        sub = self._my_sub(ref)
        deliveries = request.env["shrimp.webhook.delivery"].sudo().search(
            [("subscription_id", "=", sub.id)], limit=DELIVERIES_SHOWN)
        return self._no_store(request.render("shrimp_api.portal_webhook_deliveries", {
            "page_name": "api_webhooks",
            "sub": sub,
            "deliveries": deliveries,
            "resendable": RESENDABLE_STATES,
            "resend_days": sub._resend_days(),
            "message": kw.get("message"),
            "error": kw.get("error"),
        }))

    @http.route("/my/webhooks/<string:ref>/deliveries/<string:dref>/resend", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def my_webhook_delivery_resend(self, ref, dref, **post):
        sub = self._my_sub(ref)
        delivery = request.env["shrimp.webhook.delivery"].sudo().search(
            [("subscription_id", "=", sub.id), ("uuid_ref", "=", dref)], limit=1)
        if not delivery:
            raise NotFound()
        try:
            with request.env.cr.savepoint():
                delivery.action_resend()
        except ValidationError as e:
            return request.redirect("/my/webhooks/%s?error=%s" % (
                sub.uuid_ref, quote(e.args[0] if e.args else str(e))))
        return request.redirect("/my/webhooks/%s?message=%s" % (
            sub.uuid_ref, quote(_("Entrega reenviada: vuelve a la cola."))))

    # ------------------------------------------------------------------
    # Trazabilidad pública (destino del QR)
    # ------------------------------------------------------------------
    @http.route("/t/<string:token>", type="http", auth="public", website=True, sitemap=False)
    def public_trace(self, token, **kw):
        # D-29: solo compras confirmadas o completadas (o canceladas que se
        # llegaron a confirmar, con sello) y no retiradas por las partes.
        tx = request.env["shrimp.transaction"].sudo()._shrimp_trace_by_token(token)
        if not tx:
            raise NotFound()
        data = tx._public_traceability_data()
        response = request.render("shrimp_api.public_traceability", {
            "data": data, "token": token,
            "json_url": "/api/v1/public/traceability/%s" % token,
        })
        response.headers["X-Robots-Tag"] = "noindex"
        return response


class ShrimpApiCertificateGuard(ShrimpMarketplacePublicController):
    """El archivo del certificado del VENDEDOR se servía a cualquiera, sin
    sesión, con solo conocer el uuid del producto y un número de línea. Esos
    archivos suelen llevar nombre, cédula y firma de personas (LOPDP).

    Ahora solo lo abre, con sesión: el propio vendedor, un usuario interno,
    quien ya es su contraparte (compra o solicitud de chequeo con él) o quien
    podría comprarle este lote (contraparte potencial). El resto recibe un 404,
    como si no existiera. Los METADATOS del certificado (nombre, emisor,
    vigencia) siguen siendo públicos en la ficha y en la API.
    """

    @http.route()
    def marketplace_seller_certificate(self, product_ref, line_ref, **kwargs):
        partner = self._get_current_partner()
        if not partner:
            return request.redirect("/web/login?redirect=%s" % quote(request.httprequest.full_path))
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product:
            raise NotFound()
        if not self._may_open_seller_certificate(product, partner):
            raise NotFound()
        return super().marketplace_seller_certificate(product_ref, line_ref, **kwargs)

    def _may_open_seller_certificate(self, product, partner):
        seller = product.seller_partner_id
        if partner == seller or request.env.user.has_group("base.group_user"):
            return True
        pair = ["|", "&", ("seller_partner_id", "=", seller.id), ("buyer_partner_id", "=", partner.id),
                "&", ("seller_partner_id", "=", partner.id), ("buyer_partner_id", "=", seller.id)]
        env = request.env
        if env["shrimp.transaction"].sudo().search_count(pair, limit=1):
            return True
        if env["shrimp.check.request"].sudo().search_count(pair, limit=1):
            return True
        return product.state == "published" and not product.motivo_no_comprable(partner)


class ShrimpTracePortal(http.Controller):
    """D-29: las partes retiran o renuevan la página pública /t/<token>
    desde la compra (/marketplace/purchases/<ref>/traceability)."""

    def _trace_tx(self, tx_ref):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        actor = request.env.user.shrimp_actor_partner_id
        if not tx or actor not in (tx.buyer_partner_id, tx.seller_partner_id):
            raise NotFound()
        return tx, actor

    def _back(self, tx, msg=None, error=None):
        url = "/marketplace/purchases/%s/traceability" % tx.uuid_ref
        if error:
            url += "?trace_error=%s" % quote(error)
        elif msg:
            url += "?trace_msg=%s" % quote(msg)
        return request.redirect(url + "#shrimp_trace_public")

    @http.route("/marketplace/purchases/<tx_ref>/traceability/withdraw", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def public_trace_withdraw(self, tx_ref, **post):
        tx, actor = self._trace_tx(tx_ref)
        try:
            tx.action_withdraw_public_trace(actor=actor)
        except (ValidationError, AccessError) as e:
            return self._back(tx, error=e.args[0] if e.args else str(e))
        return self._back(tx, msg=_("La página pública se retiró: los QR ya no la abren."))

    @http.route("/marketplace/purchases/<tx_ref>/traceability/renew", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def public_trace_renew(self, tx_ref, **post):
        tx, actor = self._trace_tx(tx_ref)
        try:
            tx.action_renew_public_trace(actor=actor)
        except (ValidationError, AccessError) as e:
            return self._back(tx, error=e.args[0] if e.args else str(e))
        return self._back(tx, msg=_("Enlace renovado: los QR anteriores dejaron de funcionar."))
