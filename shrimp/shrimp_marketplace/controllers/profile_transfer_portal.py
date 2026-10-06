# -*- coding: utf-8 -*-
"""Portal: «Mover a otro perfil» (transferencia interna entre los perfiles de
la misma cuenta) desde Mi inventario y desde la ficha del producto.

Las reglas viven en el modelo (shrimp_profile_transfer.py); aquí solo se
comprueba que el lote / producto es de la cuenta que pide, se llama al método
de negocio y se vuelve a la página con el resultado.
"""
from werkzeug.exceptions import NotFound

from odoo import http, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.shrimp_user_registry.controllers.main import (
    current_partner, flash_message)

from .account_portal import ShrimpAccountPortalController
from .marketplace import ShrimpMarketplacePublicController


def _error_text(exc):
    return exc.args[0] if getattr(exc, "args", None) else str(exc)


def _pt_feedback(kw):
    """(texto ok, texto error) de la redirección anterior. El parámetro
    `message` ya llega saneado por ir.http (el texto que el servidor guardó en
    la sesión con flash_message, nunca texto libre de la URL)."""
    estado = kw.get("pt")
    texto = kw.get("message") if isinstance(kw.get("message"), str) else None
    if estado == "ok":
        return texto or _("Movimiento entre perfiles registrado."), None
    if estado == "error":
        return None, texto or _("No se pudo mover el producto.")
    return None, None


class ShrimpProfileTransferLots(ShrimpAccountPortalController):

    @http.route()
    def my_lots(self, **kw):
        res = super().my_lots(**kw)
        partner = current_partner()
        ok, error = _pt_feedback(kw)
        if getattr(res, "qcontext", None) is not None:
            res.qcontext.update({
                "pt_transfers": request.env["shrimp.profile.transfer"]._shrimp_for_partner(partner),
                "pt_ok": ok,
                "pt_error": error,
            })
        return res

    @http.route("/marketplace/my-lots/<lot_ref>/move-profile", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def lot_move_profile(self, lot_ref, **post):
        partner = current_partner()
        lot = request.env["shrimp.stock.lot"].sudo().resolve_ref(lot_ref)
        if not lot or lot.owner_id != partner:
            raise NotFound()
        try:
            with request.env.cr.savepoint():
                lot.action_transfer_profile(
                    post.get("to_role"), qty=post.get("qty") or None,
                    reason=post.get("reason"), actor=partner)
        except (ValidationError, UserError, AccessError) as e:
            return request.redirect("/marketplace/my-lots?pt=error&message=%s"
                                    % flash_message(_error_text(e)))
        return request.redirect("/marketplace/my-lots?pt=ok")


class ShrimpProfileTransferProduct(ShrimpMarketplacePublicController):

    @http.route()
    def marketplace_product_detail(self, product_ref, **kwargs):
        res = super().marketplace_product_detail(product_ref, **kwargs)
        qctx = getattr(res, "qcontext", None)
        if qctx is not None and qctx.get("product"):
            product = qctx["product"]
            ok, error = _pt_feedback(kwargs)
            targets = []
            transfers = request.env["shrimp.profile.transfer"]
            if qctx.get("is_owner"):
                targets = product.shrimp_profile_move_targets()
                transfers = transfers.sudo().search([
                    "|", ("product_id", "=", product.id),
                    ("target_product_id", "=", product.id)], limit=50)
            qctx.update({
                "pt_targets": targets,
                "pt_change_targets": [c for c, _l in product._shrimp_profile_change_targets()]
                if qctx.get("is_owner") else [],
                "pt_transfers": transfers,
                "pt_ok": ok,
                "pt_error": error,
            })
        return res

    @http.route("/marketplace/products/<product_ref>/move-profile", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def product_move_profile(self, product_ref, **post):
        partner = current_partner()
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or product.seller_partner_id != partner:
            raise NotFound()
        url = "/marketplace/product/%s" % product.uuid_ref
        try:
            with request.env.cr.savepoint():
                if post.get("mode") == "qty":
                    product.action_transfer_qty_to_profile(
                        post.get("to_role"), post.get("qty"), reason=post.get("reason"),
                        actor=partner)
                else:
                    product.action_change_profile(
                        post.get("to_role"), reason=post.get("reason"), actor=partner)
        except (ValidationError, UserError, AccessError) as e:
            return request.redirect("%s?pt=error&message=%s" % (url, flash_message(_error_text(e))))
        return request.redirect("%s?pt=ok" % url)
