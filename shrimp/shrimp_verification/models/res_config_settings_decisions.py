"""Ajustes › CamaronMarket › Verificación: plazos de las decisiones de la
auditoría de procesos (19.0.1.24.0)."""
from odoo import api, fields, models

from .shrimp_verification_accreditation import WARN_DAYS_DEFAULT, WARN_DAYS_PARAM
from .shrimp_verification_declared_deadline import DECLARED_DAYS_DEFAULT, DECLARED_DAYS_PARAM
from .shrimp_verification_response import RESPONSE_HOURS_DEFAULT, RESPONSE_HOURS_PARAM
from .shrimp_verification_second_opinion import CONTEST_HOURS_DEFAULT, CONTEST_HOURS_PARAM


class ResConfigSettingsVerificationDecisions(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_ver_response_hours = fields.Integer(
        string="Horas para aceptar o declinar la orden", default=RESPONSE_HOURS_DEFAULT,
        help="Desde que la verificadora recibe la orden. Declinada o sin respuesta, el comprador "
             "elige otra verificadora o cancela sin costo.")
    shrimp_ver_accreditation_warn_days = fields.Integer(
        string="Avisar el vencimiento de la acreditación (días antes)", default=WARN_DAYS_DEFAULT)
    shrimp_ver_contest_hours = fields.Integer(
        string="Horas para impugnar un rechazo (segunda opinión)", default=CONTEST_HOURS_DEFAULT,
        help="0 = sin segunda opinión: el rechazo queda firme en el acto.")
    shrimp_ver_declared_days = fields.Integer(
        string="Días para presentar la verificación declarada", default=DECLARED_DAYS_DEFAULT,
        help="Desde la fecha de entrega. Vencido sin presentar, la compra se cancela sin costo.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_ver_response_hours": (RESPONSE_HOURS_PARAM, RESPONSE_HOURS_DEFAULT, 1, 24 * 30),
            "shrimp_ver_accreditation_warn_days": (WARN_DAYS_PARAM, WARN_DAYS_DEFAULT, 1, 365),
            "shrimp_ver_contest_hours": (CONTEST_HOURS_PARAM, CONTEST_HOURS_DEFAULT, 0, 24 * 30),
            "shrimp_ver_declared_days": (DECLARED_DAYS_PARAM, DECLARED_DAYS_DEFAULT, 1, 365),
        })
        return specs
