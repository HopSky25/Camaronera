"""Portal: cancelación de la compra (models/shrimp_transaction_cancel.py).

  POST /marketplace/sales/<tx>/cancel                  vendedor (antes del despacho)
  POST /marketplace/purchases/<tx>/cancel/request      cualquiera de las partes:
                                                       pide cancelar de mutuo acuerdo
  POST /marketplace/purchases/<tx>/cancel/request/accept    la otra parte acepta
  POST /marketplace/purchases/<tx>/cancel/request/reject    la otra parte rechaza
  POST /marketplace/purchases/<tx>/cancel/request/withdraw  quien pidió la retira
  POST /marketplace/sales/<tx>/strike/<id>/appeal     vendedor: «Solicitar anulación»
                                                       de un strike de esa venta
                                                       (justificación + evidencia)

La cancelación sin costo del comprador sigue en POST
/marketplace/purchases/<tx>/cancel (delay_portal.py), que ahora pasa por el
servicio único (retraso, no entrega y verificación vencida).

Todas son POST con CSRF y vuelven a la página de la compra (#cancelacion)
con ?saved=… o ?error=cancel&message=….
"""
from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.tools import ustr
from werkzeug.exceptions import Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import current_partner, flash_message

from .transaction_portal import ShrimpTransactionPortalController as _TxPortal


def _pagina(tx):
    return "/marketplace/purchases/%s/traceability#cancelacion" % tx.uuid_ref


class ShrimpCancelPortal(http.Controller):

    def _tx_of(self, tx_ref, side=None):
        partner = current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx or not partner:
            raise Forbidden()
        partes = {"seller": tx.seller_partner_id, "buyer": tx.buyer_partner_id}
        if side:
            if partes[side].id != partner.id:
                raise Forbidden()
        elif partner.id not in (tx.seller_partner_id.id, tx.buyer_partner_id.id):
            raise Forbidden()
        return tx, partner

    def _run(self, tx, post, saved, fn):
        volver = _TxPortal._reception_back(post, _pagina(tx))
        try:
            with request.env.cr.savepoint():
                fn()
        except (ValidationError, AccessError) as e:
            msg = e.args[0] if e.args else ustr(e)
            return request.redirect(_TxPortal._with_params(volver, error="cancel",
                                                           message=flash_message(msg)))
        return request.redirect(_TxPortal._with_params(volver, saved=saved))

    @http.route("/marketplace/sales/<tx_ref>/cancel", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_seller_cancel(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref, "seller")
        return self._run(tx, post, "cancel_seller", lambda: tx.shrimp_cancel(
            "seller", reason=post.get("reason"), actor=partner))

    @http.route("/marketplace/purchases/<tx_ref>/cancel/request", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_cancel_request(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref)
        return self._run(tx, post, "cancel_requested", lambda: tx.action_cancel_request(
            post.get("reason"), actor=partner))

    @http.route("/marketplace/purchases/<tx_ref>/cancel/request/accept", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_cancel_request_accept(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref)
        return self._run(tx, post, "cancel_accepted", lambda: tx.action_cancel_request_respond(
            True, note=post.get("note"), actor=partner))

    @http.route("/marketplace/purchases/<tx_ref>/cancel/request/reject", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_cancel_request_reject(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref)
        return self._run(tx, post, "cancel_rejected", lambda: tx.action_cancel_request_respond(
            False, note=post.get("note"), actor=partner))

    @http.route("/marketplace/purchases/<tx_ref>/cancel/request/withdraw", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_cancel_request_withdraw(self, tx_ref, **post):
        tx, partner = self._tx_of(tx_ref)
        return self._run(tx, post, "cancel_withdrawn",
                         lambda: tx.action_cancel_request_withdraw(actor=partner))

    @http.route("/marketplace/sales/<tx_ref>/strike/<int:strike_id>/appeal", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_strike_appeal(self, tx_ref, strike_id, **post):
        tx, partner = self._tx_of(tx_ref, "seller")
        strike = request.env["shrimp.seller.strike"].sudo().browse(strike_id).exists()
        if not strike or strike.transaction_id != tx:
            raise Forbidden()
        evidencia = _TxPortal()._reception_photo()
        return self._run(tx, post, "strike_appeal", lambda: strike.action_request_annulment(
            post.get("reason"), evidence=evidencia, actor=partner))
