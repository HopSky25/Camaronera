from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_verification_fee = fields.Float(
        string="Honorario de verificación",
        config_parameter="shrimp_verification.fee",
        default=500.0,
        help="Lo que cobra el verificador por ir a campo e inspeccionar el lote. "
             "Se registra en cada verificación al crearla; cambiarlo aquí no "
             "altera las verificaciones ya emitidas.",
    )
