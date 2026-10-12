"""19.0.1.1.0: el tipo de identificación del SRI de los socios.

El módulo SRI arma el receptor del XML con ``ec_sri_identification_type``
(04 RUC, 05 cédula, 06 pasaporte, 07 consumidor final), y antes solo se
sincronizaba el tipo de l10n_latam. Se rellena para los socios existentes,
con la misma regla que al registrarse. Idempotente.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["res.partner"].search([
        ("shrimp_user_type", "!=", False), ("vat", "!=", False),
    ])._shrimp_sync_identification_type()
