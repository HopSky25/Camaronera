"""19.0.1.1.0: bandeja de correo. La actualización instala crm como
dependencia nueva, y crm le pone el alias «info» al equipo de ventas."""

from odoo import SUPERUSER_ID, api

from odoo.addons.shrimp_correo_entrante.hooks import asegurar_token, liberar_alias_de_buzones


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    asegurar_token(env)
    liberar_alias_de_buzones(env)
