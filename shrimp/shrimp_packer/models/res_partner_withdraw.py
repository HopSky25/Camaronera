# -*- coding: utf-8 -*-
"""D-20: al suspender una cuenta (o rechazar su perfil de empacadora) sus
listas de precios publicadas se retiran (vuelven a borrador o, si ya no
pueden, se archivan)."""
from odoo import _, models
from odoo.exceptions import UserError, ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _shrimp_withdraw_publications(self, roles=None, reason=None):
        res = super()._shrimp_withdraw_publications(roles=roles, reason=reason)
        if roles and "empacadora" not in roles:
            return res
        holder = self.sudo()._shrimp_role_holder()
        listas = self.env["shrimp.price.list"].sudo().search(
            [("issuer_partner_id", "=", holder.id), ("state", "=", "published")])
        for lista in listas:
            try:
                with self.env.cr.savepoint():
                    lista.action_back_to_draft()
            except (UserError, ValidationError):
                with self.env.cr.savepoint():
                    lista.action_archive_list()
        if listas:
            res[_("listas de precios")] = len(listas)
        return res
