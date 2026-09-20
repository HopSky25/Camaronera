# -*- coding: utf-8 -*-
from odoo import fields, models

DEFAULT_PRIMARY = "#123e5c"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    wtl_primary_color = fields.Char(
        string="Color principal del tema",
        config_parameter="web_theme_launcher.primary_color",
        default=DEFAULT_PRIMARY,
        help="Color corporativo aplicado a la barra superior y a los botones "
        "y acentos del backend. Formato hexadecimal, p. ej. #123e5c.",
    )

    # --- PWA (Aplicación Web Progresiva / "Instalar aplicación") ---
    wtl_pwa_name = fields.Char(
        string="Nombre del aplicativo",
        config_parameter="web_theme_launcher.pwa_name",
        help="Nombre con el que se instala la aplicación (PWA). "
        "Si se deja vacío se usa el nombre de la empresa.",
    )
    wtl_pwa_use_primary_color = fields.Boolean(
        string="Usar color principal del módulo",
        config_parameter="web_theme_launcher.pwa_use_primary_color",
        default=True,
        help="Si está activo, la barra del aplicativo usa el color principal "
        "del tema. Desactívalo para elegir un color distinto.",
    )
    wtl_pwa_bar_color = fields.Char(
        string="Color de la barra del aplicativo",
        config_parameter="web_theme_launcher.pwa_bar_color",
        help="Color de la barra/tema de la aplicación instalable cuando no se "
        "usa el color principal. Formato hexadecimal (#RRGGBB).",
    )
    # El icono se guarda como base64 en un parámetro del sistema (los campos
    # Binary no admiten config_parameter, así que se maneja a mano).
    wtl_pwa_icon = fields.Binary(
        string="Icono del aplicativo",
        help="Icono con el que se instala la aplicación (PWA). Idealmente PNG "
        "cuadrado (p. ej. 512x512). Si se deja vacío se usa el logo de la empresa.",
    )

    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        res["wtl_pwa_icon"] = icp.get_param("web_theme_launcher.pwa_icon") or False
        return res

    def set_values(self):
        super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("web_theme_launcher.pwa_icon", self.wtl_pwa_icon or "")
