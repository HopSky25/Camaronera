# -*- coding: utf-8 -*-
"""Filtros de larva en el catálogo (/marketplace y /marketplace/cards).

Hereda el controlador del catálogo sin tocarlo: amplía la búsqueda con
  tipo=nauplio|larva|camaron   pestañas (agrupan los estadíos existentes)
  genetics=<id>                línea genética
  survival_min=60|70|80|90     supervivencia mínima publicada (%)
  pcr=1 / spf=1                certificado PCR / SPF aprobado y vigente
                               (del lote o del vendedor)
  sort=survival                mejor supervivencia primero
y añade al contexto de la página las pestañas, los «Laboratorios
destacados» y el «Avísame» del estado vacío. Como los dos endpoints usan
_search_marketplace_products, «Cargar más» respeta los mismos filtros.
"""
from urllib.parse import quote, urlencode

import base64

from odoo import http
from odoo.http import request

from werkzeug.exceptions import NotFound

from odoo.addons.shrimp_user_registry.controllers.main import binary_headers, current_partner, to_int

from .marketplace import ShrimpMarketplacePublicController
from ..models.shrimp_larva import SHRIMP_SUPERVIVENCIAS, SHRIMP_TIPO_ROL, SHRIMP_TIPOS

LARVA_FILTER_KEYS = ("tipo", "genetics", "survival_min", "pcr", "spf")


def _flag(valor):
    return str(valor or "").strip().lower() in ("1", "on", "true", "si", "sí")


class ShrimpLarvaMarketplace(ShrimpMarketplacePublicController):

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------
    @staticmethod
    def _larva_filters(kw):
        tipo = (kw.get("tipo") or "").strip()
        if tipo not in SHRIMP_TIPO_ROL:
            tipo = ""
        genetics = to_int(kw.get("genetics"), 0)
        survival = max(0, min(100, to_int(kw.get("survival_min"), 0)))
        return {
            "tipo": tipo,
            "genetics": str(genetics) if genetics > 0 else "",
            "survival_min": str(survival) if survival else "",
            "pcr": "1" if _flag(kw.get("pcr")) else "",
            "spf": "1" if _flag(kw.get("spf")) else "",
        }

    # ------------------------------------------------------------------
    # Lo que el perfil activo puede comprar (por tipo de producto)
    # ------------------------------------------------------------------
    def _shrimp_allowed_tipos(self):
        """Tipos (nauplio/larva/camaron) que el perfil ACTIVO puede comprar
        según la matriz de capacidades (buy_from_<rol del vendedor del tipo>).
        Visitante o socio sin perfil: todos (ve el catálogo público entero)."""
        todos = [code for code, _label in SHRIMP_TIPOS]
        partner = self._get_current_partner()
        user_type = partner._shrimp_effective_type() if partner else False
        if not user_type:
            return todos
        Partner = request.env["res.partner"].sudo()
        return [t for t in todos
                if Partner._shrimp_type_can(user_type, "buy_from_%s" % SHRIMP_TIPO_ROL[t])]

    def _build_public_marketplace_domain(self):
        """Además del rol del vendedor, el TIPO del lote (que sale del
        estadío) tiene que ser uno que el perfil puede comprar: un semillero
        que publica postlarva no la vuelve comprable para otro semillero. Las
        pestañas, los estadíos y los filtros se cruzan con este dominio, así
        que no lo pueden saltar."""
        domain = super()._build_public_marketplace_domain()
        allowed = self._shrimp_allowed_tipos()
        if len(allowed) == len(SHRIMP_TIPOS):
            return domain
        if not allowed:
            return domain + [("id", "=", 0)]
        Product = request.env["shrimp.product"].sudo()
        extra = ["|"] * (len(allowed) - 1)
        for t in allowed:
            extra += Product._shrimp_tipo_domain(t)
        return domain + extra

    def _search_marketplace_products(self, kw):
        products, filters, sort, best_price_id = super()._search_marketplace_products(kw)
        larva = self._larva_filters(kw)
        # Una pestaña que el perfil no puede comprar no aplica (?tipo=larva
        # siendo semillero): se ignora y se ve lo que sí puede comprar.
        if larva["tipo"] and larva["tipo"] not in self._shrimp_allowed_tipos():
            larva["tipo"] = ""
        Product = request.env["shrimp.product"].sudo()

        extra = []
        if larva["tipo"]:
            extra += Product._shrimp_tipo_domain(larva["tipo"])
        if larva["genetics"]:
            extra.append(("genetics_line_id", "=", int(larva["genetics"])))
        if larva["survival_min"]:
            extra.append(("survival_rate", ">=", float(larva["survival_min"])))
        if larva["pcr"]:
            extra += Product._shrimp_cert_valid_domain("pcr")
        if larva["spf"]:
            extra += Product._shrimp_cert_valid_domain("spf")

        if extra and products:
            permitidos = set(Product.search([("id", "in", products.ids)] + extra).ids)
            # filtered() conserva el orden que ya trae la búsqueda base.
            products = products.filtered(lambda p: p.id in permitidos)
            best_price_id = min(products, key=lambda p: p.price).id if products else False

        if (kw.get("sort") or "").strip() == "survival":
            sort = "survival"
            products = products.sorted(key=lambda p: (-(p.survival_rate or 0.0), p.price))

        filters.update(larva)
        return products, filters, sort, best_price_id

    def _toolbar_hidden_keys(self):
        # La barra superior (búsqueda y orden) conserva también los de larva.
        return list(LARVA_FILTER_KEYS) + super()._toolbar_hidden_keys()

    def _sort_options(self):
        return super()._sort_options() + [("survival", "Mejor supervivencia")]

    # ------------------------------------------------------------------
    # Etiquetas de filtros activos
    # ------------------------------------------------------------------
    def _filter_tag_groups(self):
        groups = super()._filter_tag_groups()
        # Tipo primero; genética, supervivencia y certificados antes del vendedor.
        pos = groups.index(("seller",)) if ("seller",) in groups else len(groups)
        groups = groups[:pos] + [("genetics",), ("survival_min",), ("pcr",), ("spf",)] + groups[pos:]
        return [("tipo",)] + groups

    def _filter_tag_label(self, group, filters):
        key = group[0]
        val = filters.get(key) or ""
        if key == "tipo":
            return dict(SHRIMP_TIPOS).get(val) if val else None
        if key == "genetics":
            if not val:
                return None
            g = request.env["shrimp.genetics.line"].sudo().browse(int(val)).exists()
            return ("Genética: %s" % g.name) if g else None
        if key == "survival_min":
            return ("Supervivencia %s %%+" % val) if val else None
        if key == "pcr":
            return "PCR vigente" if val else None
        if key == "spf":
            return "SPF" if val else None
        return super()._filter_tag_label(group, filters)

    # ------------------------------------------------------------------
    # Página
    # ------------------------------------------------------------------
    @http.route()
    def marketplace_list(self, **kw):
        response = super().marketplace_list(**kw)
        qcontext = getattr(response, "qcontext", None)
        if qcontext is None:
            return response
        filters = qcontext.get("filters") or {}
        tipo = filters.get("tipo") or ""
        Product = request.env["shrimp.product"].sudo()

        vista = qcontext.get("vista") or ""
        sort = qcontext.get("sort") or ""

        def url(**params):
            qs = {k: v for k, v in params.items() if v}
            if vista:
                qs["vista"] = vista
            return "/marketplace" + ("?" + urlencode(qs) if qs else "")

        # Solo los tipos que el perfil activo puede comprar; con uno solo no
        # hay pestañas (ni «Tipo» en el cajón).
        allowed = self._shrimp_allowed_tipos()
        tabs = [{"code": "", "label": "Todo", "url": url(q=filters.get("q"), sort=sort if sort != "recommended" else ""),
                 "active": not tipo}]
        for code, label in SHRIMP_TIPOS:
            if code not in allowed:
                continue
            tabs.append({"code": code, "label": label, "active": tipo == code,
                         "url": url(tipo=code, q=filters.get("q"),
                                    sort=sort if sort != "recommended" else "")})
        show_tabs = len(allowed) > 1

        stage_options = qcontext.get("stage_options") or request.env["shrimp.stage"].sudo().browse()
        stage_options = stage_options.filtered(lambda s: s._shrimp_tipo() in allowed)
        if tipo:
            stage_options = stage_options.filtered(lambda s: s._shrimp_tipo() == tipo)
        stage_chips = [{"label": "Todos" if tipo else "Todas", "active": not filters.get("stage"),
                        "url": url(tipo=tipo)}]
        for st in stage_options:
            stage_chips.append({"label": st.name, "active": str(filters.get("stage") or "") == str(st.id),
                                "url": url(tipo=tipo, stage=st.id)})

        base = Product.search(self._build_public_marketplace_domain()
                              + (Product._shrimp_tipo_domain(tipo) if tipo else []))
        genetics_options = base.mapped("genetics_line_id").sorted(lambda g: g.name or "")

        # Con un único tipo comprable (p. ej. semillero → nauplio) el
        # encabezado y los destacados son los de ese tipo aunque no haya
        # pestaña elegida.
        vista_tipo = tipo or (allowed[0] if len(allowed) == 1 else "")
        larva_mode = vista_tipo in ("larva", "nauplio")
        featured = []
        # Destacados solo de un tipo que el perfil puede comprar (allowed ya
        # es «todos» para el visitante).
        if larva_mode and vista_tipo in allowed and not qcontext.get("is_best"):
            featured = Product._shrimp_featured_sellers(vista_tipo, limit=6)

        larva_active = bool(any(filters.get(k) for k in LARVA_FILTER_KEYS))
        qcontext.update({
            "larva_tabs": tabs,
            "larva_show_tabs": show_tabs,
            # Tipos que el perfil puede comprar: el cajón oculta los filtros
            # que no aplican (presentación y talla son solo de camarón).
            "larva_allowed_tipos": allowed,
            "larva_tipo": vista_tipo,
            "larva_mode": larva_mode,
            "larva_active": larva_active,
            "stage_chips": stage_chips,
            "genetics_options": genetics_options,
            "survival_options": SHRIMP_SUPERVIVENCIAS,
            "featured_sellers": featured,
            "avisame_ok": kw.get("avisame") == "ok",
            "avisame_login_url": "/web/login?redirect=" + quote(
                "/marketplace?" + urlencode({k: v for k, v in filters.items() if v}), safe=""),
        })
        return response

    # ------------------------------------------------------------------
    # «Avísame» (estado vacío)
    # ------------------------------------------------------------------
    @http.route("/marketplace/notify-me", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def marketplace_notify_me(self, **post):
        partner = current_partner()
        filtros = self._larva_filters(post)
        stage = to_int(post.get("stage"), 0)
        Stage = request.env["shrimp.stage"].sudo()
        Genetics = request.env["shrimp.genetics.line"].sudo()
        qs = {k: v for k, v in dict(filtros, stage=str(stage) if stage else "").items() if v}
        busqueda = "/marketplace?" + urlencode(sorted(qs.items()))
        Interest = request.env["shrimp.larva.interest"].sudo()
        if not Interest.search_count([("partner_id", "=", partner.id), ("search_url", "=", busqueda)]):
            Interest.create({
                "partner_id": partner.id,
                "tipo": filtros["tipo"] or False,
                "stage_id": Stage.browse(stage).exists().id if stage else False,
                "genetics_line_id": Genetics.browse(int(filtros["genetics"])).exists().id
                if filtros["genetics"] else False,
                "survival_min": int(filtros["survival_min"] or 0),
                "pcr_required": bool(filtros["pcr"]),
                "spf_required": bool(filtros["spf"]),
                "search_url": busqueda,
            })
        destino = dict(qs, avisame="ok")
        return request.redirect("/marketplace?" + urlencode(destino) + "#listado")

    # ------------------------------------------------------------------
    # Portada del lote para la landing
    # ------------------------------------------------------------------
    @http.route("/marketplace/product/<product_ref>/cover", type="http", auth="public",
                website=True, sitemap=False, readonly=True)
    def marketplace_product_cover(self, product_ref, **kw):
        """Primera foto del lote, por el código del producto. La portada (/)
        se sirve en solo lectura y no puede generar el token de cada adjunto
        (lo que exige la ruta /foto/<token>): esta ruta no lo necesita y
        aplica la misma visibilidad que la ficha del producto."""
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()
        can_see, _owner, _internal = self._can_see_product(product)
        att = product.photo_attachment_ids[:1].sudo()
        if not can_see or not att or not att.datas:
            raise NotFound()
        if (att.mimetype or "") not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            raise NotFound()
        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype, cache="public, max-age=3600"))
