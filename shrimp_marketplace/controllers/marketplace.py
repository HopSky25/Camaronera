import base64
import json
from urllib.parse import urlencode

from odoo import http, fields
from odoo.http import request
from werkzeug.exceptions import NotFound
from odoo.addons.website.controllers.main import Website


class ShrimpMarketplacePublicController(http.Controller):

    def _get_current_partner(self):
        user = request.env.user
        public_user = request.env.ref("base.public_user")
        if not user or user == public_user:
            return False
        return user.partner_id

    def _can_see_product(self, product):
        user = request.env.user
        partner = self._get_current_partner()

        is_owner = bool(partner and product.seller_partner_id.id == partner.id)
        is_internal = bool(user and user.id and user.has_group("base.group_user"))

        if product.state == "published":
            return True, is_owner, is_internal

        return (is_owner or is_internal), is_owner, is_internal

    def _build_public_marketplace_domain(self):
        current_partner = self._get_current_partner()
        current_user_type = current_partner.shrimp_user_type if current_partner else False

        domain = [
            ("active", "=", True),
            ("state", "=", "published"),
            ("available_qty", ">", 0),
        ]

        # El camarón adulto (vendido por camaroneras) es visible para cualquier
        # comprador registrado, además de la cadena tradicional de larvas.
        cam = [("seller_partner_id.shrimp_user_type", "=", "camaronera")]

        if current_user_type == "laboratorio":
            # larvas de semillero + camarón de camaronera
            domain += ["|", ("seller_partner_id.shrimp_user_type", "=", "semillero")] + cam
        elif current_user_type == "camaronera":
            # larvas de laboratorio + camarón de camaronera
            domain += ["|", ("seller_partner_id.shrimp_user_type", "=", "laboratorio")] + cam
        elif current_user_type == "semillero":
            # el semillero no compra larvas, pero sí puede comprar camarón
            domain += cam
        # público / sin tipo: ve todo el catálogo publicado

        return domain

    PAGE_SIZE = 20

    def _search_marketplace_products(self, kw):
        """Devuelve (products_ordenados, filtros_norm, sort, best_price_id)."""
        product_model = request.env["shrimp.product"].sudo()

        q = (kw.get("q") or "").strip()
        stage = (kw.get("stage") or "").strip()
        species = (kw.get("species") or "").strip()
        presentation = (kw.get("presentation") or "").strip()
        size_grade = (kw.get("size_grade") or "").strip()
        location = (kw.get("location") or "").strip()
        seller = (kw.get("seller") or "").strip()
        price_min = (kw.get("price_min") or "").strip()
        price_max = (kw.get("price_max") or "").strip()

        domain = self._build_public_marketplace_domain()

        if q:
            domain += ["|", "|",
                ("name", "ilike", q),
                ("species_id.name", "ilike", q),
                ("location", "ilike", q),
            ]

        if stage:
            try:
                domain.append(("stage_id", "=", int(stage)))
            except (TypeError, ValueError):
                pass
        if species:
            domain.append(("species_id.name", "ilike", species))
        if presentation in ("entero", "cola"):
            domain.append(("presentation", "=", presentation))
        if size_grade:
            try:
                domain.append(("size_grade_id", "=", int(size_grade)))
            except (TypeError, ValueError):
                pass
        if location:
            domain.append(("location", "ilike", location))
        if seller:
            domain.append(("seller_partner_id.name", "ilike", seller))

        if price_min:
            try:
                domain.append(("price", ">=", float(price_min)))
            except Exception:
                pass
        if price_max:
            try:
                domain.append(("price", "<=", float(price_max)))
            except Exception:
                pass

        products = product_model.search(domain, order="create_date desc")

        # ---- Ordenamiento / recomendaciones (#5) ----
        sort = (kw.get("sort") or "recommended").strip()
        if sort == "price_asc":
            products = products.sorted(key=lambda p: p.price)
        elif sort == "price_desc":
            products = products.sorted(key=lambda p: p.price, reverse=True)
        elif sort == "rating":
            products = products.sorted(
                key=lambda p: p.seller_partner_id.shrimp_rating_avg or 0.0, reverse=True)
        elif sort == "recent":
            pass  # ya viene por create_date desc
        else:  # recommended: mejor calificados primero y, a igualdad, más barato
            sort = "recommended"
            products = products.sorted(
                key=lambda p: (-(p.seller_partner_id.shrimp_rating_avg or 0.0), p.price))

        best_price_id = min(products, key=lambda p: p.price).id if products else False

        filters = {
            "q": q, "stage": stage, "species": species,
            "presentation": presentation, "size_grade": size_grade,
            "location": location,
            "seller": seller, "price_min": price_min, "price_max": price_max,
        }
        return products, filters, sort, best_price_id

    # Cuántas opciones muestra el botón "Top 10 mejores".
    BEST_LIMIT = 10

    @http.route("/marketplace", type="http", auth="public", website=True)
    def marketplace_list(self, **kw):
        product_model = request.env["shrimp.product"].sudo()
        products, filters, sort, best_price_id = self._search_marketplace_products(kw)

        is_best = bool(kw.get("best"))

        if is_best:
            # Las 10 mejores opciones: mejor calificación y, a igualdad, más barato.
            products = products.sorted(
                key=lambda p: (-(p.seller_partner_id.shrimp_rating_avg or 0.0), p.price))
            total_count = len(products)
            page = products[:self.BEST_LIMIT]
            has_more = False
            next_offset = len(page)
        else:
            total_count = len(products)
            page = products[:self.PAGE_SIZE]
            has_more = total_count > self.PAGE_SIZE
            next_offset = self.PAGE_SIZE

        stage_options = request.env["shrimp.stage"].sudo().search([("active", "=", True)], order="sequence asc, name asc")
        size_grade_options = request.env["shrimp.size.grade"].sudo().search(
            [("active", "=", True)], order="presentation asc, sequence asc, name asc")

        # Opciones para enriquecer el panel de filtros (basadas en el catálogo real)
        base_products = product_model.search(self._build_public_marketplace_domain())
        species_options = base_products.mapped("species_id")
        location_options = sorted({p.location for p in base_products if p.location})
        active_filters = len([v for v in filters.values() if v])

        # URL para quitar el modo Top 10 conservando los filtros actuales.
        _qs = {k: v for k, v in filters.items() if v}
        if sort and sort != "recommended":
            _qs["sort"] = sort
        all_url = ("/marketplace?" + urlencode(_qs) + "#listado") if _qs else "/marketplace"

        cover_atts = request.env["ir.attachment"].sudo().browse([])
        for product in page:
            first = product.photo_attachment_ids[:1]
            if first:
                cover_atts |= first
        if cover_atts:
            cover_atts.generate_access_token()

        return request.render("shrimp_marketplace.marketplace_list", {
            "products": page,
            "products_count": total_count,
            "shown_count": len(page),
            "has_more": has_more,
            "next_offset": next_offset,
            "page_size": self.PAGE_SIZE,
            "filters": filters,
            "stage_options": stage_options,
            "species_options": species_options,
            "size_grade_options": size_grade_options,
            "location_options": location_options,
            "active_filters": active_filters,
            "sort": sort,
            "best_price_id": best_price_id,
            "is_best": is_best,
            "all_url": all_url,
        })

    @http.route("/marketplace/cards", type="http", auth="public", website=True, sitemap=False)
    def marketplace_cards(self, **kw):
        """Fragmento HTML con el siguiente bloque de tarjetas (para 'cargar más')."""
        products, filters, sort, best_price_id = self._search_marketplace_products(kw)

        try:
            offset = max(0, int(kw.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0

        total_count = len(products)
        batch = products[offset:offset + self.PAGE_SIZE]
        next_offset = offset + len(batch)
        has_more = total_count > next_offset

        cover_atts = request.env["ir.attachment"].sudo().browse([])
        for product in batch:
            first = product.photo_attachment_ids[:1]
            if first:
                cover_atts |= first
        if cover_atts:
            cover_atts.generate_access_token()

        html = request.env["ir.qweb"]._render("shrimp_marketplace.marketplace_product_cards", {
            "products": batch,
            "best_price_id": best_price_id,
        })

        payload = json.dumps({
            "html": html,
            "has_more": has_more,
            "next_offset": next_offset,
            "shown_count": next_offset,
            "total": total_count,
        })
        return request.make_response(payload, headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ])

    @http.route("/marketplace/product/<product_ref>", type="http", auth="public", website=True)
    def marketplace_product_detail(self, product_ref, **kwargs):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)

        if not product or not product.active:
            raise NotFound()

        can_see, is_owner, is_internal = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        photos = []
        for att in product.photo_attachment_ids:
            photos.append({
                "id": att.id,
                "name": att.name,
                "url": f"/marketplace/producto/{product.uuid_ref}/foto/{att.id}",
            })

        evolution_lines = request.env["shrimp.product.evolution"].sudo().search([
            ("product_id", "=", product.id),
        ], order="date desc, id desc")

        # ---- #18: quiénes compraron este producto y cuánto ----
        buyers = []
        buyers_total_qty = 0.0
        buyers_total_amount = 0.0
        if is_owner or is_internal:
            txs = request.env["shrimp.transaction"].sudo().search([
                ("product_id", "=", product.id),
                ("seller_partner_id", "=", product.seller_partner_id.id),
                ("state", "in", ["confirmed", "done"]),
            ])
            agg = {}
            for tx in txs:
                b = tx.buyer_partner_id
                if not b:
                    continue
                d = agg.setdefault(b.id, {
                    "id": b.id, "name": b.name, "qty": 0.0, "amount": 0.0, "count": 0,
                    "last_date": tx.create_date,
                })
                d["qty"] += tx.transaction_qty or 0.0
                d["amount"] += tx.amount_total or 0.0
                d["count"] += 1
                if tx.create_date and (not d["last_date"] or tx.create_date > d["last_date"]):
                    d["last_date"] = tx.create_date
            buyers = sorted(agg.values(), key=lambda x: x["amount"], reverse=True)
            buyers_total_qty = sum(b["qty"] for b in buyers)
            buyers_total_amount = sum(b["amount"] for b in buyers)

        # ---- #19: certificados del vendedor vigentes a la fecha de publicación ----
        publish_dt = product.published_date or product.create_date
        publish_date = publish_dt.date() if publish_dt else fields.Date.context_today(request.env.user)
        seller_certs = request.env["shrimp.user.certificate.line"].sudo().search([
            ("partner_id", "=", product.seller_partner_id.id),
            ("status", "=", "approved"),
        ])
        # ids de certificados ya adjuntos directamente al producto (para no duplicar)
        product_cert_ids = product.certificate_line_ids.mapped("source_user_certificate_line_id").ids
        valid_seller_certs = seller_certs.filtered(lambda c:
            c.id not in product_cert_ids
            and (not c.issue_date or c.issue_date <= publish_date)
            and (not c.expiry_date or c.expiry_date >= publish_date)
        )

        return request.render("shrimp_marketplace.product_detail", {
            "product": product,
            "photos": photos,
            "evolution_lines": evolution_lines,
            "is_owner": is_owner,
            "is_internal": is_internal,
            "buyers": buyers,
            "buyers_total_qty": buyers_total_qty,
            "buyers_total_amount": buyers_total_amount,
            "valid_seller_certs": valid_seller_certs,
            "publish_date": publish_date,
        })

    @http.route("/marketplace/producto/<product_ref>", type="http", auth="public", website=True)
    def marketplace_product_detail_legacy(self, product_ref, **kwargs):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product:
            raise NotFound()
        return request.redirect(f"/marketplace/product/{product.uuid_ref}")

    @http.route("/marketplace/producto/<product_ref>/foto/<int:attachment_id>", type="http", auth="public", website=True, sitemap=False)
    def marketplace_product_photo(self, product_ref, attachment_id, **kwargs):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)

        if not product or not product.active:
            raise NotFound()

        can_see, _, _ = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        att = request.env["ir.attachment"].sudo().browse(attachment_id)
        if not att.exists() or att.id not in product.photo_attachment_ids.ids:
            raise NotFound()

        if not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        mimetype = att.mimetype or "image/jpeg"

        return request.make_response(content, headers=[
            ("Content-Type", mimetype),
            ("Content-Length", str(len(content))),
            ("Cache-Control", "private, max-age=3600"),
        ])

    @http.route("/marketplace/product/<product_ref>/certificate/<int:attachment_id>", type="http", auth="public", website=True, sitemap=False)
    def marketplace_product_certificate(self, product_ref, attachment_id, **kwargs):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)

        if not product or not product.active:
            raise NotFound()

        can_see, _, _ = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        # Solo se pueden descargar adjuntos que pertenezcan a los certificados
        # de ESTE producto (evita el IDOR sobre cualquier ir.attachment de la BD).
        allowed_ids = set(product.cert_attachment_ids.ids)
        allowed_ids |= set(product.certificate_line_ids.mapped("attachment_id").ids)

        if attachment_id not in allowed_ids:
            raise NotFound()

        att = request.env["ir.attachment"].sudo().browse(attachment_id)
        if not att.exists() or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        filename = (att.name or "certificado").replace("/", "-").replace("\\", "-")

        # Por defecto se muestra en el navegador (inline); con ?download=1 se fuerza
        # la descarga.
        disposition = "attachment" if kwargs.get("download") else "inline"

        return request.make_response(content, headers=[
            ("Content-Type", att.mimetype or "application/octet-stream"),
            ("Content-Length", str(len(content))),
            ("Content-Disposition", f'{disposition}; filename="{filename}"'),
            ("Cache-Control", "private, max-age=0"),
        ])

    @http.route("/marketplace/product/<product_ref>/user-certificate/<int:line_id>", type="http", auth="public", website=True, sitemap=False)
    def marketplace_seller_certificate(self, product_ref, line_id, **kwargs):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()

        can_see, _, _ = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        # El certificado debe pertenecer al vendedor del producto y estar aprobado.
        line = request.env["shrimp.user.certificate.line"].sudo().browse(line_id)
        if (not line.exists()
                or line.partner_id.id != product.seller_partner_id.id
                or line.status != "approved"
                or not line.file_attachment_id):
            raise NotFound()

        att = line.file_attachment_id
        if not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        filename = (att.name or "certificado").replace("/", "-").replace("\\", "-")
        disposition = "attachment" if kwargs.get("download") else "inline"

        return request.make_response(content, headers=[
            ("Content-Type", att.mimetype or "application/octet-stream"),
            ("Content-Length", str(len(content))),
            ("Content-Disposition", f'{disposition}; filename="{filename}"'),
            ("Cache-Control", "private, max-age=0"),
        ])

    @http.route("/marketplace/vendedor/<partner_ref>", type="http", auth="public", website=True)
    def marketplace_seller_storefront(self, partner_ref, **kw):
        seller = request.env["res.partner"].sudo().resolve_ref(partner_ref)

        if not seller or seller.shrimp_user_type not in ("semillero", "laboratorio", "camaronera"):
            raise NotFound()

        products = request.env["shrimp.product"].sudo().search([
            ("seller_partner_id", "=", seller.id),
            ("active", "=", True),
            ("state", "=", "published"),
            ("available_qty", ">", 0),
        ], order="create_date desc")

        cover_atts = request.env["ir.attachment"].sudo().browse([])
        for product in products:
            first = product.photo_attachment_ids[:1]
            if first:
                cover_atts |= first
        if cover_atts:
            cover_atts.generate_access_token()

        today = fields.Date.context_today(request.env.user)
        certificate_lines = request.env["shrimp.user.certificate.line"].sudo().search([
            ("partner_id", "=", seller.id),
            ("status", "=", "approved"),
            "|", ("expiry_date", "=", False), ("expiry_date", ">=", today),
        ])

        reviews = request.env["shrimp.review"].sudo().search(
            [("seller_partner_id", "=", seller.id)], order="create_date desc", limit=50)

        return request.render("shrimp_marketplace.seller_storefront", {
            "seller": seller,
            "products": products,
            "products_count": len(products),
            "certificate_lines": certificate_lines,
            "reviews": reviews,
        })

    @http.route("/marketplace/calendar", type="http", auth="public", website=True)
    def marketplace_calendar(self, **kw):
        return request.render("shrimp_marketplace.marketplace_calendar", {})

    @http.route("/marketplace/calendar/events", type="json", auth="public", website=True)
    def marketplace_calendar_events(self, **kw):
        domain = self._build_public_marketplace_domain()
        domain.append(("expected_delivery_date", "!=", False))

        products = request.env["shrimp.product"].sudo().search(
            domain,
            order="expected_delivery_date asc, create_date desc",
        )

        events = []
        for product in products:
            date_str = (
                product.expected_delivery_date
                if isinstance(product.expected_delivery_date, str)
                else product.expected_delivery_date.strftime("%Y-%m-%d")
            )

            stage_label = product.stage_id.name if product.stage_id else ""
            uom_label = product.uom_id.name or ""

            events.append({
                "id": product.id,
                "start": date_str,
                "title": f"{stage_label} • {product.available_qty:g} {uom_label} • {product.seller_partner_id.name}",
                "url": f"/marketplace/product/{product.uuid_ref}",
                "meta": {
                    "name": product.name,
                    "price": product.price,
                    "location": product.location or "",
                    "seller": product.seller_partner_id.name or "",
                    "stage": stage_label,
                    "qty": product.available_qty,
                    "uom": uom_label,
                }
            })

        return events


class ShrimpWebsiteHome(Website):
    """La home del sitio (/) es la landing de marketing estilo Apple."""

    @http.route()
    def index(self, **kw):
        return request.render("shrimp_marketplace.landing", {})