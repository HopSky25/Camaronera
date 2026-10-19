"""19.0.1.21.0 — Cierre de compras verificadas y plazos de la verificadora.

1. Compras verificadas atascadas en «Confirmada» con el informe ya aceptado
   por las dos partes: se completan (lote del comprador + ajuste al peso de
   planta), como hace ahora el cierre de la ronda de aceptación. Una por una
   en su savepoint (si una falla, la reintenta el cron horario). Sin correos:
   solo la constancia en la compra.
2. Plazos de la verificadora: cuentan desde HOY para las órdenes abiertas
   (deadline_rules_since), así ninguna vence de golpe al actualizar.
"""
import logging

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.shrimp_verification.models.shrimp_verification_deadline import (
        DEADLINE_SINCE_PARAM)
    ICP = env["ir.config_parameter"].sudo()
    if not ICP.get_param(DEADLINE_SINCE_PARAM):
        ICP.set_param(DEADLINE_SINCE_PARAM, fields.Date.context_today(env.user).isoformat())
    Tx = env["shrimp.transaction"].with_context(tracking_disable=True, mail_notrack=True)
    atascadas = Tx.search([("state", "=", "confirmed"),
                           ("verification_ids.acceptance_state", "=", "closed")])
    cerradas = atascadas._shrimp_close_verified_purchase(silent=True)
    abiertas = env["shrimp.verification"].search_count([
        ("verification_mode", "=", "platform"),
        ("state", "in", ("received", "assigned", "in_field", "done")),
        ("transaction_id.state", "=", "pending_verification")])
    _logger.info("Compras verificadas: %s atascadas en «Confirmada», %s completadas (%s quedan "
                 "para el cron). Plazos de la verificadora desde %s para %s órdenes abiertas.",
                 len(atascadas), len(cerradas), len(atascadas - cerradas),
                 ICP.get_param(DEADLINE_SINCE_PARAM), abiertas)
