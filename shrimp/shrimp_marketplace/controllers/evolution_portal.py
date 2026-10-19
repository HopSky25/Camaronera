# -*- coding: utf-8 -*-
"""«Estado real del lote»: registrar, corregir y borrar observaciones del
vendedor, y «Actualizar publicación a la etapa observada».

Solo el dueño del lote con permiso «Vender» en ese perfil (o un interno). El
lote y la observación se resuelven por su código (uuid_ref), nunca por id.
Formularios POST normales (el modal envía el formulario) que vuelven a la
página de origen con ?obs=<resultado> o ?obs_error=flash.
"""
from odoo import http, _
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from werkzeug.exceptions import Forbidden, NotFound

from odoo.addons.shrimp_user_registry.controllers.main import flash_message, to_number

NEXT_ALLOWED = ("/marketplace/product/", "/marketplace/products")


class ShrimpEvolutionPortal(http.Controller):

    @staticmethod
    def _product(product_ref):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()
        if not product.with_env(request.env)._shrimp_user_can_observe():
            raise Forbidden()
        return product

    @staticmethod
    def _observation(product, obs_ref):
        obs = request.env["shrimp.product.evolution"].sudo().resolve_ref(obs_ref) if obs_ref else None
        if not obs or obs.product_id != product:
            raise NotFound()
        return obs

    @staticmethod
    def _next(product, post):
        nxt = (post.get("next") or "").strip()
        if not nxt.startswith(NEXT_ALLOWED) or "//" in nxt or "\\" in nxt:
            nxt = "/marketplace/product/%s" % product.uuid_ref
        return nxt.split("#")[0].split("?")[0]

    @staticmethod
    def _go(url, **params):
        qs = "&".join("%s=%s" % (k, v) for k, v in params.items() if v)
        anchor = "#evo_real" if url.startswith("/marketplace/product/") else ""
        return request.redirect("%s%s%s%s" % (url, "?" if qs else "", qs, anchor), local=True)

    @staticmethod
    def _data(post):
        stage = request.env["shrimp.stage"].sudo().resolve_ref(post.get("stage")) \
            if post.get("stage") else None
        num = lambda k: to_number(post.get(k), 0.0)  # noqa: E731
        return {
            "date": (post.get("date") or "").strip() or None,
            "stage": stage or request.env["shrimp.stage"],
            "size": num("size"),
            "survival_rate": num("survival_rate"),
            "health_status": post.get("health_status"),
            "note": post.get("note"),
            "water_temp_c": num("water_temp_c"),
            "salinity_ppt": num("salinity_ppt"),
            "dissolved_oxygen_mgl": num("dissolved_oxygen_mgl"),
            "water_ph": num("water_ph"),
            "estimated_qty": num("estimated_qty"),
        }

    def _run(self, product, post, fn, ok_code):
        nxt = self._next(product, post)
        try:
            fn()
        except (ValidationError, AccessError) as e:
            return self._go(nxt, obs_error=flash_message(e.args[0] if e.args else str(e)),
                            obs_product=product.uuid_ref)
        return self._go(nxt, obs=ok_code, obs_product=product.uuid_ref)

    @http.route("/marketplace/products/<product_ref>/observations", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def observation_create(self, product_ref, **post):
        product = self._product(product_ref)
        prod = product.with_env(request.env)
        return self._run(product, post, lambda: prod.shrimp_register_observation(self._data(post)),
                         "saved")

    @http.route("/marketplace/products/<product_ref>/observations/<obs_ref>/update", type="http",
                auth="user", website=True, methods=["POST"], csrf=True, sitemap=False)
    def observation_update(self, product_ref, obs_ref, **post):
        product = self._product(product_ref)
        obs = self._observation(product, obs_ref)
        prod = product.with_env(request.env)
        return self._run(product, post, lambda: prod.shrimp_update_observation(obs, self._data(post)),
                         "updated")

    @http.route("/marketplace/products/<product_ref>/observations/<obs_ref>/delete", type="http",
                auth="user", website=True, methods=["POST"], csrf=True, sitemap=False)
    def observation_delete(self, product_ref, obs_ref, **post):
        product = self._product(product_ref)
        obs = self._observation(product, obs_ref)
        prod = product.with_env(request.env)
        return self._run(product, post, lambda: prod.shrimp_delete_observation(obs), "deleted")

    @http.route("/marketplace/products/<product_ref>/observations/<obs_ref>/apply", type="http",
                auth="user", website=True, methods=["POST"], csrf=True, sitemap=False)
    def observation_apply(self, product_ref, obs_ref, **post):
        """«Actualizar publicación a la etapa observada»: solo con este POST."""
        product = self._product(product_ref)
        obs = self._observation(product, obs_ref)
        prod = product.with_env(request.env)
        return self._run(product, post, lambda: prod.shrimp_apply_observation(obs), "applied")
