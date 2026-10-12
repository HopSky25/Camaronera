"""19.0.1.5.0 — dispositivos de la app y operaciones firmadas.

Las claves que emitió POST /auth/login antes de existir ``is_app_key`` se
reconocen por su nombre («App móvil – <dispositivo>», el que les pone
controllers/auth.py) y se marcan como claves de la app: desde ahora sus
operaciones sensibles exigen dispositivo registrado y firma
(docs/app/CONTRATO-FIRMA-DISPOSITIVO.md). Las claves creadas a mano en el
portal no se tocan.

Idempotente: solo toca claves con prefijo nuevo que aún no están marcadas.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE shrimp_api_key
           SET is_app_key = TRUE
         WHERE key_prefix IS NOT NULL
           AND key_prefix NOT LIKE 'legacy:%%'
           AND name LIKE %s
           AND is_app_key IS NOT TRUE
    """, ("App móvil – %",))
    _logger.info("shrimp_api 19.0.1.5.0: %s claves «App móvil» marcadas como claves de la app",
                 cr.rowcount)
