"""19.0.1.4.0 — el empaque mueve el inventario.

Órdenes de empaque firmadas o cerradas antes de esta versión: no movieron
nada (la merma seguía publicada y el producto empacado no tenía lote). Se
regularizan con el mismo método que usan las órdenes nuevas: empaque y merma
salen del lote del cliente (solo lo que el lote todavía tenga) y nace el lote
empacado. Lo que el lote ya no tiene (porque se vendió antes de esta
versión) se da por vendido del producto empacado.

Idempotente: las órdenes que ya tienen lote empacado o movimientos de
empaque no se tocan.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env["shrimp.copack.order"].sudo().search([
        ("state", "in", ("signed", "closed")), ("es_facturable", "=", True),
        ("packed_lot_id", "=", False)], order="packed_date asc, id asc")
    hechas = 0
    for order in orders:
        if order.packing_move_ids:
            continue
        if order._shrimp_apply_packing_stock():
            hechas += 1
    _logger.info("Órdenes de empaque regularizadas (lote empacado): %s de %s", hechas, len(orders))
