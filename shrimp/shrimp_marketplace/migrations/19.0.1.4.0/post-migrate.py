"""19.0.1.4.0 — endurecimiento de seguridad (post).

1. Grupos del backoffice (M7): todos los usuarios internos que ya existían
   reciben "Operador" (group_shrimp_user) para que nadie pierda el acceso
   que tenía. Los administradores del sistema ya son "Administrador" porque
   base.group_system implica group_shrimp_manager.
2. Códigos uuid_ref: el mixin ya rellena los vacíos en cada instalación y
   actualización (_auto_init); aquí no hay nada más que hacer.

Idempotente: volver a ejecutarlo no cambia nada.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    grupo = env.ref("shrimp_marketplace.group_shrimp_user", raise_if_not_found=False)
    if not grupo:
        return
    Users = env["res.users"].with_context(active_test=False)
    internos = Users.search([("share", "=", False)])
    faltan = internos.filtered(lambda u: grupo not in u.group_ids)
    if faltan:
        faltan.write({"group_ids": [(4, grupo.id)]})
    _logger.info("shrimp_marketplace 19.0.1.4.0: grupo Operador asignado a %s usuario(s) interno(s).",
                 len(faltan))
