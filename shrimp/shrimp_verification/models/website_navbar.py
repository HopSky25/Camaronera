"""El verificador en la barra del marketplace (sitio principal).

Antes, para el verificador, el desplegable «Productos» se sustituía por uno
propio (plantilla submenu_mi_panel_verificador, por xpath). Con la barra
nueva sus accesos van en «Servicios ▾ > Verificación» y su cuenta en
«Mi cuenta ▾». El sitio de verificadores tiene su propio menú
(verifier_jobs / verifier_company) y no cambia.
"""
from odoo import api, models

_VERIFICACION = [
    ("verify_assigned", "Compras por verificar", "/verifier/inbox?state=assigned", "fa-inbox", "teal"),
    ("verify_field", "En campo", "/verifier/inbox?state=in_field", "fa-flask", "amber"),
    ("verify_done", "Por dictaminar", "/verifier/inbox?state=done", "fa-gavel", "navy"),
    ("verify_approved", "Ya verificadas", "/verifier/inbox?state=approved", "fa-check-circle", "green"),
    ("verify_all", "Todas mis verificaciones", "/verifier/inbox", "fa-list", "blue"),
]


class Website(models.Model):
    _inherit = "website"

    @api.model
    def _shrimp_nav_entries(self, partner):
        entradas = super()._shrimp_nav_entries(partner)
        for i, (key, label, url, icon, tone) in enumerate(_VERIFICACION):
            entradas.append(
                {"section": "services", "key": key, "sequence": 100 + i,
                 "label": label, "url": url, "icon": icon, "tone": tone,
                 "caps": ("verify",), "group": "Verificación"})
        return entradas
