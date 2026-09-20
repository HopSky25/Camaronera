"""Backfill del código de referencia UUID en todos los registros existentes.

Se agregó el mixin shrimp.uuid.mixin (campo uuid_ref) a todos los modelos
shrimp. Los registros creados antes de esta versión no tienen código; aquí se
les asigna uno único. Cubre también los modelos de shrimp_user_registry, cuyos
campos ya existen para cuando corre este post-migrate.
"""
import uuid

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    for model_name, model in env.items():
        if getattr(model, "_abstract", False) or getattr(model, "_transient", False):
            continue
        if "uuid_ref" not in model._fields:
            continue
        records = model.sudo().with_context(active_test=False).search(
            [("uuid_ref", "=", False)])
        for rec in records:
            rec.uuid_ref = str(uuid.uuid4())
