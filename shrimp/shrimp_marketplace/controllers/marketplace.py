import base64
import json
from urllib.parse import urlencode

from odoo import http, fields
from odoo.http import request
from werkzeug.exceptions import NotFound
from odoo.addons.website.controllers.main import Website
from odoo.tools import consteq

from odoo.addons.shrimp_user_registry.controllers.main import binary_headers


def _por_token(adjuntos, token):
    """Adjunto de `adjuntos` cuyo access_token coincide (comparación en
    tiempo constante). Las rutas de archivos se identifican por este token,
    nunca por el id del ir.attachment."""
    token = str(token or "")
    if not token or token.isdigit():
        return adjuntos.browse()
    for att in adjuntos.sudo():
        if att.access_token and consteq(att.access_token, token):
            return att
    return adjuntos.browse()


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
        # Perfil ACTIVO efectivo (un contacto hijo navega con el de su empresa).
        current_user_type = current_partner._shrimp_effective_type() if current_partner else False

        domain = [
            ("active", "=", True),
            ("state", "=", "published"),
            ("available_qty", ">", 0),
        ]

        # Varios perfiles por cuenta: se filtra por el ROL del lote
        # (seller_role), no por el perfil con el que navega su vendedor.
        # Los roles de lote que el perfil activo puede comprar salen de la
        # matriz (buy_from_<rol>), incluido su MISMO nivel: una camaronera ve
        # la larva de laboratorio y el camarón de otras camaroneras; un
        # laboratorio, el nauplio de semillero y la larva de otros
        # laboratorios. El filtro fino (motivo_no_comprable) va después.
        if current_user_type:
            Partner = request.env["res.partner"].sudo()
            roles_lote = [v for v, _l in request.env["shrimp.product"]._fields["seller_role"].selection]
            domain.append(("seller_role", "in", [
                r for r in roles_lote
                if Partner._shrimp_type_can(current_user_type, "buy_from_%s" % r)]))
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

        # Solo lo que el usuario puede comprar: si tiene veto (por rol o por su
        # posición en la cadena) el producto no aparece en el grid. Al público
        # no se le filtra (no hay veto hasta iniciar sesión). El texto
        # "No disponible" de la tarjeta queda como red de seguridad.
        user = request.env.user
        if products and not user._is_public():
            partner = user.partner_id
            products = products.filtered(lambda p: not p.motivo_no_comprable(partner))

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

    # ------------------------------------------------------------------
    # Etiquetas de filtros activos (barra superior del catálogo)
    # ------------------------------------------------------------------
    def _filter_tag_groups(self):
        """Grupos de parámetros que forman UNA etiqueta, en el orden en que se
        muestran. La búsqueda (q) y el orden (sort) no son etiquetas."""
        return [
            ("stage",), ("species",), ("presentation",), ("size_grade",),
            ("location",), ("price_min", "price_max"), ("seller",),
        ]

    def _filter_tag_label(self, group, filters):
        """Texto de la etiqueta de un grupo de filtros (o None si no aplica)."""
        key = group[0]
        val = filters.get(key) or ""
        if group == ("price_min", "price_max"):
            pmin, pmax = filters.get("price_min") or "", filters.get("price_max") or ""
            if pmin and pmax:
                return "Precio $%s–$%s" % (pmin, pmax)
            if pmin:
                return "Precio desde $%s" % pmin
            if pmax:
                return "Precio hasta $%s" % pmax
            return None
        if not val:
            return None
        if key == "stage":
            try:
                st = request.env["shrimp.stage"].sudo().browse(int(val)).exists()
            except (TypeError, ValueError):
                st = False
            return ("Estadío: %s" % st.name) if st else None
        if key == "presentation":
            return {"entero": "Entero", "cola": "Cola"}.get(val)
        if key == "size_grade":
            try:
                sg = request.env["shrimp.size.grade"].sudo().browse(int(val)).exists()
            except (TypeError, ValueError):
                sg = False
            return ("Talla: %s" % sg.name) if sg else None
        if key == "location":
            return "Ubicación: %s" % val
        if key == "seller":
            return "Vendedor: %s" % val
        if key == "species":
            return val
        return None

    def _active_filter_tags(self, filters, sort, vista, is_best):
        """Etiquetas removibles: cada una enlaza a la misma URL sin su
        parámetro (se conservan la búsqueda, el orden, la vista y el resto).
        Devuelve (etiquetas, url_limpiar_todo)."""
        keep = {}
        if sort and sort != "recommended":
            keep["sort"] = sort
        if vista:
            keep["vista"] = vista
        base = {k: v for k, v in filters.items() if v}
        best = {"best": "1"} if is_best else {}

        def url(qs):
            return "/marketplace" + ("?" + urlencode(qs) if qs else "") + "#listado"

        tags = []
        for group in self._filter_tag_groups():
            label = self._filter_tag_label(group, filters)
            if not label:
                continue
            qs = {k: v for k, v in base.items() if k not in group}
            qs.update(keep)
            qs.update(best)
            tags.append({"key": "-".join(group), "label": label, "url": url(qs)})
        clear = dict({"q": filters["q"]} if filters.get("q") else {}, **keep)
        return tags, url(clear)

    def _toolbar_hidden_keys(self):
        """Filtros que la barra superior (búsqueda y orden) conserva como
        campos ocultos. Los módulos que añaden filtros amplían la lista."""
        return ["stage", "species", "presentation", "size_grade", "location",
                "seller", "price_min", "price_max"]

    def _sort_options(self):
        return [
            ("recommended", "Recomendados"),
            ("price_asc", "Precio: menor a mayor"),
            ("price_desc", "Precio: mayor a menor"),
            ("rating", "Mejor calificados"),
            ("recent", "Más recientes"),
        ]

    def _partner_price_map(self, products):
        """El precio por cliente se retiró: ya no hay precios personalizados."""
        return {}

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

        # URL para quitar el modo Top 10 conservando los filtros actuales.
        _qs = {k: v for k, v in filters.items() if v}
        if sort and sort != "recommended":
            _qs["sort"] = sort

        # Vista del listado elegida por el usuario: "lista" (filas, estilo
        # Amazon) o "grid" (tarjetas, por defecto). Solo cambia la
        # presentación; se conserva en todos los enlaces y formularios del
        # listado. Sin parámetro, el navegador aplica la última vista
        # guardada (localStorage) antes de pintar las tarjetas.
        vista = (kw.get("vista") or "").strip()
        if vista not in ("lista", "grid"):
            vista = ""
        vista_qs = ("?vista=" + vista) if vista else ""
        vista_amp = ("&vista=" + vista) if vista else ""
        _qs_vista = dict(_qs, **({"best": "1"} if is_best else {}))
        vista_lista_url = "/marketplace?" + urlencode(dict(_qs_vista, vista="lista")) + "#listado"
        vista_grid_url = "/marketplace?" + urlencode(dict(_qs_vista, vista="grid")) + "#listado"
        if vista:
            _qs["vista"] = vista

        all_url = ("/marketplace?" + urlencode(_qs) + "#listado") if _qs else "/marketplace"

        # Filtros activos: etiquetas removibles y contador del botón «Filtros»
        # (no cuentan la búsqueda ni el orden).
        filter_tags, clear_filters_url = self._active_filter_tags(filters, sort, vista, is_best)
        active_filters = len(filter_tags)

        # Barra superior, etiquetas y cajón: plantillas reutilizables
        # (views/filter_drawer_templates.xml) que leen este diccionario.
        fd = {
            "action": "/marketplace",
            "toolbar_label": "Buscar y ordenar productos",
            "search": {"name": "q", "value": filters.get("q") or "",
                       "placeholder": "Buscar por nombre, especie o ubicación…",
                       "label": "Buscar productos"},
            "hidden": [(k, filters.get(k) or "") for k in self._toolbar_hidden_keys()]
            + [("vista", vista)],
            "sort": {"name": "sort", "value": sort, "options": self._sort_options()},
            "view": {"key": "shrimp_mkt_vista", "target": "prodGrid", "path": "/marketplace",
                     "current": vista, "lista_url": vista_lista_url, "grid_url": vista_grid_url,
                     "label": "Vista de los productos"},
            "tags": filter_tags,
            "clear_url": clear_filters_url,
            "active": active_filters,
            "count_url": "/marketplace/count",
            "total": total_count,
            "one": "producto", "many": "productos", "apply_label": "Ver productos",
            "drawer_hidden": [("q", filters.get("q") or ""), ("stage", filters.get("stage") or ""),
                              ("sort", sort if sort and sort != "recommended" else ""),
                              ("vista", vista)],
            "keep": ["q", "sort", "vista"],
            "anchor": "#listado",
        }

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
            "filter_tags": filter_tags,
            "clear_filters_url": clear_filters_url,
            "sort": sort,
            "best_price_id": best_price_id,
            "is_best": is_best,
            "all_url": all_url,
            "vista": vista,
            "vista_qs": vista_qs,
            "vista_amp": vista_amp,
            "vista_lista_url": vista_lista_url,
            "vista_grid_url": vista_grid_url,
            "partner_prices": self._partner_price_map(page),
            "fd": fd,
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
            "partner_prices": self._partner_price_map(batch),
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
            ("X-Content-Type-Options", "nosniff"),
        ])

    @http.route("/marketplace/count", type="http", auth="public", website=True,
                methods=["GET"], sitemap=False)
    def marketplace_count(self, **kw):
        """Cuántos productos daría la selección actual del cajón de filtros
        (botón «Ver N productos»). Mismo dominio que /marketplace."""
        products, _filters, _sort, _best = self._search_marketplace_products(kw)
        payload = json.dumps({"count": len(products)})
        return request.make_response(payload, headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
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
                "name": att.name,
                "url": product.shrimp_photo_url(att),
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

        buyer_partner = request.env.user.partner_id if not request.env.user._is_public() else False
        unit_price = product.price_for_partner(buyer_partner)
        has_custom_price = unit_price != product.price

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
            "unit_price": unit_price,
            "has_custom_price": has_custom_price,
        })

    # La antigua /marketplace/producto/<ref> (redirect legacy) la atiende ahora
    # el redirector genérico de rutas viejas (shrimp_user_registry/ir_http.py):
    # 301 a /marketplace/product/<ref>, que resuelve los mismos formatos de ref.

    @http.route("/marketplace/product/<product_ref>/photo/<token>", type="http", auth="public", website=True, sitemap=False)
    def marketplace_product_photo(self, product_ref, token, **kwargs):
        """Foto de un producto. Se identifica por el access_token del adjunto
        (no por su id) y solo entre las fotos de ESTE producto."""
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)

        if not product or not product.active:
            raise NotFound()

        can_see, _, _ = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        att = _por_token(product.photo_attachment_ids, token)
        if not att or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype or "image/jpeg", cache="private, max-age=3600"))

    @http.route("/marketplace/product/<product_ref>/certificate/<token>", type="http", auth="public", website=True, sitemap=False)
    def marketplace_product_certificate(self, product_ref, token, **kwargs):
        """Archivo de un certificado del producto, por el token del adjunto.

        Al público solo se le sirven los certificados APROBADOS; el dueño y el
        personal interno ven también los pendientes (y el campo heredado
        cert_attachment_ids).
        """
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)

        if not product or not product.active:
            raise NotFound()

        can_see, is_owner, is_internal = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        lineas = product.shrimp_visible_certificate_lines(owner_view=is_owner or is_internal)
        permitidos = lineas.mapped("attachment_id")
        if is_owner or is_internal:
            permitidos |= product.cert_attachment_ids

        att = _por_token(permitidos, token)
        if not att or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        # Por defecto en el navegador (inline); con ?download=1 se descarga.
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype, att.name or "certificado",
            download=bool(kwargs.get("download"))))

    @http.route("/marketplace/product/<product_ref>/user-certificate/<line_ref>", type="http", auth="public", website=True, sitemap=False)
    def marketplace_seller_certificate(self, product_ref, line_ref, **kwargs):
        """Certificado del VENDEDOR mostrado en la ficha del producto.

        La línea se resuelve por su código (uuid_ref), como el resto de la
        plataforma: un id numérico responde 404.
        """
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()

        can_see, _, _ = self._can_see_product(product)
        if not can_see:
            raise NotFound()

        # El certificado debe pertenecer al vendedor del producto y estar aprobado.
        line = request.env["shrimp.user.certificate.line"].sudo().resolve_ref(line_ref)
        if (not line
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

    @http.route("/marketplace/sellers/<partner_ref>/avatar", type="http", auth="public", website=True, sitemap=False)
    def marketplace_seller_avatar(self, partner_ref, **kw):
        """Logo del vendedor por su código (en vez de /web/image/res.partner/<id>)."""
        seller = request.env["res.partner"].sudo().resolve_ref(partner_ref)
        if not seller or not seller._shrimp_can_any("sell_products") \
                or not seller.image_128:
            raise NotFound()
        content = base64.b64decode(seller.image_128)
        from odoo.tools.mimetypes import guess_mimetype
        mimetype = guess_mimetype(content, default="image/png")
        if mimetype not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            raise NotFound()   # nunca SVG/HTML desde esta ruta
        return request.make_response(content, headers=binary_headers(
            content, mimetype, cache="public, max-age=3600"))

    @http.route("/marketplace/sellers/<partner_ref>", type="http", auth="public", website=True)
    def marketplace_seller_storefront(self, partner_ref, **kw):
        seller = request.env["res.partner"].sudo().resolve_ref(partner_ref)

        # Varios perfiles: la tienda es de la cuenta si ALGUNO de sus perfiles
        # vende, aunque ahora navegue con otro (p. ej. como empacadora).
        if not seller or not seller._shrimp_can_any("sell_products"):
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

        # Solo las reseñas recibidas como VENDEDOR: las que le dejaron como
        # comprador no hablan de lo que vende (y no cuentan en su nota).
        reviews = seller._shrimp_seller_reviews(limit=50)

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

    @http.route("/marketplace/calendar/events", type="jsonrpc", auth="public", website=True)
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
                "id": product.uuid_ref,
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

    def _login_redirect(self, uid, redirect=None):
        """Tras el login, el socio de portal entra a su trabajo y no a /my.

        Sin ?redirect= explícito (o con el genérico /web, que para el portal
        acaba en /my), el sitio decide la llegada (website._shrimp_login_landing):
        /my/dashboard en el marketplace si el perfil activo tiene panel; la bandeja
        propia en los sitios de verificadores y de empaque. Un ?redirect=
        explícito se respeta siempre; los usuarios internos siguen al backend.
        También vale tras el alta con invitación (auth_signup acaba en
        web_login) y tras el registro propio (/register → /web/login)."""
        if (not redirect or redirect in ("/web", "/web/")) and request.website:
            user = request.env["res.users"].sudo().browse(uid)
            destino = request.website.sudo()._shrimp_login_landing(user) if user.exists() else None
            if destino:
                redirect = destino
        return super()._login_redirect(uid, redirect=redirect)

    @http.route()
    def index(self, **kw):
        # «/» (y el logo) muestran SIEMPRE la portada, también con sesión.
        # Al panel se llega al iniciar sesión (_login_redirect) y desde
        # «Mi panel» en la barra.
        return request.render("shrimp_marketplace.landing", {})