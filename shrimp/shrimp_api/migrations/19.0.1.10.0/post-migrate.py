"""19.0.1.10.0 — D-46: ninguna clave de API sin caducidad.

Las claves que no tenían fecha de caducidad (las heredadas de la API anterior:
56 en Pruebas) reciben un año (shrimp_api.key_lifetime_days, 365 por defecto)
desde la fecha de esta actualización. Idempotente: solo toca expires_at NULL.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    days = env["shrimp.api.key"]._lifetime_days()
    cr.execute("""
        UPDATE shrimp_api_key
           SET expires_at = (now() at time zone 'utc') + %s * interval '1 day',
               expiry_notice_sent_at = NULL
         WHERE expires_at IS NULL
     RETURNING id
    """, (days,))
    n = len(cr.fetchall())
    _logger.warning("shrimp_api 19.0.1.10.0: %s clave(s) de API sin caducidad reciben %s días "
                    "desde hoy (D-46).", n, days)
