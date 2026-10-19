"""19.0.1.9.0: las cuentas rechazadas no conservan claves de API activas.

Se DESACTIVAN (active=False, no se revocan) las claves de los usuarios de
cuentas cuyo perfil activo está rechazado; el administrador puede
reactivarlas si la cuenta se aprueba. Las cuentas pendientes conservan sus
claves en solo lectura (la puerta está en controllers/framework.py)."""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    keys = env["shrimp.api.key"]._shrimp_disable_keys_of_rejected()
    _logger.warning("shrimp_api %s: %s clave(s) de API desactivada(s) por cuenta rechazada: %s",
                    "19.0.1.9.0", len(keys), ", ".join(keys.mapped("display_key")) or "-")
