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
        extra = [
            {"label": _("Oferta disponible"), "url": "/marketplace/supply", "icon": "fa-binoculars", "tone": "teal"},
            {"label": _("Mis listas de precios"), "url": "/marketplace/price-lists", "icon": "fa-list-alt", "tone": "green"},
            {"label": _("Cosechas ofrecidas"), "url": "/packer/reservations", "icon": "fa-calendar", "tone": "amber"},
            {"label": _("Rendimiento de proveedores"), "url": "/marketplace/suppliers", "icon": "fa-trophy", "tone": "navy"},
        ]
        data["shortcuts"] = data["shortcuts"][:1] + extra + data["shortcuts"][1:]
        data["actions"].append({"label": _("Nueva lista de precios"), "url": "/marketplace/price-lists/new",
                                "icon": "fa-plus", "primary": False})

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
        extra = [
            {"label": _("Reservas de cosecha"), "url": "/marketplace/reservations", "icon": "fa-calendar-check-o", "tone": "amber"},
            {"label": _("Listas de precios recibidas"), "url": "/marketplace/price-lists", "icon": "fa-list-alt", "tone": "green"},
            {"label": _("Empacadoras"), "url": "/marketplace/packers", "icon": "fa-industry", "tone": "navy"},
            {"label": _("Simulador de cosecha"), "url": "/marketplace/simulator", "icon": "fa-sliders", "tone": "teal"},
            {"label": _("Mi historial verificado"), "url": "/marketplace/my-track-record", "icon": "fa-check-circle", "tone": "green"},
        ]
        # Después de «Ventas» (los siete accesos propios de la camaronera).
        data["shortcuts"] = data["shortcuts"][:7] + extra + data["shortcuts"][7:]
