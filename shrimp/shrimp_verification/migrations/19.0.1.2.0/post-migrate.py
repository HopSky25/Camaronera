"""19.0.1.2.0 — endurecimiento de seguridad.

- Las fotos de evidencia se sirven ahora por el access_token del adjunto (las
  URLs ya no llevan el id). Se generan los tokens que falten en las fotos ya
  existentes para que los enlaces funcionen desde el primer momento.
- shrimp.tech.role y shrimp.verification.acceptance pasan a tener uuid_ref:
  el mixin lo rellena en _auto_init (no hace falta nada aquí).

Idempotente.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    fotos = env["shrimp.verification"].with_context(active_test=False).search(
        []).mapped("photo_ids").filtered(lambda a: not a.access_token)
    if fotos:
        fotos.generate_access_token()
