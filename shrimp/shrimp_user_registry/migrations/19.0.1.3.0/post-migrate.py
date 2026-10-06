"""19.0.1.3.0 — varios perfiles por cuenta.

Cada socio con shrimp_user_type recibe su primera línea de perfil
(shrimp.partner.role) con el estado de aprobación de la cuenta
(shrimp_account_state). shrimp_user_type sigue siendo el perfil ACTIVO, así
que nada cambia para quien tiene un solo rol. Idempotente.
"""
import logging

from odoo.addons.shrimp_user_registry.migration_utils import crear_perfiles_desde_tipo

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    n = crear_perfiles_desde_tipo(cr)
    _logger.info("shrimp_user_registry 19.0.1.3.0: %s perfiles creados desde shrimp_user_type", n)
