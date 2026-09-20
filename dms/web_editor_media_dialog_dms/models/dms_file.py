# Copyright 2024 Tecnativa - Carlos Roca
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import models


class File(models.Model):
    _inherit = "dms.file"

    def get_access_token(self):
        """Called over RPC by the media dialog when the editor asks for a link
        that public visitors can open."""
        self.ensure_one()
        return self._portal_ensure_token()
