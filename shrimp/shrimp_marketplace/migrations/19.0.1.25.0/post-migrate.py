"""19.0.1.25.0 — recepción por el comprador y avisos de la fecha de entrega.

Las compras que ya existían no pasaron por action_confirm con este código:
se decide si llevan recepción (tabla por defecto; shrimp_verification la
reevalúa con su matriz en su propia migración) y el plazo de las que la
llevan empieza HOY, así que el primer cron no confirma sola ninguna compra
vieja: el comprador tiene los N días completos. Los avisos de fechas de
entrega ya pasadas se dan por enviados (no se manda una tanda de «entrega
atrasada» de compras viejas; siguen visibles en Mi panel). Ver
models/shrimp_transaction_reception.py.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    n = env["shrimp.transaction"]._shrimp_backfill_reception()
    _logger.info("Recepción: %s compras confirmadas preparadas (plazo desde hoy)", n)
