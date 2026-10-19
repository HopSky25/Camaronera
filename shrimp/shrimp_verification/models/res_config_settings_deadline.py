"""Ajustes › CamaronMarket › Verificación: plazos de la verificadora
(models/shrimp_verification_deadline.py)."""
from odoo import api, fields, models

from .shrimp_verification_deadline import (
    ASSIGN_HOURS_DEFAULT, ASSIGN_HOURS_PARAM, AUTO_CANCEL_DAYS_DEFAULT, AUTO_CANCEL_DAYS_PARAM,
    REMINDER_HOURS_DEFAULT, REMINDER_HOURS_PARAM, VERDICT_DAYS_DEFAULT, VERDICT_DAYS_PARAM)


class ResConfigSettingsVerificationDeadline(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_ver_deadline_assign_hours = fields.Integer(
        string="Horas para asignar técnico", default=ASSIGN_HOURS_DEFAULT,
        help="Desde que la verificadora recibe la orden.")
    shrimp_ver_deadline_verdict_days = fields.Integer(
        string="Días para emitir el veredicto", default=VERDICT_DAYS_DEFAULT,
        help="Desde la fecha de entrega / cosecha de la compra (o desde la orden, si es "
             "posterior).")
    shrimp_ver_deadline_reminder_hours = fields.Integer(
        string="Recordatorio antes de vencer (horas)", default=REMINDER_HOURS_DEFAULT,
        help="0 = sin recordatorio.")
    shrimp_ver_deadline_auto_cancel_days = fields.Integer(
        string="Cancelar sola tras el vencimiento (días)", default=AUTO_CANCEL_DAYS_DEFAULT,
        help="Si nadie resuelve una verificación vencida (el comprador no cancela ni cambia de "
             "verificadora y la plataforma no la reasigna), la compra se cancela sin costo para el "
             "comprador a los N días del vencimiento. 0 = nunca.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_ver_deadline_assign_hours": (ASSIGN_HOURS_PARAM, ASSIGN_HOURS_DEFAULT, 1, 24 * 60),
            "shrimp_ver_deadline_verdict_days": (VERDICT_DAYS_PARAM, VERDICT_DAYS_DEFAULT, 1, 365),
            "shrimp_ver_deadline_reminder_hours": (
                REMINDER_HOURS_PARAM, REMINDER_HOURS_DEFAULT, 0, 24 * 30),
            "shrimp_ver_deadline_auto_cancel_days": (
                AUTO_CANCEL_DAYS_PARAM, AUTO_CANCEL_DAYS_DEFAULT, 0, 365),
        })
        return specs
