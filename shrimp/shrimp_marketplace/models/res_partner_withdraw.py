# -*- coding: utf-8 -*-
"""D-20: al suspender una cuenta (o rechazar uno de sus perfiles) se retiran
sus lotes publicados: vuelven a borrador. Las compras en curso no se tocan."""
from odoo import _, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _shrimp_withdraw_publications(self, roles=None, reason=None):
        res = super()._shrimp_withdraw_publications(roles=roles, reason=reason)
        holder = self.sudo()._shrimp_role_holder()
        Product = self.env["shrimp.product"].sudo()
        productos = Product.search([("seller_partner_id", "=", holder.id),
                                    ("state", "=", "published")])
        if roles:
            productos = productos.filtered(
                lambda p: (p.seller_role or p._shrimp_seller_role()) in roles)
        if productos:
            productos.with_context(shrimp_system_flow=True).write({"state": "draft"})
            for p in productos:
                p.message_post(body=_("Retirado de la venta: %s") % (
                    reason or _("cuenta suspendida o perfil rechazado")))
            res[_("lotes")] = len(productos)
        return res
