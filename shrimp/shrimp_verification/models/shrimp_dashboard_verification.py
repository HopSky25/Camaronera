# -*- coding: utf-8 -*-
"""Mi panel (/my/dashboard): supervivencia VERIFICADA en campo.

El verificador mide la supervivencia de la larva al recibirla
(larvae_survival_rate) y su desvío frente a la publicada
(larvae_survival_diff). Al laboratorio y al semillero se les muestra el
promedio y se les avisa de los lotes que quedaron por debajo de lo publicado.
"""
from datetime import timedelta

from odoo import api, fields, models, _

VERIFICACIONES_CERRADAS = ("done", "approved", "approved_obs", "rejected")
DESVIO_ALERTA = -5.0   # puntos porcentuales


class ShrimpDashboardVerification(models.AbstractModel):
    _inherit = "shrimp.dashboard"

    @api.model
    def _verified_survival(self, partner, role, data):
        Verif = self.env["shrimp.verification"].sudo()
        dominio = [("seller_partner_id", "=", partner.id),
                   ("product_id.seller_role", "=", role),
                   ("state", "in", list(VERIFICACIONES_CERRADAS)),
                   ("larvae_survival_rate", ">", 0)]
        verifs = Verif.search(dominio)
        if not verifs:
            return
        medida = sum(verifs.mapped("larvae_survival_rate")) / len(verifs)
        publicadas = [v.product_id.survival_rate for v in verifs if v.product_id.survival_rate]
        sub = (_("publicada %.0f %% · %s verificaciones") % (sum(publicadas) / len(publicadas), len(verifs))
               if publicadas else _("%s verificaciones") % len(verifs))
        data["kpis"].append({
            "key": "survival_verified", "label": _("Supervivencia verificada"),
            "value": "%.0f %%" % medida, "sub": sub, "icon": "fa-check-circle", "tone": "green",
            "url": False,
        })
        desde = fields.Datetime.now() - timedelta(days=90)
        bajas = verifs.filtered(lambda v: v.larvae_survival_diff and v.larvae_survival_diff < DESVIO_ALERTA
                                and v.create_date and v.create_date >= desde)
        if bajas:
            peor = min(bajas, key=lambda v: v.larvae_survival_diff)
            data["alerts"].append({
                "level": "danger", "key": "survival_below",
                "text": _("%(n)s lotes verificados en los últimos 90 días quedaron por debajo de la "
                          "supervivencia publicada (el peor, «%(lote)s», %(d).1f pp).") % {
                    "n": len(bajas), "lote": peor.product_id.name, "d": peor.larvae_survival_diff},
                "url": "/marketplace/sales", "link_label": _("Ver ventas"),
            })

    @api.model
    def _panel_laboratorio(self, partner, data):
        super()._panel_laboratorio(partner, data)
        self._verified_survival(partner, "laboratorio", data)

    @api.model
    def _panel_semillero(self, partner, data):
        super()._panel_semillero(partner, data)
        self._verified_survival(partner, "semillero", data)
