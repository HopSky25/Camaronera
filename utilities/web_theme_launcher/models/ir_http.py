# -*- coding: utf-8 -*-
from odoo import models

DEFAULT_PRIMARY = "#123e5c"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Expose the theme settings to the web client so the launcher JS can
        apply the primary color and each user's dark-mode preference without an
        extra RPC round-trip."""
        result = super().session_info()
        icp = self.env["ir.config_parameter"].sudo()
        result["wtl_primary_color"] = (
            icp.get_param("web_theme_launcher.primary_color") or DEFAULT_PRIMARY
        )
        user = self.env.user
        result["wtl_dark_mode"] = bool(
            user and not user._is_public() and user.wtl_dark_mode
        )
        return result
