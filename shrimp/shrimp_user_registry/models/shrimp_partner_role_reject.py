# -*- coding: utf-8 -*-
"""Asistente «Rechazar perfil»: motivo obligatorio (decisión D-18)."""
from odoo import fields, models


class ShrimpPartnerRoleReject(models.TransientModel):
    _name = "shrimp.partner.role.reject"
    _description = "Rechazar perfil (motivo)"

    role_ids = fields.Many2many("shrimp.partner.role", string="Perfiles", required=True)
    reason = fields.Text(string="Motivo del rechazo", required=True,
                         help="Se envía por correo al socio y se le muestra en «Mi cuenta».")

    def action_confirm(self):
        self.ensure_one()
        self.role_ids.action_reject(reason=self.reason)
        return {"type": "ir.actions.act_window_close"}
