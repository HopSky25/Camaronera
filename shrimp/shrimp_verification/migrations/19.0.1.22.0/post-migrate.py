"""D-40 (decisión del usuario): el honorario que se cobra es la tarifa publicada
por cada verificadora (ver_fee_base + ver_fee_por_lb × cantidad).

Algunas cuentas tenían ver_fee_por_lb escrito en CENTAVOS (1,5 / 2 / 3) cuando
el campo es en dólares por libra: con la tarifa propia eso cobraría 2-3 USD por
libra. Toda tarifa por libra ≥ 0,50 USD se divide entre 100, con constancia en
el chatter de la verificadora y en el log del -u.
"""
import logging

from markupsafe import Markup

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

UMBRAL = 0.5


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    socios = env["res.partner"].search([("ver_fee_por_lb", ">=", UMBRAL)])
    for socio in socios:
        antes = socio.ver_fee_por_lb
        despues = round(antes / 100.0, 6)
        socio.write({"ver_fee_por_lb": despues})
        _logger.info("D-40: tarifa por libra de %s (id %s) corregida de %s a %s USD/lb",
                     socio.name, socio.id, antes, despues)
        try:
            socio.message_post(body=Markup(
                "<p>Tarifa por libra corregida de <b>%s</b> a <b>%s USD/lb</b>: el valor "
                "estaba en centavos. Desde esta versión se cobra la tarifa publicada por la "
                "verificadora (D-40).</p>") % (antes, despues))
        except Exception:  # noqa: BLE001
            _logger.warning("D-40: sin chatter para %s", socio.name)
    _logger.info("D-40: %s tarifas por libra corregidas (≥ %s USD/lb).", len(socios), UMBRAL)
