"""Mi panel: retrasos de entrega por responder, compras que se pueden
cancelar sin costo, strikes del vendedor y «Entregas a tiempo %».

Ver models/shrimp_transaction_delay.py y models/shrimp_seller_strike.py.
"""
from odoo import api, models, _

from .shrimp_transaction_delay import shrimp_delay_ready

SELLER_ROLES = ("semillero", "laboratorio")


class ShrimpDashboardDelay(models.AbstractModel):
    _inherit = "shrimp.dashboard"

    @api.model
    def _panel_deliveries(self, partner, role, data):
        res = super()._panel_deliveries(partner, role, data)
        if shrimp_delay_ready(self.env):
            self._panel_delays(partner, role, data)
        return res

    @api.model
    def _panel_delays(self, partner, role, data):
        Tx = self.env["shrimp.transaction"]
        ventas, compras = self._panel_tx_open(partner, role)
        url = lambda t: "/marketplace/purchases/%s/traceability#retraso" % t.uuid_ref  # noqa: E731
        alertas = []

        # Comprador: avisos de retraso que esperan su respuesta.
        por_responder = [t for t in compras if t.shrimp_delay_open().state == "proposed"]
        if por_responder:
            t = min(por_responder, key=lambda x: x.shrimp_delay_open().buyer_deadline)
            aviso = t.shrimp_delay_open()
            alertas.append({
                "level": "warning", "key": "delay_to_answer",
                "text": (_("El vendedor avisó un retraso en %(n)s compra. Responde antes del %(f)s o "
                           "la nueva fecha se dará por aceptada.") if len(por_responder) == 1 else
                         _("Los vendedores avisaron retrasos en %(n)s compras. La primera vence el "
                           "%(f)s: si no respondes, la nueva fecha se da por aceptada.")) % {
                    "n": len(por_responder),
                    "f": Tx.shrimp_fmt_dt(aviso.buyer_deadline, partner)},
                "url": url(t), "link_label": _("Responder"),
            })
        cancelables = [t for t in compras if t.shrimp_delay_cancel_cause()]
        if cancelables:
            alertas.append({
                "level": "info", "key": "delay_can_cancel",
                "text": (_("%s compra atrasada se puede cancelar sin costo.") if len(cancelables) == 1
                         else _("%s compras atrasadas se pueden cancelar sin costo.")) % len(cancelables),
                "url": url(cancelables[0]), "link_label": _("Ver compra"),
            })
        # Vendedor: contrapropuestas por responder.
        contra = [t for t in ventas if t.shrimp_delay_open().state == "countered"]
        if contra:
            t = min(contra, key=lambda x: x.shrimp_delay_open().seller_deadline)
            alertas.append({
                "level": "warning", "key": "delay_counter",
                "text": (_("El comprador propuso otra fecha de entrega en %(n)s venta. Responde antes "
                           "del %(f)s; si no, cuenta como rechazada.") if len(contra) == 1 else
                         _("Los compradores propusieron otra fecha en %(n)s ventas. La primera vence el "
                           "%(f)s; si no respondes, cuenta como rechazada.")) % {
                    "n": len(contra),
                    "f": Tx.shrimp_fmt_dt(t.shrimp_delay_open().seller_deadline, partner)},
                "url": url(t), "link_label": _("Responder"),
            })

        # Strikes y suspensión: solo al vendedor (administrador o subusuario
        # con permiso de vender en este perfil); nunca en público.
        puede_vender = role in SELLER_ROLES and (
            not hasattr(partner, "_shrimp_user_allows")
            or partner._shrimp_user_allows("sell_products", role))
        if puede_vender:
            Strike = self.env["shrimp.seller.strike"]
            aviso_n, susp_n = Strike._shrimp_thresholds()
            # Directo (no el campo calculado, que queda en caché).
            activos = Strike._shrimp_active_count(partner)
            if self.env["shrimp.seller.suspension"]._shrimp_active_for(partner):
                alertas.append({
                    "level": "danger", "key": "strikes_suspended",
                    "text": _("Tu publicación está suspendida: tienes %s strikes activos por "
                              "incumplimientos de entrega. Tus ventas en curso siguen; la plataforma "
                              "revisará tu cuenta.") % activos,
                    "url": "/marketplace/sales", "link_label": _("Ver ventas"),
                })
            elif activos >= aviso_n:
                alertas.append({
                    "level": "warning", "key": "strikes_warning",
                    "text": _("Tienes %(n)s strikes activos: tus productos bajan en el catálogo y no "
                              "salen en destacados. Con %(s)s se suspende la publicación.") % {
                        "n": activos, "s": susp_n},
                    "url": "/marketplace/sales", "link_label": _("Ver ventas"),
                })
            if activos or partner.shrimp_delivery_count:
                data["kpis"].append({
                    "key": "strikes", "label": _("Strikes activos"),
                    "value": str(activos),
                    "sub": _("Vencen a los %(d)s días · aviso con %(a)s, suspensión con %(s)s") % {
                        "d": Strike._shrimp_expiry_days(), "a": aviso_n, "s": susp_n},
                    "icon": "fa-exclamation-circle",
                    "tone": "coral" if activos >= aviso_n else ("amber" if activos else "green"),
                    "url": "/marketplace/sales",
                })
        if role in SELLER_ROLES and partner.shrimp_delivery_count:
            data["kpis"].append({
                "key": "delivery_ontime", "label": _("Entregas a tiempo"),
                "value": "%.0f %%" % partner.shrimp_ontime_pct,
                "sub": _("%(a)s de %(n)s sin retraso") % {
                    "a": partner.shrimp_ontime_count, "n": partner.shrimp_delivery_count},
                "icon": "fa-clock-o", "tone": "teal", "url": "/marketplace/sales",
            })
        data["alerts"][:0] = alertas
        return data
