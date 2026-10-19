"""19.0.1.31.0 — stock y reservas sin dueño (auditoría de procesos, hueco #9).

Repara lo que dejó el comportamiento anterior (idempotente):

1. Solicitudes de chequeo abiertas sin plazo: reciben el plazo de Ajustes
   contado DESDE AHORA (no se vencen de golpe; el cron las vence después).
   Las de productos ya retirados (archivados o eliminados) se cancelan con
   aviso a las partes: nadie las podía aprobar.
2. Productos «zombis» (eliminados = cancel, pero activos porque el portal los
   reactivaba): vuelven a eliminados (inactivos).
3. Productos eliminados con stock «disponible»: el stock propio del vendedor
   se da de baja con un movimiento «Baja (producto eliminado)» por lote.
   Los que tuvieran operaciones abiertas NO se tocan y se informan en el log.
"""
import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)
_P = "shrimp_marketplace 19.0.1.31.0"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"tracking_disable": True})
    Check = env["shrimp.check.request"]
    Product = env["shrimp.product"].with_context(active_test=False)

    # 1) Chequeos abiertos
    abiertas = Check.search([("state", "in", ("requested", "under_review"))])
    retirados = abiertas.filtered(lambda c: not c.product_id.active or c.product_id.state == "cancel")
    for chk in retirados:
        chk._shrimp_close_open("cancelled", (
            "La solicitud %s se canceló porque el producto ya no está en el marketplace "
            "(retirado por el vendedor). La cantidad reservada se libera.") % (chk.name or ""))
    vivas = (abiertas - retirados).filtered(lambda c: not c.response_deadline)
    # 19.0.1.38.0 retiró el flujo (D-28): ya no hay plazo de respuesta; esa
    # migración cancela TODAS las abiertas y avisa a las partes.
    plazo = None
    if hasattr(Check, "_shrimp_response_hours"):
        plazo = fields.Datetime.now() + timedelta(hours=Check._shrimp_response_hours())
    if vivas and plazo:
        vivas.write({"response_deadline": plazo})
    _logger.info("%s: chequeos abiertos %s (reservan %.2f u.): %s con plazo hasta %s, %s cancelados "
                 "por producto retirado.", _P, len(abiertas), sum(abiertas.mapped("qty")),
                 len(vivas), plazo, len(retirados))

    # 2) Zombis: eliminados y activos
    zombis = Product.search([("state", "=", "cancel"), ("active", "=", True)])
    bloqueados = Product.browse()
    for p in zombis:
        if p._shrimp_retire_blockers():
            bloqueados |= p
    (zombis - bloqueados).with_context(shrimp_retire_force=True).write({"active": False})
    _logger.info("%s: productos zombis (cancel + activo) %s: %s vuelven a eliminados; %s con "
                 "operaciones abiertas sin tocar %s.", _P, len(zombis), len(zombis - bloqueados),
                 len(bloqueados), bloqueados.ids)

    # 3) Stock de los eliminados
    eliminados = Product.search([("state", "=", "cancel")]) - bloqueados
    con_stock = eliminados.filtered(lambda p: env["shrimp.stock.lot"].search_count([
        ("product_id", "=", p.id), ("owner_id", "=", p.seller_partner_id.id),
        ("state", "=", "available"), ("available_qty", ">", 0)]))
    abiertos = con_stock.filtered(lambda p: p._shrimp_retire_blockers())
    a_dar_de_baja = con_stock - abiertos
    moves = a_dar_de_baja._shrimp_write_off_stock(
        "Baja de stock: producto eliminado (regularización 19.0.1.31.0)")
    _logger.info("%s: productos eliminados con stock %s: %s dados de baja con %s movimiento(s) "
                 "(%.2f u.); %s con operaciones abiertas sin tocar %s.", _P, len(con_stock),
                 len(a_dar_de_baja), len(moves), sum(moves.mapped("qty")), len(abiertos), abiertos.ids)
