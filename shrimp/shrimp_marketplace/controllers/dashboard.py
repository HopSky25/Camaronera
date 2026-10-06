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
