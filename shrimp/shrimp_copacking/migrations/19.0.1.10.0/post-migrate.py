"""19.0.1.10.0 — reglas de acceso con el socio que ACTÚA (subusuarios).

Las reglas en noupdate de este módulo pasan de user.partner_id a
user.shrimp_actor_partner_id (shrimp_user_registry 19.0.1.6.0): el dueño de la
cuenta y los técnicos siguen viendo lo mismo; un subusuario ve lo de su
empresa.
"""
from odoo.addons.shrimp_user_registry.migration_utils import reglas_con_actor


def migrate(cr, version):
    if not version:
        return
    reglas_con_actor(cr, "shrimp_copacking")
