from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # El honorario no es fijo: el coste real de una verificación tiene una parte
    # que no depende del lote (desplazamiento, día de técnico) y otra que sí
    # (muestreo, pesaje, clasificación). Un importe único deja los lotes
    # pequeños inviables y los grandes infravalorados.
    shrimp_verification_fee_base = fields.Float(
        string="Honorario base", config_parameter="shrimp_verification.fee_base",
        default=250.0,
        help="Parte fija: cubre el desplazamiento y el día de técnico, "
             "independientemente del tamaño del lote.")

    shrimp_verification_fee_cents = fields.Float(
        string="Centavos por unidad", config_parameter="shrimp_verification.fee_cents",
        default=2.0,
        help="Parte variable, en centavos por cada unidad verificada (libra, "
             "millar...). Refleja el trabajo que sí crece con el lote.")

    shrimp_verification_fee_min = fields.Float(
        string="Honorario mínimo", config_parameter="shrimp_verification.fee_min",
        default=300.0,
        help="Por debajo de este importe la salida a campo no es rentable.")

    shrimp_verification_margin_pct = fields.Float(
        string="Margen de la plataforma (%)",
        config_parameter="shrimp_verification.margin_pct",
        default=15.0,
        help="Porcentaje del honorario que retiene la plataforma por intermediar "
             "y garantizar que el verificador está acreditado. El resto se le "
             "liquida a la empresa verificadora.")

    shrimp_verification_acceptance_hours = fields.Integer(
        string="Plazo para aceptar el informe (horas)",
        config_parameter="shrimp_verification.acceptance_hours", default=48,
        help="Tiempo que tienen comprador y vendedor para aceptar o rechazar el "
             "informe. Vencido el plazo sin respuesta, se da por aceptado.")

    shrimp_verification_weight_tolerance_pct = fields.Float(
        string="Tolerancia de peso (%)",
        config_parameter="shrimp_verification.weight_tolerance_pct", default=2.0,
        help="Cuánto puede faltar del peso vendido sin contarlo como "
             "incumplimiento del vendedor. Entre la pesada en finca y la de "
             "planta siempre hay merma.")

    shrimp_verification_fee_max = fields.Float(
        string="Honorario máximo", config_parameter="shrimp_verification.fee_max",
        default=800.0,
        help="Tope: a partir de cierto volumen el trabajo deja de crecer, así "
             "que el honorario tampoco debe hacerlo.")


class ShrimpVerificationFee(models.AbstractModel):
    """Cálculo del honorario de verificación, en un solo sitio."""

    _name = "shrimp.verification.fee"
    _description = "Tarifa de verificación"

    @api.model
    def compute(self, qty):
        """Honorario para un lote de `qty` unidades: base + variable, acotado
        entre el mínimo y el máximo configurados."""
        param = self.env["ir.config_parameter"].sudo()

        def _num(clave, defecto):
            try:
                return float(param.get_param(clave) or defecto)
            except (TypeError, ValueError):
                return defecto

        base = _num("shrimp_verification.fee_base", 250.0)
        centavos = _num("shrimp_verification.fee_cents", 2.0)
        minimo = _num("shrimp_verification.fee_min", 300.0)
        maximo = _num("shrimp_verification.fee_max", 800.0)

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
        param = self.env["ir.config_parameter"].sudo()

        def _num(clave, defecto):
            try:
                return float(param.get_param(clave) or defecto)
            except (TypeError, ValueError):
                return defecto

        base = _num("shrimp_verification.fee_base", 250.0)
        centavos = _num("shrimp_verification.fee_cents", 2.0)
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
