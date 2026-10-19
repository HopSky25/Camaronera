# -*- coding: utf-8 -*-
"""Mi panel (/my/dashboard) y el servicio de empaque (copacking).

El acceso «Servicio de empaque» ya no se inserta aquí a mano: sale de la
tabla única de la barra (models/website_navbar.py, capacidad
request_copack), que también arma «Accesos» de Mi panel. Así la barra y el
panel no se separan y el subusuario sin permiso de compra no lo ve.
"""
from odoo import models


class ShrimpDashboardCopacking(models.AbstractModel):
    _inherit = "shrimp.dashboard"
