# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    wtl_dark_mode = fields.Boolean(
        string="Modo oscuro",
        default=False,
        help="Activa el tema oscuro del backend solo para este usuario.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        # Allow each user to read/write their own dark-mode preference.
        return super().SELF_READABLE_FIELDS + ["wtl_dark_mode"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["wtl_dark_mode"]
