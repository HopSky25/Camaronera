"""Decisiones de la auditoría de procesos (19.0.1.24.0): piso de los plazos
nuevos (D-41: las declaraciones en preparación que ya existían no vencen de
golpe; cuentan desde hoy)."""
import logging

from odoo import api, fields, SUPERUSER_ID

_logger = logging.getLogger(__name__)

DECISIONS_SINCE_PARAM = "shrimp_verification.decisions_rules_since"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env["ir.config_parameter"].sudo()
    if not ICP.get_param(DECISIONS_SINCE_PARAM):
        hoy = env["shrimp.transaction"]._shrimp_today()
        ICP.set_param(DECISIONS_SINCE_PARAM, fields.Date.to_string(hoy))
        _logger.info("Plazos de las decisiones de verificación desde %s", hoy)
