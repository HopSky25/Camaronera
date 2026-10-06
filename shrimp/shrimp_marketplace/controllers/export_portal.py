"""Portal: registrar la salida / exportación de un lote y declarar la
producción real de un lote propio (laboratorio)."""

import base64

from werkzeug.exceptions import NotFound

from odoo import fields, http, _
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from odoo.addons.shrimp_user_registry.controllers.main import (
    current_partner, flash_message, read_upload)


class ShrimpExportPortal(http.Controller):

    def _own_lot(self, lot_ref, partner):
        lot = request.env["shrimp.stock.lot"].sudo().resolve_ref(lot_ref) if lot_ref else False
        if not lot or lot.owner_id != partner:
            raise NotFound()
        return lot

    def _exports_guard(self, partner):
        """Las rutas de salidas siguen la misma regla que el menú «Salidas» y
        el botón de «Mi inventario»: la capacidad «register_exports»."""
        if not partner.sudo()._shrimp_can_any("register_exports"):
            raise NotFound()

    # ------------------------------------------------------------------
    # Salidas
    # ------------------------------------------------------------------
    @http.route("/marketplace/exports", type="http", auth="user", website=True)
    def exports_list(self, **kw):
        partner = current_partner()
        self._exports_guard(partner)
        exports = request.env["shrimp.export"].sudo().search(
            [("partner_id", "=", partner.id)], order="date desc, id desc", limit=200)
        return request.render("shrimp_marketplace.export_list", {
            "partner": partner, "exports": exports,
            "saved": kw.get("saved"), "error": kw.get("error"), "message": kw.get("message"),
        })

    @http.route("/marketplace/exports/new", type="http", auth="user", website=True)
    def export_new(self, lot=None, **kw):
        partner = current_partner()
        self._exports_guard(partner)
        lots = request.env["shrimp.stock.lot"].sudo().search([
            ("owner_id", "=", partner.id), ("state", "=", "available"),
            ("available_qty", ">", 0)], order="id desc", limit=200)
        # Solo los lotes de los que se puede registrar una salida (camarón
        # adulto o empacado), con la misma regla que aplica el modelo.
        lots = lots.filtered(lambda l: l.shrimp_can_register_export(partner))
        selected = lots.filtered(lambda l: l.uuid_ref == lot)[:1] if lot else lots.browse()
        countries = request.env["res.country"].sudo().search([], order="name")
        return request.render("shrimp_marketplace.export_form", {
            "partner": partner, "lots": lots, "selected": selected, "countries": countries,
            "today": fields.Date.context_today(request.env.user),
            "error": kw.get("error"), "message": kw.get("message"),
        })

    @http.route("/marketplace/exports/register", type="http", auth="user", website=True,
                methods=["POST"], csrf=True)
    def export_register(self, **post):
        partner = current_partner()
        self._exports_guard(partner)
        lot = self._own_lot(post.get("lot_ref"), partner)
        try:
            qty = float((post.get("qty") or "0").replace(",", "."))
            boxes = int(post.get("boxes") or 0)
            price = float((post.get("price_unit") or "0").replace(",", ".") or 0)
        except (TypeError, ValueError):
            return request.redirect("/marketplace/exports/new?lot=%s&error=validation&message=%s"
                                    % (lot.uuid_ref, flash_message(_("Revisa las cantidades."))))
        country = False
        if post.get("country_code"):
            country = request.env["res.country"].sudo().search(
                [("code", "=", post["country_code"].strip().upper()[:2])], limit=1)
        vals = {
            "date": post.get("date") or fields.Date.context_today(request.env.user),
            "destination_buyer": (post.get("destination_buyer") or "").strip()[:200] or False,
            "destination_country_id": country.id if country else False,
            "destination_place": (post.get("destination_place") or "").strip()[:200] or False,
            "dae_number": (post.get("dae_number") or "").strip()[:60] or False,
            "invoice_number": (post.get("invoice_number") or "").strip()[:60] or False,
            "container": (post.get("container") or "").strip()[:60] or False,
            "boxes": max(0, boxes),
            "price_unit": max(0.0, price),
            "confidential": bool(post.get("confidential")),
            "notes": (post.get("notes") or "").strip() or False,
        }
        try:
            # Savepoint: si la salida no se registra, tampoco quedan sus adjuntos.
            with request.env.cr.savepoint():
                adjuntos = []
                for f in request.httprequest.files.getlist("attachments"):
                    content, mime, nombre = read_upload(f)
                    if content:
                        adjuntos.append(request.env["ir.attachment"].sudo().create({
                            "name": nombre, "datas": base64.b64encode(content), "mimetype": mime,
                            "res_model": "res.partner", "res_id": partner.id, "public": False}).id)
                if adjuntos:
                    vals["attachment_ids"] = [(6, 0, adjuntos)]
                exp = request.env["shrimp.export"].sudo().shrimp_register(partner, vals, [(lot, qty)])
                for att in exp.attachment_ids:
                    att.write({"res_model": "shrimp.export", "res_id": exp.id})
        except (ValidationError, AccessError) as e:
            return request.redirect("/marketplace/exports/new?lot=%s&error=validation&message=%s"
                                    % (lot.uuid_ref, flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/exports?saved=%s" % exp.uuid_ref)

    @http.route("/marketplace/exports/<export_ref>/cancel", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def export_cancel(self, export_ref, **post):
        partner = current_partner()
        exp = request.env["shrimp.export"].sudo().resolve_ref(export_ref)
        if not exp or exp.partner_id != partner:
            raise NotFound()
        if exp.state != "cancelled":
            exp.action_cancel(reason=(post.get("reason") or "").strip() or _("Anulada por el titular"),
                              actor=partner)
        return request.redirect("/marketplace/exports?saved=cancelled")

    # ------------------------------------------------------------------
    # Producción real declarada (laboratorio)
    # ------------------------------------------------------------------
    @http.route("/marketplace/my-lots/<lot_ref>/production", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def lot_declare_production(self, lot_ref, **post):
        partner = current_partner()
        lot = self._own_lot(lot_ref, partner)
        if lot.product_id.seller_partner_id != partner:
            raise NotFound()
        try:
            qty = float((post.get("actual_qty") or "").replace(",", "."))
        except (TypeError, ValueError):
            return request.redirect("/marketplace/my-lots?error=validation&message=%s"
                                    % flash_message(_("Indica la cantidad real producida.")))
        try:
            lot.product_id.sudo().action_declare_production(
                qty, reason=(post.get("reason") or "").strip() or None, actor=partner)
        except (ValidationError, AccessError) as e:
            return request.redirect("/marketplace/my-lots?error=validation&message=%s"
                                    % flash_message(e.args[0] if e.args else ""))
        return request.redirect("/marketplace/my-lots?saved=production")
