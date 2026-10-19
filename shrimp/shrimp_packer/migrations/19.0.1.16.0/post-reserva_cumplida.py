"""19.0.1.16.0 — la reserva cumplida queda reservada (auditoría, hueco #8).

Los compromisos «cumplidos» anteriores no tenían reserva del lote:

* si la empacadora ya compró las libras liquidadas → reserva «Comprada»;
* si le faltan libras y el lote sigue publicado con stock → se reserva en
  exclusiva para ella DESDE AHORA por el plazo de Ajustes (sin penalizar
  nada hacia atrás; si ya compró una parte, al vencer no cuenta como
  incumplimiento);
* si le faltan libras pero el lote ya no está a la venta → «Plazo vencido»,
  sin incumplimiento.

No se escriben incumplimientos retroactivos de la camaronera: las
declaraciones ya vencidas con compromiso aceptado quedan como estaban.
Idempotente (solo toca compromisos cumplidos sin estado de reserva).
"""
import logging

from odoo import SUPERUSER_ID, api, fields
from odoo.tools import float_compare

from odoo.addons.shrimp_packer.models.shrimp_harvest_reservation import _fmt_dt

_logger = logging.getLogger(__name__)
_P = "shrimp_packer 19.0.1.16.0"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"tracking_disable": True})
    Commitment = env["shrimp.harvest.commitment"]
    cumplidos = Commitment.search([
        ("state", "=", "honored"), ("product_id", "!=", False), ("reservation_state", "=", False)])
    ahora = fields.Datetime.now()
    reservados = comprados = vencidos = 0
    lb_reservadas = 0.0
    for c in cumplidos:
        pendiente = c._reserva_pendiente_lb()
        lote = c.product_id.with_context(active_test=False)
        if float_compare(pendiente, 0.0, precision_digits=2) <= 0:
            c.reservation_state = "bought"
            comprados += 1
        elif lote.active and lote.state == "published" and lote.available_qty > 0:
            c._iniciar_reserva(desde=ahora, avisar=False)
            c._message_log(body="Lote reservado en exclusiva para %s hasta %s (regularización: "
                                "le faltan %.2f lb por comprar)." % (
                                    c.packer_partner_id.name or "", _fmt_dt(c, c.purchase_deadline), pendiente))
            reservados += 1
            lb_reservadas += lote.available_qty
        else:
            c.reservation_state = "lapsed"
            vencidos += 1
    _logger.info("%s: compromisos cumplidos %s: %s reservados desde ahora (%.2f lb de lote), %s ya "
                 "comprados, %s sin lote a la venta (vencidos sin incumplimiento).", _P,
                 len(cumplidos), reservados, lb_reservadas, comprados, vencidos)
