"""«Servicios ▾ > Servicio de empaque» en la barra del marketplace.

Antes era un <li> inyectado por xpath en el desplegable «Operaciones»
(views/navbar_inherit.xml). Solo para quien es dueño del camarón (capacidad
request_copack: camaronera y empacadora); el maquilador presta el servicio
desde su propio sitio.
"""
from odoo import api, models


class Website(models.Model):
    _inherit = "website"

    @api.model
    def _shrimp_nav_entries(self, partner):
        entradas = super()._shrimp_nav_entries(partner)
        entradas.append(
            {"section": "services", "key": "copack", "sequence": 10,
             "label": "Servicio de empaque",
             "url": "/marketplace/copacking", "icon": "fa-cube", "tone": "blue",
             "caps": ("request_copack",),
             "desc": "Plantas maquiladoras: pide servicio y sigue tus órdenes."})
        return entradas
