from odoo import api, fields, models

from . import copack_plazos as P
from .shrimp_copack_order import DEFAULT_TOLERANCE_PCT, PLATFORM_RATE_PER_LB


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_copack_platform_rate = fields.Float(
        string="Cuota de la plataforma por libra empacada", default=PLATFORM_RATE_PER_LB,
        # Dos decimales: la orden la guarda como importe en la moneda (centavos).
        digits=(16, 2),
        help="Lo que CamaronMarket factura al maquilador por cada libra empacada "
             "cuando las dos partes firman el acta. Se fija en cada orden al "
             "crearla: cambiarla no altera las órdenes existentes. El empaque "
             "propio no paga cuota.")
    shrimp_copack_default_tolerance = fields.Float(
        string="Tolerancia por defecto del acta (%)", default=DEFAULT_TOLERANCE_PCT,
        digits=(5, 2),
        help="Merma de manipulación (recibidas menos empacadas) que se da por "
             "normal en las órdenes nuevas. Cada orden la puede ajustar.")
    shrimp_copack_require_license = fields.Boolean(
        string="Exigir habilitación vigente de la planta para empacar", default=False,
        help="Activado, una planta (maquilador o empaque propio) sin habilitación "
             "vigente —vencida o sin fecha— no puede registrar la recepción ni el "
             "empaque, y ve un aviso claro con el motivo. Apagado (por defecto) se "
             "comporta como siempre.")

    # Plazos (auditoría de procesos D-14 y D-47).
    shrimp_copack_acceptance_hours = fields.Integer(
        string="Horas para firmar el acta", default=P.ACCEPTANCE_HOURS,
        help="Desde que la planta registra el empaque. Si al vencer una parte no firmó ni "
             "dio su disconformidad, el acta se da por aceptada (el silencio vale "
             "conformidad) y el empaque pasa a facturable.")
    shrimp_copack_reminder_1 = fields.Integer(
        string="Primer recordatorio (horas antes)", default=P.REMINDER_1,
        help="Horas antes del vencimiento del acta en que se recuerda firmar. 0 = no.")
    shrimp_copack_reminder_2 = fields.Integer(
        string="Segundo recordatorio (horas antes)", default=P.REMINDER_2,
        help="Horas antes del vencimiento del acta del segundo recordatorio. 0 = no.")
    shrimp_copack_mediation_days = fields.Integer(
        string="Días para mediar una disputa", default=P.MEDIATION_DAYS,
        help="Plazo de la actividad que se asigna a la plataforma cuando un acta queda "
             "en disputa.")
    shrimp_copack_request_days = fields.Integer(
        string="Vigencia de una solicitud (días)", default=P.REQUEST_DAYS,
        help="Una solicitud publicada que nadie adjudica vence a los N días (o antes, al "
             "pasar su fecha «Necesita hasta»). Se avisa al cliente el día antes.")
    shrimp_copack_offer_days = fields.Integer(
        string="Vigencia de una oferta (días)", default=P.OFFER_DAYS,
        help="Una oferta sin respuesta vence a los N días (o antes, al pasar su fecha "
             "«Disponible hasta»). La planta la renueva editándola.")
    shrimp_copack_autoclose_hours = fields.Integer(
        string="Cierre automático tras la firma (horas)", default=P.AUTOCLOSE_HOURS,
        help="Una orden con el acta firmada se cierra sola pasadas estas horas (0 = al "
             "firmar). Al cerrarse, la solicitud queda «Cerrada».")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_copack_platform_rate": (
                "shrimp_copacking.platform_rate_per_lb", PLATFORM_RATE_PER_LB, 0, 100),
            "shrimp_copack_default_tolerance": (
                "shrimp_copacking.default_tolerance_pct", DEFAULT_TOLERANCE_PCT, 0, 100),
            "shrimp_copack_require_license": (
                "shrimp_copacking.require_valid_license", False, None, None),
            "shrimp_copack_acceptance_hours": (P.ACCEPTANCE_HOURS_PARAM, P.ACCEPTANCE_HOURS, 1, 24 * 60),
            "shrimp_copack_reminder_1": (P.REMINDER_1_PARAM, P.REMINDER_1, 0, 24 * 60),
            "shrimp_copack_reminder_2": (P.REMINDER_2_PARAM, P.REMINDER_2, 0, 24 * 60),
            "shrimp_copack_mediation_days": (P.MEDIATION_DAYS_PARAM, P.MEDIATION_DAYS, 1, 90),
            "shrimp_copack_request_days": (P.REQUEST_DAYS_PARAM, P.REQUEST_DAYS, 1, 365),
            "shrimp_copack_offer_days": (P.OFFER_DAYS_PARAM, P.OFFER_DAYS, 1, 365),
            "shrimp_copack_autoclose_hours": (P.AUTOCLOSE_HOURS_PARAM, P.AUTOCLOSE_HOURS, 0, 24 * 90),
        })
        return specs
