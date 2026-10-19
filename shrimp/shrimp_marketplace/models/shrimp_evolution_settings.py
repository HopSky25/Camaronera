"""Ajustes › CamaronMarket › «Estado real del lote» (observaciones)."""
from odoo import api, fields, models

from .shrimp_product_evolution import (DEFAULT_EDIT_HOURS, DEFAULT_SIZE_THRESHOLD,
                                       PARAM_EDIT_HOURS, PARAM_SIZE_THRESHOLD)


class ResConfigSettingsEvolution(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_evo_size_threshold = fields.Integer(
        string="Umbral de talla para avisar (%)",
        default=DEFAULT_SIZE_THRESHOLD,
        help="Si la talla observada se aparta de la publicada más de este porcentaje, se avisa "
             "al vendedor (y a los compradores en curso) como cuando difiere la etapa. "
             "0 = solo se compara la etapa.")
    shrimp_evo_edit_hours = fields.Integer(
        string="Horas para corregir una observación",
        default=DEFAULT_EDIT_HOURS,
        help="Durante estas horas desde que la registra, el vendedor puede corregir o borrar "
             "una observación. Después queda fija como evidencia para los compradores.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_evo_size_threshold": (PARAM_SIZE_THRESHOLD, DEFAULT_SIZE_THRESHOLD, 0, 1000),
            "shrimp_evo_edit_hours": (PARAM_EDIT_HOURS, DEFAULT_EDIT_HOURS, 0, 24 * 30),
        })
        return specs
