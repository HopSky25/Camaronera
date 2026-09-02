from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError
from werkzeug.exceptions import NotFound, Forbidden


class ShrimpClientPricePortal(http.Controller):
    """Gestión de precios por cliente desde el portal del vendedor."""

    def _current_partner(self):
        return request.env.user.partner_id

    def _is_seller(self):
        partner = self._current_partner()
        return (partner.shrimp_user_type in ("semillero", "laboratorio")
                or request.env.user.has_group("base.group_user"))

    def _seller_partner(self):
        """Vendedor efectivo: el propio partner si es semillero/lab; los internos
        pueden gestionar cualquiera (se usa su propio partner por defecto)."""
        return self._current_partner()

    def _client_options(self, seller):
        # Clientes potenciales: partners del marketplace, distintos del vendedor.
        return request.env["res.partner"].sudo().search([
            ("shrimp_user_type", "in", ["semillero", "laboratorio", "camaronera"]),
            ("id", "!=", seller.id),
        ], order="name asc")

    def _product_options(self, seller):
        return request.env["shrimp.product"].sudo().search([
            ("seller_partner_id", "=", seller.id),
            ("active", "=", True),
        ], order="name asc")

    @http.route("/marketplace/precios", type="http", auth="user", website=True)
    def client_prices(self, **kw):
        if not self._is_seller():
            raise Forbidden()
        seller = self._seller_partner()
        prices = request.env["shrimp.client.price"].sudo().search([
            ("seller_partner_id", "=", seller.id),
        ], order="client_partner_id, product_id")
        return request.render("shrimp_marketplace.client_prices_page", {
            "prices": prices,
            "client_options": self._client_options(seller),
            "product_options": self._product_options(seller),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/precios/nuevo", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def client_price_create(self, **post):
        if not self._is_seller():
            raise Forbidden()
        seller = self._seller_partner()
        try:
            client_id = int(post.get("client_partner_id") or 0)
            product_id = int(post.get("product_id") or 0)
            price = float(post.get("price") or 0.0)
        except (TypeError, ValueError):
            return request.redirect("/marketplace/precios?error=datos")

        if not client_id or not product_id:
            return request.redirect("/marketplace/precios?error=datos")

        product = request.env["shrimp.product"].sudo().browse(product_id)
        if not product.exists() or product.seller_partner_id.id != seller.id:
            return request.redirect("/marketplace/precios?error=producto")

        existing = request.env["shrimp.client.price"].sudo().search([
            ("client_partner_id", "=", client_id),
            ("product_id", "=", product_id),
        ], limit=1)
        try:
            if existing:
                existing.write({"price": price, "active": True})
            else:
                request.env["shrimp.client.price"].sudo().create({
                    "seller_partner_id": seller.id,
                    "client_partner_id": client_id,
                    "product_id": product_id,
                    "price": price,
                })
        except ValidationError as e:
            msg = e.args[0] if e.args else _("No se pudo guardar el precio.")
            return request.redirect(f"/marketplace/precios?error=validation&message={msg}")

        return request.redirect("/marketplace/precios?message=ok")

    @http.route("/marketplace/precios/<price_ref>/eliminar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def client_price_delete(self, price_ref, **post):
        if not self._is_seller():
            raise Forbidden()
        seller = self._seller_partner()
        rec = request.env["shrimp.client.price"].sudo().resolve_ref(price_ref)
        if not rec:
            raise NotFound()
        if rec.seller_partner_id.id != seller.id and not request.env.user.has_group("base.group_user"):
            raise Forbidden()
        rec.unlink()
        return request.redirect("/marketplace/precios?message=deleted")
