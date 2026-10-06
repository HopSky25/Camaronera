from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .shrimp_proveedores import (PESO_CLASE_A_DEF, PESO_CUMPLIMIENTO_DEF,
                                 PESO_RENDIMIENTO_DEF, UMBRAL_LOTES)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Reservas de cosecha: cuánto tiene quien rechazó una confirmación de
    # cosecha para deshacerlo (shrimp.harvest.confirmation; el rechazo libera
    # el compromiso de la empacadora).
    shrimp_signoff_undo_minutes = fields.Integer(
        string="Ventana para deshacer un rechazo (minutos)", default=15,
        help="Tras rechazar la confirmación de una cosecha, quien rechazó puede "
             "deshacerlo durante estos minutos (si el lote sigue en borrador). "
             "0 = no se puede deshacer.")

    # Ranking de proveedores (/marketplace/suppliers y «Mi historial»).
    shrimp_ranking_min_lots = fields.Integer(
        string="Lotes mínimos para el ranking", default=UMBRAL_LOTES,
        help="Con menos lotes verificados un proveedor no entra en el ranking: "
             "se muestra lote a lote, porque con 1 o 2 lotes no hay promedio fiable.")
    shrimp_ranking_weight_yield = fields.Integer(
        string="Peso del rendimiento (%)", default=int(PESO_RENDIMIENTO_DEF * 100))
    shrimp_ranking_weight_class_a = fields.Integer(
        string="Peso de la clase A (%)", default=int(PESO_CLASE_A_DEF * 100))
    shrimp_ranking_weight_compliance = fields.Integer(
        string="Peso del cumplimiento (%)", default=int(PESO_CUMPLIMIENTO_DEF * 100))

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_signoff_undo_minutes": ("shrimp.signoff_undo_minutes", 15, 0, 24 * 60),
            "shrimp_ranking_min_lots": ("shrimp_packer.ranking_min_lots", UMBRAL_LOTES, 1, 100),
            "shrimp_ranking_weight_yield": (
                "shrimp_packer.ranking_weight_yield", int(PESO_RENDIMIENTO_DEF * 100), 0, 100),
            "shrimp_ranking_weight_class_a": (
                "shrimp_packer.ranking_weight_class_a", int(PESO_CLASE_A_DEF * 100), 0, 100),
            "shrimp_ranking_weight_compliance": (
                "shrimp_packer.ranking_weight_compliance", int(PESO_CUMPLIMIENTO_DEF * 100), 0, 100),
        })
        return specs

    def _shrimp_check_params(self):
        super()._shrimp_check_params()
        total = (self.shrimp_ranking_weight_yield + self.shrimp_ranking_weight_class_a
                 + self.shrimp_ranking_weight_compliance)
        if total != 100:
            raise ValidationError(_(
                "Los pesos del ranking de proveedores tienen que sumar 100 %% (ahora suman %s %%).")
                % total)
