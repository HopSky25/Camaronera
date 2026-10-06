"""19.0.1.10.0 — barra nueva del marketplace.

«Inicio · Marketplace · Productos ▾ · Operaciones ▾ · Mi cuenta ▾ ·
Contáctanos» pasa a «Mi panel · Marketplace · Vender ▾ · Comprar ▾ ·
Servicios ▾» (+ «Mi cuenta ▾» en el menú de usuario de la derecha).

En cada sitio del marketplace (no en los de verificadores ni empaque):
* products → sell («Vender»), operations → buy («Comprar»),
  account → services («Servicios», /marketplace/empaque), con el orden de la
  barra; el nombre solo se cambia si era el de por defecto.
* «Marketplace» recibe su clave (marketplace) y se crea «Mi panel»
  (/mi-panel, clave panel) si no existe.
* Inicio y Contáctanos no se borran: los oculta website.menu._compute_visible
  al usuario de portal con sesión.

No borra menús. Idempotente (website._shrimp_alinear_menus_portal: un menú
que ya tiene su clave nueva no se toca; los que falten se crean una sola vez
por sitio). La misma función corre en cada actualización desde
data/menu_config.xml.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cambios = env["website"]._shrimp_alinear_menus_portal()
    _logger.info("Barra del marketplace: %s menús alineados/creados", cambios)
