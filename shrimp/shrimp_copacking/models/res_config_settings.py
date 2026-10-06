from odoo import api, fields, models

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
        })
        return specs
