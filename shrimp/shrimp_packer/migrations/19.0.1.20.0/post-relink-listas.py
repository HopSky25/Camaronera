"""19.0.1.20.0 — D-49: lotes atados a una lista de precios archivada pasan a
la lista vigente del mismo emisor (o se desatan si no la hay), con aviso al
vendedor."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    movidos, desatados = env["shrimp.price.list"]._shrimp_relink_archived_migrate()
    _logger.info("D-49 listas archivadas: %s lotes pasaron a la lista vigente del emisor, %s "
                 "quedaron sin lista.", len(movidos), len(desatados))
