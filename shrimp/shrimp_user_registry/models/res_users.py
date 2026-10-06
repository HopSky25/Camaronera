# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # El perfil activo vive en la cuenta (res.partner.shrimp_user_type), así
    # que persiste entre sesiones y dispositivos. Este campo es solo un acceso
    # cómodo desde el usuario: leerlo da el perfil con el que opera ahora y
    # escribirlo lo cambia (solo a un perfil aprobado).
    shrimp_active_role = fields.Selection(
        selection=lambda self: list(self.env["res.partner"]._fields["shrimp_user_type"].selection),
        string="Perfil activo", compute="_compute_shrimp_active_role",
        inverse="_inverse_shrimp_active_role")

    @api.depends("partner_id.shrimp_user_type", "partner_id.parent_id.shrimp_user_type")
    def _compute_shrimp_active_role(self):
        for user in self:
            user.shrimp_active_role = (user.partner_id._shrimp_effective_type()
                                       if user.partner_id else False) or False

    def _inverse_shrimp_active_role(self):
        for user in self:
            if user.shrimp_active_role:
                user.partner_id.sudo()._shrimp_set_active_role(user.shrimp_active_role)
