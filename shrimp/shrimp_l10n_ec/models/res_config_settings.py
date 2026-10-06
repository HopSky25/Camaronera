from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_service_journal_id = fields.Many2one(
        related="company_id.shrimp_service_journal_id", readonly=False)
    shrimp_service_sri_point_id = fields.Many2one(
        related="company_id.shrimp_service_sri_point_id", readonly=False)
    shrimp_service_payment_id = fields.Many2one(
        related="company_id.shrimp_service_payment_id", readonly=False)
    shrimp_sri_auto_emit = fields.Boolean(
        related="company_id.shrimp_sri_auto_emit", readonly=False)
