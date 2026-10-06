"""19.0.1.1.0 — endurecimiento de seguridad.

shrimp.price.list.line, shrimp.price.list.bonus, shrimp.aguaje y
shrimp.lot.alert pasan a tener uuid_ref (las rutas y formularios ya no
aceptan ids). El mixin rellena los códigos en _auto_init; aquí solo se
comprueba que no haya quedado ninguno vacío. Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    for tabla in ("shrimp_price_list_line", "shrimp_price_list_bonus",
                  "shrimp_aguaje", "shrimp_lot_alert"):
        cr.execute("SELECT to_regclass(%s)", (tabla,))
        if not cr.fetchone()[0]:
            continue
        cr.execute('UPDATE "%s" SET uuid_ref = gen_random_uuid()::text '
                   'WHERE uuid_ref IS NULL' % tabla)
        if cr.rowcount:
            _logger.info("shrimp_packer: %s código(s) uuid_ref asignado(s) en %s.",
                         cr.rowcount, tabla)
