# -*- coding: utf-8 -*-
"""Mi panel (/my/dashboard): lo que aporta shrimp_packer.

- Empacadora: listas de precios vigentes, cosechas ofrecidas, avisos de
  lotes y accesos a oferta, listas, reservas y proveedores.
- Camaronera: reservas de cosecha (con compromisos por responder) y accesos
  a listas recibidas, empacadoras, simulador e historial verificado.
"""
from datetime import timedelta

from odoo import api, fields, models, _


class ShrimpDashboardPacker(models.AbstractModel):
    _inherit = "shrimp.dashboard"

    @api.model
    def _panel_empacadora(self, partner, data):
        super()._panel_empacadora(partner, data)
        env = self.env
        listas = env["shrimp.price.list"].sudo().search_count([
            ("issuer_partner_id", "=", partner.id), ("is_current", "=", True)])
        cosechas = env["shrimp.harvest.forecast"].sudo().search_count([("state", "=", "published")])
        desde = fields.Datetime.now() - timedelta(days=7)
        avisos = env["shrimp.lot.alert"].sudo().search_count([
            ("packer_partner_id", "=", partner.id), ("sent_date", ">=", desde)])
        data["kpis"] += [
            {"key": "price_lists", "label": _("Listas de precios vigentes"), "value": str(listas),
             "sub": _("publicadas y en su ventana de despacho"), "icon": "fa-list-alt", "tone": "teal",
             "url": "/marketplace/price-lists"},
            {"key": "forecasts", "label": _("Cosechas ofrecidas"), "value": str(cosechas),
             "sub": _("reservas abiertas de camaroneras"), "icon": "fa-calendar", "tone": "amber",
             "url": "/packer/reservations"},
            {"key": "lot_alerts", "label": _("Avisos de lotes (7 días)"), "value": str(avisos),
             "sub": _("lotes que encajan con tus listas"), "icon": "fa-bell", "tone": "coral",
             "url": "/marketplace/supply"},
        ]
        if not listas:
            data["alerts"].append({
                "level": "warning", "key": "no_price_list",
                "text": _("No tienes ninguna lista de precios vigente: las camaroneras no ven a cuánto les pagas."),
                "url": "/marketplace/price-lists/new", "link_label": _("Publicar lista"),
            })
        por_confirmar = env["shrimp.harvest.commitment"].sudo().search_count([
            ("packer_partner_id", "=", partner.id), ("state", "=", "to_confirm")])
        if por_confirmar:
            data["alerts"].append({
                "level": "info", "key": "commit_to_confirm",
                "text": _("%s reservas de cosecha esperan tu confirmación.") % por_confirmar,
                "url": "/packer/reservations", "link_label": _("Responder"),
            })
        # Los accesos (Oferta disponible, Mis listas de precios, Cosechas
        # ofrecidas, Rendimiento de proveedores) vienen de la tabla única de
        # la barra (models/website_navbar.py). Aquí solo el botón de la
        # cabecera para publicar una lista, si el usuario puede.
        if partner._shrimp_can("issue_price_lists"):
            data["actions"].append({"label": _("Nueva lista de precios"), "url": "/marketplace/price-lists/new",
                                    "icon": "fa-plus", "primary": False})
        self._seccion_mejores_proveedores(partner, data)

    @api.model
    def _seccion_mejores_proveedores(self, partner, data):
        """El líder del ranking verificado y los que le siguen, en «Mi panel»:
        tras iniciar sesión la empacadora llega a /my/dashboard, no a la
        portada, así que el resumen tiene que estar también aquí. Mismo
        cálculo y misma regla de acceso que /marketplace/suppliers."""
        dash = self.env["shrimp.proveedor.ranking"].sudo().dashboard_para(partner)
        if not dash:
            return

        def pct(valor, dec=1):
            return ("%%.%df %%%%" % dec) % valor if valor is not None else "—"

        filas = []
        for pos, f in enumerate([dash["lider"]] + dash["resto"], 1):
            nombre = f["proveedor"].name or ""
            if pos == 1:
                nombre = _("%s (líder)") % nombre
            elif not f["suficiente"]:
                nombre = _("%s (muestra corta)") % nombre
            filas.append({
                "cells": [
                    nombre, str(f["lotes"]), pct(f["rendimiento"]), pct(f["clase_a"], 0),
                    "%s / %s" % (f["lotes"] - f["incumplidos"], f["lotes"]),
                    "%.0f" % f["puntaje"] if f["puntaje"] is not None else "—",
                ],
                "url": "/marketplace/sellers/%s" % f["proveedor"].uuid_ref,
            })
        data["sections"].insert(0, {
            "key": "verified_suppliers",
            "title": _("Tu mejor proveedor verificado: %s") % (dash["lider"]["proveedor"].name or ""),
            "icon": "fa-trophy",
            "columns": [_("Camaronera"), _("Lotes"), _("Rendimiento"), _("Clase A"),
                        _("Fue lo publicado"), _("Puntaje")],
            "rows": filas,
            "empty": _("Todavía no hay lotes verificados con muestra suficiente."),
            "more_url": "/marketplace/suppliers", "more_label": _("Ver el ranking completo"),
        })

    @api.model
    def _panel_camaronera(self, partner, data):
        super()._panel_camaronera(partner, data)
        env = self.env
        Forecast = env["shrimp.harvest.forecast"].sudo()
        abiertas = Forecast.search_count([
            ("farmer_partner_id", "=", partner.id), ("state", "in", ["published", "committed"])])
        data["kpis"].append({
            "key": "harvest_forecasts", "label": _("Reservas de cosecha"), "value": str(abiertas),
            "sub": _("publicadas o comprometidas"), "icon": "fa-calendar-check-o", "tone": "amber",
            "url": "/marketplace/reservations"})
        ofertas = env["shrimp.harvest.commitment"].sudo().search_count([
            ("forecast_id.farmer_partner_id", "=", partner.id), ("state", "=", "sent")])
        if ofertas:
            data["alerts"].append({
                "level": "info", "key": "commitments_sent",
                "text": _("%s empacadoras se comprometieron con tus cosechas y esperan respuesta.") % ofertas,
                "url": "/marketplace/reservations", "link_label": _("Responder"),
            })
        # Accesos (reservas, listas recibidas, empacadoras, simulador,
        # historial verificado): tabla única de la barra.
