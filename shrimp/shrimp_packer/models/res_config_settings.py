from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .shrimp_price_list import AVISO_CADUCIDAD_DIAS_DEF, CADUCIDAD_DIAS_DEF
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

    # Reservas cumplidas: días que el lote queda reservado en exclusiva a la
    # empacadora comprometida para que lo compre (hueco #8 de la auditoría).
    shrimp_reserva_purchase_days = fields.Integer(
        string="Días para comprar una reserva cumplida", default=3,
        help="Cuando la cosecha cumple el compromiso, el lote queda reservado en exclusiva "
             "para la empacadora comprometida durante estos días (otras empacadoras no lo ven "
             "ni reciben aviso). Si no lo compra a tiempo, la reserva vence, cuenta como "
             "incumplimiento suyo y el lote se ofrece a las demás.")

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

    # D-48: caducidad de las listas «hasta segunda orden».
    shrimp_price_list_expiry_days = fields.Integer(
        string="Caducidad de las listas «hasta segunda orden»",
        default=CADUCIDAD_DIAS_DEF,
        help="Una lista publicada sin fecha de fin deja de regir a los N días de "
             "publicada (o de su última renovación), salvo que la empacadora la renueve.")
    shrimp_price_list_expiry_notice_days = fields.Integer(
        string="Aviso antes de la caducidad", default=AVISO_CADUCIDAD_DIAS_DEF,
        help="Cuántos días antes de caducar se avisa a la empacadora por correo.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_signoff_undo_minutes": ("shrimp.signoff_undo_minutes", 15, 0, 24 * 60),
            "shrimp_reserva_purchase_days": ("shrimp_packer.reserva_purchase_days", 3, 1, 60),
            "shrimp_price_list_expiry_days": (
                "shrimp_packer.price_list_expiry_days", CADUCIDAD_DIAS_DEF, 1, 365),
            "shrimp_price_list_expiry_notice_days": (
                "shrimp_packer.price_list_expiry_notice_days", AVISO_CADUCIDAD_DIAS_DEF, 0, 60),
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
