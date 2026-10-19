# -*- coding: utf-8 -*-
"""Asistente «Suspender cuenta»: motivo obligatorio (decisión D-20)."""
from odoo import fields, models


class ShrimpAccountSuspend(models.TransientModel):
    _name = "shrimp.account.suspend"
    _description = "Suspender cuenta (motivo)"

    partner_ids = fields.Many2many("res.partner", string="Cuentas", required=True)
    reason = fields.Text(string="Motivo de la suspensión", required=True,
                         help="Se envía por correo al socio.")

    def action_confirm(self):
        self.ensure_one()
        self.partner_ids.action_shrimp_suspend_account(reason=self.reason)
        return {"type": "ir.actions.act_window_close"}
