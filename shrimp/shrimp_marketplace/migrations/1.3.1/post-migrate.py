"""Backfill del código UUID en registros que aún no lo tienen.

Se agregó el mixin shrimp.uuid.mixin también a res.partner (para exponer el
vendedor por código en las URLs). Este script asigna el código a todos los
registros existentes que aún no lo tengan (incluye res.partner).
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
