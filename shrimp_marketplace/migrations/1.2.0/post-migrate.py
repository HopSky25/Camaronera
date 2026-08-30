"""Traslada la comisión global (config_parameter) a la unidad de medida libra.

Antes la comisión del marketplace era un único valor global
(shrimp_marketplace.commission_cents). Ahora cada Unidad de medida tiene su
propia tarifa (shrimp.uom.commission_cents). Este script copia el valor
existente a la unidad 'libra' para no perder la configuración previa.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    rate = float(env["ir.config_parameter"].sudo().get_param(
        "shrimp_marketplace.commission_cents") or 0.0)
    if rate <= 0:
        return

    libra = env.ref("shrimp_marketplace.uom_libra", raise_if_not_found=False)
    if libra and not libra.commission_cents:
        libra.commission_cents = rate
