"""19.0.1.20.0 — recepción por el comprador con confirmación automática.

1. Matriz de verificación: marca «Confirmación de recepción» en las reglas
   generales (sin tipo de producto) de las cuatro parejas de nauplio y larva
   (naupliero → laboratorio, laboratorio → camaronera, naupliero ↔ naupliero,
   laboratorio ↔ laboratorio). Las reglas son noupdate: el -u no las toca.
   Solo se marcan si el campo está en falso en TODAS (primera vez); si un
   administrador ya marcó alguna, no se pisa nada.
2. Reevalúa con la matriz las compras confirmadas que esperan recepción
   (la migración de shrimp_marketplace usó la tabla por defecto): las
   compras verificadas no llevan este paso. El plazo de las que pasan a
   llevarlo empieza HOY (no se confirma sola ninguna compra vieja sin los N
   días completos).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

PAREJAS = [("semillero", "laboratorio"), ("laboratorio", "camaronera"),
           ("semillero", "semillero"), ("laboratorio", "laboratorio")]


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Rule = env["shrimp.verification.rule"].with_context(active_test=False)
    if not Rule.search_count([("requires_reception_confirmation", "=", True)]):
        for vende, compra in PAREJAS:
            reglas = Rule.search([("seller_role", "=", vende), ("buyer_role", "=", compra),
                                  ("product_tipo", "=", False)])
            reglas.write({"requires_reception_confirmation": True})
            _logger.info("Matriz: confirmación de recepción en %s → %s (%s reglas)",
                         vende, compra, len(reglas))
    n = env["shrimp.transaction"]._shrimp_backfill_reception(reevaluate=True)
    _logger.info("Recepción: %s compras confirmadas reevaluadas con la matriz", n)
