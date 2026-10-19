"""Portal: «Notificar retraso de entrega», respuesta del comprador, respuesta
del vendedor a la contrapropuesta y «Cancelar compra» sin costo
(models/shrimp_transaction_delay.py). También aplica los strikes al
catálogo: fuera los vendedores suspendidos y al final los penalizados.

Todas las acciones son POST con CSRF y vuelven a la página de la compra
(#retraso) con ?saved=… o ?error=delay&message=….
"""
from odoo import fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.tools import ustr
from werkzeug.exceptions import Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import current_partner, flash_message

from .marketplace import ShrimpMarketplacePublicController
from .transaction_portal import ShrimpTransactionPortalController as _TxPortal
from ..models.shrimp_transaction_delay import shrimp_delay_ready


def _pagina(tx):
    return "/marketplace/purchases/%s/traceability#retraso" % tx.uuid_ref


class ShrimpDelayPortal(http.Controller):

    def _tx_of(self, tx_ref, side):
        partner = current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        dueno = tx.seller_partner_id if side == "seller" else tx.buyer_partner_id if tx else None
        if not tx or not partner or dueno.id != partner.id:
            raise Forbidden()
        return tx, partner

    def _run(self, tx, post, saved, fn):
        volver = _TxPortal._reception_back(post, _pagina(tx))
        try:
            with request.env.cr.savepoint():
                fn()
        except (ValidationError, AccessError) as e:
            msg = e.args[0] if e.args else ustr(e)
            return request.redirect(_TxPortal._with_params(volver, error="delay",
                                                           message=flash_message(msg)))
        return request.redirect(_TxPortal._with_params(volver, saved=saved))

    @staticmethod
    def _fecha(valor):
        try:
            return fields.Date.to_date((valor or "").strip()) if valor else False
        except (TypeError, ValueError):
            return False

    # ---------------------------------------------------------------- vendedor
    @http.route("/marketplace/sales/<tx_ref>/delay", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_notify_delay(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "seller")
        return self._run(tx, post, "delay_notified", lambda: tx.action_notify_delay(
            self._fecha(post.get("new_date")), post.get("reason"), note=post.get("note"),
            actor=partner))

    @http.route("/marketplace/sales/<tx_ref>/delay/accept", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_delay_seller_accept(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "seller")
        return self._run(tx, post, "delay_counter_accepted",
                         lambda: tx.action_delay_seller_accept(actor=partner))

    @http.route("/marketplace/sales/<tx_ref>/delay/reject", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_delay_seller_reject(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "seller")
        return self._run(tx, post, "delay_counter_rejected",
                         lambda: tx.action_delay_seller_reject(actor=partner))

    # --------------------------------------------------------------- comprador
    @http.route("/marketplace/purchases/<tx_ref>/delay/accept", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_delay_buyer_accept(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "buyer")
        return self._run(tx, post, "delay_accepted",
                         lambda: tx.action_delay_buyer_accept(actor=partner))

    @http.route("/marketplace/purchases/<tx_ref>/delay/counter-offer", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_delay_buyer_counter(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "buyer")
        return self._run(tx, post, "delay_countered", lambda: tx.action_delay_buyer_counter(
            self._fecha(post.get("counter_date")), note=post.get("note"), actor=partner))

    @http.route("/marketplace/purchases/<tx_ref>/cancel", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_cancel_for_delay(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "buyer")
        # Servicio único (shrimp_transaction_cancel.py): retraso, no entrega
        # y verificación vencida.
        return self._run(tx, post, "delay_cancelled",
                         lambda: tx.shrimp_cancel("buyer", reason=post.get("note"), actor=partner))


class ShrimpMarketplaceStrikes(ShrimpMarketplacePublicController):
    """Catálogo: los vendedores con la publicación suspendida no salen y los
    penalizados (strikes del umbral de aviso) van al final del orden
    recomendado / por calificación y no entran en el «Top 10»."""

    def _build_public_marketplace_domain(self):
        domain = super()._build_public_marketplace_domain()
        suspendidos = request.env["res.partner"].sudo()._shrimp_suspended_ids()
        if suspendidos:
            domain = domain + [("seller_partner_id", "not in", suspendidos)]
        return domain

    def _search_marketplace_products(self, kw):
        products, filters, sort, best_price_id = super()._search_marketplace_products(kw)
        if not products or not shrimp_delay_ready(request.env):
            return products, filters, sort, best_price_id
        Product = request.env["shrimp.product"].sudo()
        if kw.get("best"):
            castigados = request.env["res.partner"].sudo()._shrimp_penalized_ids(
                set(products.mapped("seller_partner_id").ids))
            if castigados:
                products = products.filtered(lambda p: p.seller_partner_id.id not in castigados)
        elif sort in ("recommended", "rating", "survival"):
            products = Product.shrimp_rank_with_strikes(products)
        return products, filters, sort, best_price_id
