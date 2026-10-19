# -*- coding: utf-8 -*-
"""/my/dashboard: el panel de trabajo de cada perfil (Fase 1).

Los datos los arma shrimp.dashboard (models/shrimp_dashboard.py) para el
socio del usuario, siempre con el perfil ACTIVO; los demás perfiles de la
cuenta aparecen como una franja con sus alertas y el botón para cambiar.
"""
from odoo import http
from odoo.http import request

from odoo.addons.shrimp_user_registry.controllers.main import current_partner, pop_message


class ShrimpDashboardController(http.Controller):

    @http.route("/my/dashboard", type="http", auth="user", website=True, sitemap=False)
    def mi_panel(self, **kw):
        partner = current_partner()
        Dashboard = request.env["shrimp.dashboard"]
        role = Dashboard._panel_active_role(partner)
        # Perfil de otra plataforma sin ninguno de este sitio (ir.http ya
        # habría activado uno): se explica dónde se usa.
        if role and not request.website.sudo()._shrimp_role_allowed_here(role):
            return request.redirect("/my/site-profile")
        # El verificador y el maquilador tienen su propio panel en su sitio
        # (o en /my): aquí no hay nada para ellos todavía.
        if role and not Dashboard._panel_supported(role):
            return request.redirect("/my")
        data = Dashboard._panel_data(partner, role)
        operativo = partner.sudo().shrimp_is_operational() if hasattr(partner, "shrimp_is_operational") else True
        return request.render("shrimp_marketplace.mi_panel", {
            "panel": data,
            "partner": partner,
            "operativo": operativo,
            "error_message": pop_message(kw.get("error")),
            "page_name": "mi_panel",
        })

    @http.route("/my/site-profile", type="http", auth="user", website=True, sitemap=False)
    def perfil_de_otro_sitio(self, **kw):
        """«Este perfil se usa en su sitio», con el enlace a ese sitio."""
        web = request.website.sudo()
        user = request.env.user
        activo = web._shrimp_align_active_role(user)
        if not activo or web._shrimp_role_allowed_here(activo):
            return request.redirect("/my/dashboard" if web.shrimp_platform == "main" else "/my")
        holder = user.sudo()._shrimp_account_company()
        roles = holder._shrimp_roles(include_pending=True) if holder else []
        enlaces = web._shrimp_offsite_links(roles, "/web/login")
        if not request.env.ref("shrimp_marketplace.site_profile_page", raise_if_not_found=False):
            # Módulo aún sin actualizar: directo al sitio del perfil.
            return request.redirect(enlaces[0]["url"], local=False) if enlaces else request.redirect("/my")
        return request.render("shrimp_marketplace.site_profile_page", {
            "active_label": request.env["res.partner"]._shrimp_type_label(activo),
            "site_links": enlaces,
        })
