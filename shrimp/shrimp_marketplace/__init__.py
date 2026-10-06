from . import controllers
from . import models


def _shrimp_bautizar_sitio_principal(env):
    """Solo en instalaciones nuevas: el sitio por defecto de Odoo («My
    Website») pasa a llamarse como la plataforma y se sirve en español. En
    una base existente no se toca el nombre que haya puesto el cliente."""
    principal = env["website"].sudo()._shrimp_main_site()
    if principal and principal.name in ("My Website", "Mi sitio web"):
        principal.name = "CamaronMarket"
    env["website"].sudo().search([])._shrimp_set_spanish_default()


def post_init_hook(env):
    _shrimp_bautizar_sitio_principal(env)
    _shrimp_asignar_portal_demo(env)


def _shrimp_asignar_portal_demo(env):
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
