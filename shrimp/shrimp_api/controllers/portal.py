"""Páginas web: «Integraciones (API)» en /my, la trazabilidad pública /t/<token>
y el cierre del certificado personal que se servía sin sesión."""

import logging
from datetime import timedelta
from urllib.parse import quote

from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.shrimp_marketplace.controllers.marketplace import ShrimpMarketplacePublicController

from ..models.api_scope import SCOPES
from ..models.webhook import EVENTS

_logger = logging.getLogger(__name__)

EXPIRY_CHOICES = [(30, "30 días"), (90, "90 días"), (180, "180 días"), (365, "1 año")]
MAX_ACTIVE_KEYS = 10


class ShrimpApiPortal(http.Controller):

    # ------------------------------------------------------------------
    def _api_user_ok(self):
        user = request.env.user
        return user.share and not user._is_public()

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
            "scopes": SCOPES,
            "expiry_choices": EXPIRY_CHOICES,
            "allowed": self._api_user_ok(),
            "error": extra.pop("error", None),
            "secret": extra.pop("secret", None),
            "created": extra.pop("created", None),
        }
        values.update(extra)
        return self._no_store(request.render("shrimp_api.portal_api_keys", values))

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
        active = self._keys().filtered(lambda k: k.status == "active")
        if len(active) >= MAX_ACTIVE_KEYS:
            return self._render_keys(error=_("Tienes %s claves activas: revoca alguna antes de crear otra.")
                                     % MAX_ACTIVE_KEYS)
        form = request.httprequest.form
        codes = [c for c in form.getlist("scopes") if c in dict(SCOPES)]
        try:
            days = int(post.get("expiry_days") or 90)
        except ValueError:
            days = 90
        days = min(max(days, 1), 365)
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

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    def _subs(self):
        return request.env["shrimp.webhook.subscription"].sudo().with_context(active_test=False).search(
            [("partner_id", "=", request.env.user.partner_id.id)], order="id desc")

    def _render_webhooks(self, **extra):
        installed = request.env.registry._init_modules
        events = [(code, spec.label) for code, spec in sorted(EVENTS.items())
                  if code != "ping" and (code != "invoice.authorized" or "shrimp_api_sri" in installed)]
        values = {
            "page_name": "api_webhooks",
            "subs": self._subs(),
            "events": events,
            "allowed": self._api_user_ok(),
            "error": extra.pop("error", None),
            "secret": extra.pop("secret", None),
            "created": extra.pop("created", None),
            "message": extra.pop("message", None),
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
                    "partner_id": request.env.user.partner_id.id,
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
        else:
            sub.action_reactivate()
        return request.redirect("/my/webhooks")

    @http.route("/my/webhooks/<string:ref>/ping", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def my_webhook_ping(self, ref, **post):
        self._my_sub(ref).action_ping()
        return request.redirect("/my/webhooks?message=%s" % quote(_("Ping encolado.")))

    # ------------------------------------------------------------------
    # Trazabilidad pública (destino del QR)
    # ------------------------------------------------------------------
    @http.route("/t/<string:token>", type="http", auth="public", website=True, sitemap=False)
    def public_trace(self, token, **kw):
        if not token or len(token) < 16 or len(token) > 64:
            raise NotFound()
        tx = request.env["shrimp.transaction"].sudo().search([("trace_token", "=", token)], limit=1)
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
