"""19.0.1.34.0 — No hay autoconfirmación de la recepción sin despacho;
cancelación general de compras (registro shrimp.transaction.cancellation).

No cambia datos: las compras directas que esperan la recepción sin despacho
dejan de confirmarse solas. Cuando venza su plazo, el cron las pasará a «No
entregada» (aviso a la plataforma, cancelación sin costo para el comprador).
Se registra cuántas son y cuántas ya tienen el plazo vencido.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Tx = env["shrimp.transaction"]
    hoy = Tx._shrimp_today()
    esperando = Tx.search([("state", "=", "confirmed"), ("reception_required", "=", True),
                           ("reception_status", "=", "waiting"), ("dispatched_at", "=", False)])
    vencidas = esperando.filtered(lambda t: t.reception_deadline and t.reception_deadline < hoy)
    _logger.info("Recepción sin despacho: %s compras directas esperan la recepción sin despacho "
                 "(ya no se autoconfirmarán); %s con el plazo vencido pasarán a «No entregada» en "
                 "la próxima pasada del cron.", len(esperando), len(vencidas))
