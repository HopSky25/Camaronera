"""19.0.1.4.0 — reglas de acceso con el socio que ACTÚA (subusuarios).

user.partner_id -> user.shrimp_actor_partner_id (shrimp_user_registry
19.0.1.6.0). Las reglas de este módulo no están en noupdate (el -u ya las
recarga); se repite aquí por si alguna se cargó como noupdate. Idempotente.
"""
from odoo.addons.shrimp_user_registry.migration_utils import reglas_con_actor


def migrate(cr, version):
    if not version:
        return
    reglas_con_actor(cr, "shrimp_api")
