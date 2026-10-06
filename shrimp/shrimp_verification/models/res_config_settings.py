from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Valores de siempre: los usan los getters cuando el parámetro no existe.
FEE_BASE = 250.0
FEE_CENTS = 2.0
FEE_MIN = 300.0
FEE_MAX = 800.0
MARGIN_PCT = 15.0
ACCEPTANCE_HOURS = 48
WEIGHT_TOLERANCE_PCT = 2.0


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # El honorario no es fijo: el coste real de una verificación tiene una parte
    # que no depende del lote (desplazamiento, día de técnico) y otra que sí
    # (muestreo, pesaje, clasificación). Un importe único deja los lotes
    # pequeños inviables y los grandes infravalorados.
    #
    # Sin config_parameter a propósito: se guardan con _shrimp_param_specs()
    # (shrimp_marketplace) para que un 0 —sin mínimo, sin tope, sin margen—
    # se guarde como 0 y no vuelva al valor por defecto.
    shrimp_verification_fee_base = fields.Float(
        string="Honorario base", default=FEE_BASE,
        help="Parte fija: cubre el desplazamiento y el día de técnico, "
             "independientemente del tamaño del lote.")

    shrimp_verification_fee_cents = fields.Float(
        string="Centavos por unidad", default=FEE_CENTS,
        help="Parte variable, en centavos por cada unidad verificada (libra, "
             "millar...). Refleja el trabajo que sí crece con el lote.")

    shrimp_verification_fee_min = fields.Float(
        string="Honorario mínimo", default=FEE_MIN,
        help="Por debajo de este importe la salida a campo no es rentable. "
             "0 = sin mínimo.")

    shrimp_verification_margin_pct = fields.Float(
        string="Margen de la plataforma (%)", default=MARGIN_PCT,
        help="Porcentaje del honorario que retiene la plataforma por intermediar "
             "y garantizar que el verificador está acreditado. El resto se le "
             "liquida a la empresa verificadora. Se congela en cada verificación "
             "al emitir su primer documento.")

    shrimp_verification_acceptance_hours = fields.Integer(
        string="Plazo para aceptar el informe (horas)", default=ACCEPTANCE_HOURS,
        help="Tiempo que tienen comprador y vendedor para aceptar o rechazar el "
             "informe. Vencido el plazo sin respuesta, se da por aceptado.")

    shrimp_verification_weight_tolerance_pct = fields.Float(
        string="Tolerancia de peso en planta (%)", default=WEIGHT_TOLERANCE_PCT,
        help="Cuánto puede faltar del peso vendido sin contarlo como "
             "incumplimiento del vendedor. Entre la pesada en finca y la de "
             "planta siempre hay merma.")

    shrimp_verification_fee_max = fields.Float(
        string="Honorario máximo", default=FEE_MAX,
        help="Tope: a partir de cierto volumen el trabajo deja de crecer, así "
             "que el honorario tampoco debe hacerlo. 0 = sin tope.")

    shrimp_signoff_undo_margin_minutes = fields.Integer(
        string="Margen al deshacer (minutos)", default=60,
        help="Si una parte deshace su rechazo del informe cuando al plazo de "
             "aceptación le queda menos que esto, el plazo se corre hasta ahora "
             "+ este margen: así deshacer no equivale a aceptar sin querer.")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_verification_fee_base": ("shrimp_verification.fee_base", FEE_BASE, 0, None),
            "shrimp_verification_fee_cents": ("shrimp_verification.fee_cents", FEE_CENTS, 0, None),
            "shrimp_verification_fee_min": ("shrimp_verification.fee_min", FEE_MIN, 0, None),
            "shrimp_verification_fee_max": ("shrimp_verification.fee_max", FEE_MAX, 0, None),
            "shrimp_verification_margin_pct": ("shrimp_verification.margin_pct", MARGIN_PCT, 0, 100),
            "shrimp_verification_acceptance_hours": (
                "shrimp_verification.acceptance_hours", ACCEPTANCE_HOURS, 1, 24 * 60),
            "shrimp_verification_weight_tolerance_pct": (
                "shrimp_verification.weight_tolerance_pct", WEIGHT_TOLERANCE_PCT, 0, 100),
            "shrimp_signoff_undo_margin_minutes": (
                "shrimp.signoff_undo_margin_minutes", 60, 0, 24 * 60),
        })
        return specs

    def _shrimp_check_params(self):
        super()._shrimp_check_params()
        if (self.shrimp_verification_fee_max and self.shrimp_verification_fee_min
                and self.shrimp_verification_fee_max < self.shrimp_verification_fee_min):
            raise ValidationError(_(
                "El honorario máximo no puede ser menor que el mínimo (0 = sin tope)."))


class ShrimpVerificationFee(models.AbstractModel):
    """Cálculo del honorario de verificación, en un solo sitio."""

    _name = "shrimp.verification.fee"
    _description = "Tarifa de verificación"

    @api.model
    def _parametros(self):
        """(base, centavos por unidad, mínimo, máximo) de Ajustes ›
        CamaronMarket › Verificación, con los valores de siempre si faltan."""
        S = self.env["shrimp.settings"]
        return (S.get_float("shrimp_verification.fee_base", FEE_BASE, minimum=0),
                S.get_float("shrimp_verification.fee_cents", FEE_CENTS, minimum=0),
                S.get_float("shrimp_verification.fee_min", FEE_MIN, minimum=0),
                S.get_float("shrimp_verification.fee_max", FEE_MAX, minimum=0))

    @api.model
    def compute(self, qty):
        """Honorario para un lote de `qty` unidades: base + variable, acotado
        entre el mínimo y el máximo configurados."""
        base, centavos, minimo, maximo = self._parametros()

        importe = base + (max(0.0, qty or 0.0) * centavos / 100.0)
        if minimo:
            importe = max(importe, minimo)
        if maximo:
            importe = min(importe, maximo)
        return round(importe, 2)

    @api.model
    def desglose(self, qty):
        """Los mismos números, desagregados, para poder explicárselos al
        comprador en vez de mostrarle un total sin origen."""
        base, centavos, _minimo, _maximo = self._parametros()
        variable = round(max(0.0, qty or 0.0) * centavos / 100.0, 2)
        total = self.compute(qty)
        return {
            "base": base,
            "centavos": centavos,
            "variable": variable,
            "total": total,
            "topado": total < round(base + variable, 2),
            "minimo_aplicado": total > round(base + variable, 2),
        }
