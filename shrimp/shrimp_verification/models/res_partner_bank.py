from odoo import fields, models


class ResPartnerBank(models.Model):
    """Datos de la cuenta del verificador que el estándar no trae."""

    _inherit = "res.partner.bank"

    shrimp_account_type = fields.Selection(
        [("ahorros", "Ahorros"), ("corriente", "Corriente")], string="Tipo de cuenta")
    shrimp_holder_id = fields.Char(string="Cédula/RUC del titular")
