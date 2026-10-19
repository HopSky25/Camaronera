"""19.0.1.12.0 — técnicos de campo como subusuarios de su empresa.

La migración de shrimp_user_registry (19.0.1.6.0) corre antes de que este
módulo cargue el campo shrimp_is_field_tech, así que solo pudo crear los
administradores. Aquí se completa: cada técnico pasa a ser subusuario de su
verificadora con el permiso «aprobar» del perfil Verificador (lo que ya
hacía). Las reglas de acceso de este módulo pasan a user.shrimp_actor_partner_id.
Idempotente.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.shrimp_user_registry.migration_utils import reglas_con_actor

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    creados = env["shrimp.account.member"]._shrimp_migrate_existing_users()
    reglas_con_actor(cr, "shrimp_verification")
    _logger.info("shrimp_verification 19.0.1.12.0: %s técnicos como subusuarios", creados)
