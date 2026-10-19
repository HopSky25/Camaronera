"""19.0.1.6.0 — documentos legales versionados y cuentas con subusuarios.

1. Cuentas de empresa: cada usuario portal activo cuyo contacto tiene perfiles
   (shrimp_user_type) pasa a ser el ADMINISTRADOR de su cuenta; cada técnico
   de campo de una verificadora, un subusuario de su empresa con el permiso
   «aprobar» del perfil Verificador (lo que ya hacía).
2. Aceptaciones: los socios que tenían registrada la aceptación de la
   política (shrimp_privacy_version) reciben una aceptación de Privacidad y
   Términos de esa versión, canal «migración», sin comprobante PDF (no se
   puede reconstruir la IP ni el navegador del momento).
Las tablas y los datos semilla (documentos y versiones 2026-10) ya los
cargó el -u antes de este script. Idempotente.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    creados = env["shrimp.account.member"]._shrimp_migrate_existing_users()
    aceptaciones = env["shrimp.legal.acceptance"]._shrimp_migrate_legacy_privacy()
    _logger.info("shrimp 19.0.1.6.0: %s usuarios de cuenta y %s aceptaciones migradas",
                 creados, aceptaciones)
