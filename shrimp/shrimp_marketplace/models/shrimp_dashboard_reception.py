"""Mi panel: entregas próximas, atrasadas y recepciones por confirmar.

Se agrega en el gancho común (_panel_common) para todos los perfiles, del lado
del vendedor (sus ventas con ese perfil) y del comprador (sus compras con ese
perfil). Ver models/shrimp_transaction_reception.py.
"""
from datetime import timedelta

from odoo import api, models, _

from .shrimp_dashboard import fmt_num

# Días hacia adelante que cubre «Próximas entregas» (como mínimo; si la
# anticipación de los avisos es mayor, se usa esa).
UPCOMING_DAYS = 7
MAX_ROWS = 8


class ShrimpDashboardReception(models.AbstractModel):
    _inherit = "shrimp.dashboard"

    @api.model
    def _panel_common(self, partner, role, data):
        res = super()._panel_common(partner, role, data)
        self._panel_deliveries(partner, role, data)
        return res

    @api.model
    def _panel_tx_open(self, partner, role):
        """(ventas, compras) confirmadas que esperan entrega o recepción."""
        Tx = self.env["shrimp.transaction"].sudo()
        base = [("state", "=", "confirmed")]
        ventas = Tx.search(self._sales_domain(partner, role) + base, order="delivery_date asc, id asc")
        compras = Tx.search(self._purchases_domain(partner, role) + base, order="delivery_date asc, id asc")
        directa = lambda t: t._shrimp_is_direct_delivery()  # noqa: E731
        return ventas.filtered(directa), compras.filtered(directa)

    @api.model
    def _panel_delivery_row(self, tx, lado, hoy):
        es_venta = lado == "sale"
        url = "/marketplace/purchases/%s/traceability#recepcion" % tx.uuid_ref
        if tx.reception_status == "incidence":
            estado = _("Con incidencia")
        elif tx.dispatched_at:
            estado = _("Despachada")
        elif tx.delivery_date and tx.delivery_date < hoy:
            estado = _("Atrasada %s días") % (hoy - tx.delivery_date).days
        elif tx.delivery_date == hoy:
            estado = _("Hoy")
        elif tx.delivery_date:
            estado = _("En %s días") % (tx.delivery_date - hoy).days
        else:
            estado = "—"
        if es_venta and not tx.dispatched_at and tx.reception_status != "incidence":
            boton = {"label": _("Marcar despachado"), "url": url, "primary": True}
        elif not es_venta and tx.shrimp_reception_window_open() and tx.reception_status != "incidence":
            boton = {"label": _("Confirmar recepción"), "url": url, "primary": True}
        else:
            boton = {"label": _("Ver"), "url": url}
        return {
            "cells": [
                tx.delivery_date.strftime("%d/%m/%Y") if tx.delivery_date else "—",
                _("Venta") if es_venta else _("Compra"),
                (tx.buyer_partner_id if es_venta else tx.seller_partner_id).name or "—",
                tx.product_id.name or "—",
                "%s %s" % (fmt_num(tx.transaction_qty), tx.product_id.uom_id.name or ""),
                estado,
            ],
            "url": url,
            "button": boton,
        }

    @api.model
    def _panel_deliveries(self, partner, role, data):
        Tx = self.env["shrimp.transaction"]
        hoy = Tx._shrimp_today()
        ventas, compras = self._panel_tx_open(partner, role)
        columnas = [_("Entrega"), _("Tipo"), _("Contraparte"), _("Producto"), _("Cantidad"),
                    _("Situación")]

        atrasadas = [(t, "sale") for t in ventas if t.shrimp_delivery_overdue()] + \
                    [(t, "purchase") for t in compras if t.shrimp_delivery_overdue()]
        horizonte = hoy + timedelta(days=max(UPCOMING_DAYS, Tx._shrimp_delivery_alert_days()))
        proximas = [(t, "sale") for t in ventas
                    if t.delivery_date and hoy <= t.delivery_date <= horizonte] + \
                   [(t, "purchase") for t in compras
                    if t.delivery_date and hoy <= t.delivery_date <= horizonte]
        proximas.sort(key=lambda par: (par[0].delivery_date, par[0].id))
        atrasadas.sort(key=lambda par: (par[0].delivery_date, par[0].id))

        # Recepciones que el comprador ya puede confirmar.
        por_confirmar = [t for t in compras
                         if t.reception_status != "incidence" and t.shrimp_reception_window_open()]
        con_plazo = [t for t in por_confirmar if t.reception_required and t.reception_deadline]
        incidencias_v = [t for t in ventas if t.reception_status == "incidence"]
        incidencias_c = [t for t in compras if t.reception_status == "incidence"]

        alertas = []
        if atrasadas:
            n_v = len([1 for _t, lado in atrasadas if lado == "sale"])
            n_c = len(atrasadas) - n_v
            partes = []
            if n_v:
                partes.append((_("%s venta sin despachar después de su fecha de entrega") if n_v == 1
                               else _("%s ventas sin despachar después de su fecha de entrega")) % n_v)
            if n_c:
                partes.append((_("%s compra atrasada") if n_c == 1 else _("%s compras atrasadas")) % n_c)
            alertas.append({
                "level": "danger", "key": "late_deliveries",
                "text": _("Entregas atrasadas: %s.") % "; ".join(partes),
                "url": "/my/dashboard#late_deliveries", "link_label": _("Ver entregas atrasadas"),
            })
        if por_confirmar:
            texto = (_("%s compra espera que confirmes la recepción.") if len(por_confirmar) == 1
                     else _("%s compras esperan que confirmes la recepción.")) % len(por_confirmar)
            nivel = "info"
            if con_plazo:
                urgente = min(con_plazo, key=lambda t: t.reception_days_left)
                dias = max(urgente.reception_days_left, 0) + 1
                texto += " " + (_("La más próxima se confirma automáticamente mañana (%(r)s).")
                                if dias == 1 else
                                _("La más próxima se confirma automáticamente en %(d)s días "
                                  "(%(r)s).")) % {"d": dias, "r": urgente.name}
                if urgente.reception_days_left <= 3:
                    nivel = "warning"
            alertas.append({
                "level": nivel, "key": "to_receive", "text": texto,
                "url": "/marketplace/purchases", "link_label": _("Confirmar recepción"),
            })
        if incidencias_v:
            alertas.append({
                "level": "danger", "key": "incidence_sales",
                "text": (_("%s venta con una incidencia reportada por el comprador.")
                         if len(incidencias_v) == 1 else
                         _("%s ventas con una incidencia reportada por el comprador.")) % len(incidencias_v),
                "url": "/marketplace/sales", "link_label": _("Ver ventas"),
            })
        if incidencias_c:
            alertas.append({
                "level": "info", "key": "incidence_purchases",
                "text": (_("%s compra con incidencia en revisión por la plataforma.")
                         if len(incidencias_c) == 1 else
                         _("%s compras con incidencia en revisión por la plataforma.")) % len(incidencias_c),
                "url": "/marketplace/purchases", "link_label": _("Ver compras"),
            })
        data["alerts"][:0] = alertas

        secciones = []
        if atrasadas:
            secciones.append({
                "key": "late_deliveries", "title": _("Entregas atrasadas"),
                "icon": "fa-exclamation-triangle", "columns": columnas,
                "rows": [self._panel_delivery_row(t, lado, hoy) for t, lado in atrasadas[:MAX_ROWS]],
                "empty": _("No tienes entregas atrasadas."),
            })
        secciones.append({
            "key": "upcoming_deliveries", "title": _("Próximas entregas"), "icon": "fa-calendar",
            "columns": columnas,
            "rows": [self._panel_delivery_row(t, lado, hoy) for t, lado in proximas[:MAX_ROWS]],
            "empty": _("No tienes entregas en los próximos %s días.") % (horizonte - hoy).days,
            "more_url": "/marketplace/planning", "more_label": _("Ver planificación"),
        })
        data["sections"][:0] = secciones

        # Reputación del vendedor: entregas cumplidas.
        if role in ("semillero", "laboratorio") and partner.shrimp_delivery_count:
            data["kpis"].append({
                "key": "delivery_ok", "label": _("Entregas cumplidas"),
                "value": "%.0f %%" % partner.shrimp_delivery_ok_pct,
                "sub": _("%(ok)s de %(n)s sin incidencia · %(a)s confirmadas solas") % {
                    "ok": partner.shrimp_delivery_ok_count, "n": partner.shrimp_delivery_count,
                    "a": partner.shrimp_delivery_auto_count},
                "icon": "fa-truck", "tone": "green", "url": "/marketplace/sales",
            })
        return data
