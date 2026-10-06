"""«Mi cuenta ▾ > Integraciones (API)» en la barra del marketplace."""
from odoo import api, models


class Website(models.Model):
    _inherit = "website"

    @api.model
    def _shrimp_nav_entries(self, partner):
        entradas = super()._shrimp_nav_entries(partner)
        # Las claves de API son para cuentas de socios del portal (como la
        # tarjeta de /my): un usuario interno no las usa.
        if partner and any(u.share for u in partner.sudo().user_ids):
            entradas.append(
                {"section": "account", "key": "api_keys", "sequence": 40,
                 "label": "Integraciones (API)",
                 "url": "/my/api-keys", "icon": "fa-plug", "tone": "navy",
                 "caps": ()})
        return entradas
