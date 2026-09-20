from . import controllers
from . import models


def post_init_hook(env):
    """Asigna el grupo Portal a los usuarios de datos de ejemplo.

    El campo de grupos del usuario cambió de nombre entre versiones de Odoo
    (`groups_id` en 18, `group_ids` en 19), por eso no se asigna en el XML
    —que debe ser compatible con ambas— sino aquí, detectando el nombre real.
    """
    portal = env.ref("base.group_portal", raise_if_not_found=False)
    if not portal:
        return

    field = "group_ids" if "group_ids" in env["res.users"]._fields else "groups_id"

    demo_users = env["ir.model.data"].search([
        ("module", "=", "shrimp_marketplace"),
        ("model", "=", "res.users"),
    ])
    users = env["res.users"].browse(demo_users.mapped("res_id")).exists()
    if users:
        # (6, 0, [portal]) reemplaza los grupos: deja el usuario como Portal.
        users.write({field: [(6, 0, [portal.id])]})
