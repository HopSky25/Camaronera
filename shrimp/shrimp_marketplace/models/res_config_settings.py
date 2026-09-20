from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # La comisión por unidad vendida se configura ahora por cada Unidad de medida
    # (campo commission_cents en shrimp.uom), visible solo para el administrador.

    shrimp_check_fee = fields.Float(
        string="Costo del chequeo",
        config_parameter="shrimp_marketplace.check_fee",
        default=0.0,
        help="Valor fijo que se cobra al comprador por enviar un equipo a verificar "
        "el producto cuando solicita un chequeo. Se cobra además del valor del producto.",
    )
