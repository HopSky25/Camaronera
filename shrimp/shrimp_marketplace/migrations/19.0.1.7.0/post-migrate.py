"""19.0.1.7.0 — trazabilidad completa y cantidades físicas.

1. Siembras de origen de los productos de cosecha (origin_allocation_ids):
   se proponen a partir de la piscina de origen y las siembras anteriores a
   la fecha de producción. Es el eslabón que unía la cosecha con la larva.
2. Las siembras de camaroneras (lote comprado, no revendible) consumen el
   lote: se crea el movimiento «sowing» que faltaba y se descuenta lo que el
   lote todavía tenga (nunca por debajo de cero).
3. Fecha real de recepción de las compras completadas (received_date), a
   partir de la creación del lote del comprador.
4. Ruido de «Evolución»: se borran las filas «Actualización automática del
   producto» que no cambiaban nada biológico respecto a la anterior (solo
   reflejaban stock o estado comercial).

Idempotente: cada paso solo toca lo que falta.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _backfill_received_date(cr):
    cr.execute("""
        UPDATE shrimp_transaction t
           SET received_date = sub.fecha
          FROM (SELECT m.transaction_id AS tx_id, MIN(l.create_date) AS fecha
                  FROM shrimp_stock_move m
                  JOIN shrimp_stock_lot l ON l.origin_move_id = m.id
                 GROUP BY m.transaction_id) sub
         WHERE sub.tx_id = t.id AND t.state = 'done' AND t.received_date IS NULL
    """)
    cr.execute("""
        UPDATE shrimp_transaction SET received_date = write_date
         WHERE state = 'done' AND received_date IS NULL
    """)


def _backfill_sowing_moves(env):
    Alloc = env["shrimp.lot.allocation"].sudo()
    allocs = Alloc.search([("state", "in", ("allocated", "released")),
                           ("sowing_move_id", "=", False)], order="allocation_date asc, id asc")
    hechos = 0
    for alloc in allocs:
        if not alloc._shrimp_consumes_lot():
            continue
        lot = alloc.stock_lot_id
        qty = min(alloc.allocated_qty, lot.available_qty)
        if qty <= 0:
            continue
        move = lot._shrimp_internal_move(
            "sowing", qty, "Siembra en %s (regularización 19.0.1.7.0)" % (alloc.pond_id.display_name or ""),
            allocation_id=alloc.id,
            date=env["shrimp.transaction"].shrimp_noon_utc(alloc.allocation_date, alloc.partner_id)
            if alloc.allocation_date else None)
        alloc.with_context(shrimp_alloc_internal=True).write({"sowing_move_id": move.id})
        hechos += 1
    _logger.info("Siembras regularizadas (consumo del lote): %s", hechos)


def _backfill_origin_allocations(env):
    prods = env["shrimp.product"].with_context(active_test=False).sudo().search([
        ("origin_pond_id", "!=", False), ("origin_allocation_ids", "=", False)])
    prods._shrimp_autofill_origin_allocations()
    _logger.info("Productos con siembras de origen propuestas: %s",
                 len(prods.filtered("origin_allocation_ids")))


def _drop_evolution_noise(cr):
    cr.execute("""
        WITH ord AS (
            SELECT id, note,
                   stage_id, avg_size_mg, survival_rate, COALESCE(health_status, '') AS hs,
                   LAG(stage_id) OVER w AS p_stage, LAG(avg_size_mg) OVER w AS p_size,
                   LAG(survival_rate) OVER w AS p_surv,
                   LAG(COALESCE(health_status, '')) OVER w AS p_hs,
                   LAG(id) OVER w AS p_id
              FROM shrimp_product_evolution
            WINDOW w AS (PARTITION BY product_id ORDER BY date, id)
        )
        DELETE FROM shrimp_product_evolution e
         USING ord
         WHERE e.id = ord.id
           AND ord.p_id IS NOT NULL
           AND ord.note = 'Actualización automática del producto.'
           AND ord.stage_id IS NOT DISTINCT FROM ord.p_stage
           AND ord.avg_size_mg IS NOT DISTINCT FROM ord.p_size
           AND ord.survival_rate IS NOT DISTINCT FROM ord.p_surv
           AND ord.hs = ord.p_hs
    """)
    _logger.info("Filas de evolución sin cambio biológico eliminadas: %s", cr.rowcount)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _backfill_received_date(cr)
    _backfill_sowing_moves(env)
    _backfill_origin_allocations(env)
    _drop_evolution_noise(cr)
