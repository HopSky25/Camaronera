"""19.0.1.13.0 — backoffice en tres aplicaciones (post).

Nadie pierde lo que veía: antes todo usuario interno con «Operador» veía la app
única «Camaronera» completa. Ahora la app «Copacking» la ven sus propios grupos, así
que quien tenía Operador recibe «Copacking · Usuario» y quien tenía Administrador
(de los datos de la plataforma) recibe «Copacking · Administrador». Los
administradores del sistema ya lo tienen por implicación (base.group_system).

Idempotente: volver a ejecutarlo no cambia nada.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

PARES = [
    ("shrimp_marketplace.group_shrimp_user", "shrimp_copacking.group_copacking_user"),
    ("shrimp_marketplace.group_shrimp_manager", "shrimp_copacking.group_copacking_manager"),
]


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    sistema = env.ref("base.group_system")
    Users = env["res.users"].with_context(active_test=False)
    for origen, destino in PARES:
        g_origen = env.ref(origen, raise_if_not_found=False)
        g_destino = env.ref(destino, raise_if_not_found=False)
        if not g_origen or not g_destino:
            continue
        usuarios = Users.search([
            ("share", "=", False),
            ("all_group_ids", "in", [g_origen.id]),
        ]).filtered(lambda u: g_destino not in u.all_group_ids
                    and sistema not in u.all_group_ids)
        if usuarios:
            usuarios.write({"group_ids": [(4, g_destino.id)]})
        _logger.info("shrimp_copacking 19.0.1.13.0: %s asignado a %s usuario(s) interno(s): %s",
                     destino, len(usuarios), ", ".join(usuarios.mapped("login")))
