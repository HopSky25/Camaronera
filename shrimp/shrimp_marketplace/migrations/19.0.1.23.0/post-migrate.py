"""19.0.1.23.0 — «Mi panel» vuelve a la barra del marketplace.

Causa (base Pruebas): los website.menu del sitio se rehicieron el
2026-10-12 (website.main_menu recreado a las 00:35 y los menús de
shrimp_marketplace a las 01:07), pero el parámetro
shrimp_marketplace.navbar_v2_sites ya decía «1,2,tpl»: para
website._shrimp_alinear_menus_portal() el sitio estaba «hecho» y no volvió a
crear «Mi panel» (ni en el sitio ni la plantilla sin sitio). Resultado: la
barra empezaba en «Marketplace» para todos los perfiles.

Ahora «Mi panel» es obligatorio (_SHRIMP_NAV_REQUIRED): se crea siempre que
falte, en cada actualización (data/menu_config.xml) y aquí. Idempotente; no
toca los sitios de verificadores ni de empaque.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cambios = env["website"]._shrimp_alinear_menus_portal()
    _logger.info("Barra del marketplace: «Mi panel» restaurado (%s menús creados/alineados)", cambios)
