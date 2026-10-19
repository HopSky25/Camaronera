"""Ajustes › CamaronMarket › Protección de datos: plazos de las solicitudes
LOPDP y purga de retención (shrimp_user_registry/models/shrimp_data_request.py).

Se guardan con _shrimp_param_specs (también el 0) y el código los lee con su
valor por defecto si no existen (lopdp_param)."""
from odoo import api, fields, models


class ResConfigSettingsLopdp(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_lopdp_deadline_days = fields.Integer(
        string="Plazo de respuesta (días)", default=15,
        help="Días calendario para responder una solicitud de derechos (LOPDP: 15). La Política de "
             "privacidad publicada promete 15 días: si cambias este valor, actualiza también la "
             "política.")
    shrimp_lopdp_reminder_days = fields.Integer(
        string="Avisar antes del vencimiento (días)", default=3,
        help="Desde cuántos días antes del vencimiento se avisa cada día al responsable (y al DPO "
             "por correo). Las vencidas se avisan todos los días.")
    shrimp_lopdp_retention_months = fields.Integer(
        string="Retención de altas rechazadas y registros de acceso (meses)", default=12,
        help="Política de privacidad §5: las solicitudes de registro rechazadas y los registros "
             "técnicos de acceso se conservan 12 meses.")
    shrimp_lopdp_fiscal_years = fields.Integer(
        string="Retención tributaria (años)", default=7,
        help="Al anonimizar una cuenta se conservan nombre fiscal y RUC/cédula, compras, cobros, "
             "facturas y certificados durante estos años.")
    shrimp_lopdp_purge_dry_run = fields.Boolean(
        string="Purga en simulación", default=True,
        help="Marcado: el cron semanal de retención solo genera el informe de lo que haría "
             "(CamaronMarket › Documentos legales › Purgas de retención). Desmárcalo para que "
             "anonimice y borre de verdad.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_lopdp_deadline_days": ("shrimp_user_registry.lopdp_deadline_days", 15, 1, 90),
            "shrimp_lopdp_reminder_days": ("shrimp_user_registry.lopdp_reminder_days", 3, 0, 30),
            "shrimp_lopdp_retention_months": ("shrimp_user_registry.lopdp_retention_months", 12, 1, 120),
            "shrimp_lopdp_fiscal_years": ("shrimp_user_registry.lopdp_fiscal_retention_years", 7, 1, 30),
            "shrimp_lopdp_purge_dry_run": ("shrimp_user_registry.lopdp_purge_dry_run", True, None, None),
        })
        return specs
