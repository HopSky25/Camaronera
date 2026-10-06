# -*- coding: utf-8 -*-
"""Ficha de calidad del lote y «Registrar muestreo» (portal del vendedor).

Solo el dueño del lote (o un usuario interno) crea, edita o borra fichas; el
lote y la ficha se resuelven por su código (uuid_ref), nunca por id. El
informe PDF se valida por contenido y tamaño (5 MB) con el mismo lector que
el resto de subidas de la plataforma, y se sirve por la ruta del producto a
quien puede ver el lote.
"""
import base64

from odoo import fields, http, _
from odoo.exceptions import ValidationError
from odoo.http import request
from werkzeug.exceptions import Forbidden, NotFound

from odoo.addons.shrimp_user_registry.controllers.main import (
    binary_headers, current_partner, flash_message, pop_message, read_upload, to_number)

from ..models.shrimp_product_quality import PCR_FIELDS, PCR_RESULTS

PDF_ONLY = {"application/pdf"}


class ShrimpQualityPortal(http.Controller):

    # ------------------------------------------------------------------
    # Acceso
    # ------------------------------------------------------------------
    @staticmethod
    def _is_internal():
        return request.env.user.has_group("base.group_user")

    def _owned_product(self, product_ref):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()
        partner = current_partner()
        if product.seller_partner_id != partner and not self._is_internal():
            raise Forbidden()
        return product

    @staticmethod
    def _quality_of(product, quality_ref):
        ficha = request.env["shrimp.product.quality"].sudo().resolve_ref(quality_ref) if quality_ref else None
        if not ficha or ficha.product_id != product:
            raise NotFound()
        return ficha

    @staticmethod
    def _base(product):
        return "/marketplace/products/%s/quality" % product.uuid_ref

    # ------------------------------------------------------------------
    # Pantalla
    # ------------------------------------------------------------------
    @http.route("/marketplace/products/<product_ref>/quality", type="http", auth="user", website=True,
                sitemap=False)
    def quality_page(self, product_ref, **kw):
        product = self._owned_product(product_ref)
        Quality = request.env["shrimp.product.quality"].sudo()
        editing = Quality.browse()
        if kw.get("ficha"):
            editing = self._quality_of(product, kw.get("ficha"))
        stages = request.env["shrimp.stage"].sudo().search([("active", "=", True)]).filtered(
            lambda s: s._shrimp_tipo() in ("nauplio", "larva"))
        return request.render("shrimp_marketplace.product_quality_page", {
            "product": product,
            "sheets": Quality.search([("product_id", "=", product.id)]),
            "editing": editing,
            "stages": stages,
            "pcr_fields": PCR_FIELDS,
            "pcr_results": PCR_RESULTS,
            "origin_lots": Quality._origin_lot_options(product.seller_partner_id),
            "accepts": product._shrimp_accepts_quality(),
            "today": fields.Date.context_today(request.env.user),
            "error_message": pop_message(kw.get("error")),
            "saved": kw.get("saved"),
        })

    # ------------------------------------------------------------------
    # Guardar / borrar
    # ------------------------------------------------------------------
    def _quality_vals(self, product, post):
        resultados = {c for c, _l in PCR_RESULTS}
        vals = {
            "sample_date": fields.Date.to_date(post.get("sample_date")) if post.get("sample_date") else
            fields.Date.context_today(request.env.user),
            "stress_test_survival": to_number(post.get("stress_test_survival"), 0.0),
            "uniformity": to_number(post.get("uniformity"), 0.0),
            "tank": (post.get("tank") or "").strip()[:80] or False,
            "analysis_lab": (post.get("analysis_lab") or "").strip()[:120] or False,
            "notes": (post.get("notes") or "").strip()[:2000] or False,
        }
        for fname, _label in PCR_FIELDS:
            valor = (post.get(fname) or "no_realizado").strip()
            vals[fname] = valor if valor in resultados else "no_realizado"
        stage = request.env["shrimp.stage"].sudo().resolve_ref(post.get("stage")) if post.get("stage") else None
        vals["stage_id"] = stage.id if stage else False
        lote = request.env["shrimp.stock.lot"].sudo().resolve_ref(post.get("origin_lot")) \
            if post.get("origin_lot") else None
        # Solo lotes propios del vendedor del producto (el modelo también lo valida).
        vals["origin_lot_id"] = lote.id if lote and lote.owner_id == product.seller_partner_id else False
        return vals

    @http.route("/marketplace/products/<product_ref>/quality/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def quality_save(self, product_ref, **post):
        product = self._owned_product(product_ref)
        Quality = request.env["shrimp.product.quality"].sudo()
        ficha = self._quality_of(product, post.get("ficha")) if post.get("ficha") else Quality.browse()
        try:
            vals = self._quality_vals(product, post)
            contenido, mime, nombre = read_upload(request.httprequest.files.get("report_file"),
                                                  allowed=PDF_ONLY)
            if ficha:
                ficha.write(vals)
            else:
                ficha = Quality.create(dict(vals, product_id=product.id))
            if contenido:
                att = request.env["ir.attachment"].sudo().create({
                    "name": nombre, "type": "binary", "datas": base64.b64encode(contenido),
                    "mimetype": mime, "res_model": "shrimp.product.quality", "res_id": ficha.id,
                    "public": False,
                })
                att.generate_access_token()
                ficha.attachment_id = att.id
            if post.get("register_sample"):
                ficha.action_register_sample()
        except (ValidationError, ValueError) as e:
            return request.redirect("%s?error=%s" % (self._base(product), flash_message(
                e.args[0] if getattr(e, "args", None) else str(e))))
        return request.redirect("%s?saved=1" % self._base(product))

    @http.route("/marketplace/products/<product_ref>/quality/<quality_ref>/delete", type="http",
                auth="user", website=True, methods=["POST"], csrf=True, sitemap=False)
    def quality_delete(self, product_ref, quality_ref, **post):
        product = self._owned_product(product_ref)
        ficha = self._quality_of(product, quality_ref)
        ficha.attachment_id.sudo().unlink()
        ficha.unlink()
        return request.redirect("%s?saved=deleted" % self._base(product))

    @http.route("/marketplace/products/<product_ref>/quality/<quality_ref>/sampling", type="http",
                auth="user", website=True, methods=["POST"], csrf=True, sitemap=False)
    def quality_register_sample(self, product_ref, quality_ref, **post):
        product = self._owned_product(product_ref)
        self._quality_of(product, quality_ref).action_register_sample()
        return request.redirect("%s?saved=sample" % self._base(product))

    # ------------------------------------------------------------------
    # «Registrar muestreo» rápido (sin ficha completa)
    # ------------------------------------------------------------------
    @http.route("/marketplace/products/<product_ref>/sampling", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def product_register_sample(self, product_ref, **post):
        product = self._owned_product(product_ref)
        survival = to_number(post.get("survival_rate"), 0.0)
        size = to_number(post.get("avg_size_mg"), 0.0)
        if survival < 0 or survival > 100 or size < 0:
            return request.redirect("%s?error=%s" % (self._base(product), flash_message(
                _("La supervivencia va de 0 a 100 % y el tamaño no puede ser negativo."))))
        stage = request.env["shrimp.stage"].sudo().resolve_ref(post.get("stage")) if post.get("stage") else None
        request.env["shrimp.product.evolution"].sudo().create({
            "product_id": product.id,
            "date": fields.Datetime.now(),
            "stage_id": (stage or product.stage_id).id or False,
            "survival_rate": survival or product.survival_rate,
            "avg_size_mg": size or product.avg_size_mg,
            "available_qty": product.available_qty,
            "health_status": (post.get("health_status") or "").strip()[:500] or False,
            "note": (post.get("note") or "").strip()[:500] or _("Muestreo registrado por el vendedor."),
        })
        return request.redirect("%s?saved=sample" % self._base(product))

    # ------------------------------------------------------------------
    # Informe PDF (comprador y público que puede ver el lote)
    # ------------------------------------------------------------------
    @http.route("/marketplace/product/<product_ref>/quality/<quality_ref>/report", type="http",
                auth="public", website=True, sitemap=False)
    def quality_report_file(self, product_ref, quality_ref, **kw):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()
        partner = current_partner()
        es_dueno = bool(partner and product.seller_partner_id == partner)
        # Publicado o ya vendido (lo enlaza la trazabilidad de los lotes que
        # nacieron de él); los borradores y cancelados, solo dueño e internos.
        if product.state not in ("published", "sold") and not (es_dueno or self._is_internal()):
            raise NotFound()
        ficha = self._quality_of(product, quality_ref)
        att = ficha.attachment_id.sudo()
        if not att or not att.datas:
            raise NotFound()
        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype or "application/pdf", att.name or "ficha-calidad.pdf",
            download=bool(kw.get("download"))))
