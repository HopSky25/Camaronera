# -*- coding: utf-8 -*-
"""Mi panel (/my/dashboard): acceso al servicio de empaque (copacking) para la
camaronera y la empacadora, con el número de solicitudes en curso."""
from odoo import api, models, _


class ShrimpDashboardCopacking(models.AbstractModel):
    _inherit = "shrimp.dashboard"

    @api.model
    def _copack_shortcut(self, partner, data):
        data["shortcuts"].insert(min(len(data["shortcuts"]), 4), {
            "label": _("Servicio de empaque"), "url": "/marketplace/copacking",
            "icon": "fa-cube", "tone": "coral"})

    @api.model
    def _panel_camaronera(self, partner, data):
        super()._panel_camaronera(partner, data)
        self._copack_shortcut(partner, data)

    @api.model
    def _panel_empacadora(self, partner, data):
        super()._panel_empacadora(partner, data)
        self._copack_shortcut(partner, data)
