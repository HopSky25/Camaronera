from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Factor exacto de conversión. Confundir kilos con libras es un error del 120 %,
# y las listas del sector cotizan el entero en $/Kg y la cola en $/Lb, así que
# la conversión ocurre en cada cruce.
LB_POR_KG = 2.2046226218


class ShrimpProduct(models.Model):
    _name = "shrimp.product"
    _inherit = "shrimp.product"

    # ------------------------------------------------------------------
    # Prerrequisito del cruce con las listas de precios
    # ------------------------------------------------------------------
    # Todo cruce se hace por talla. Sin talla, presentación y unidad de peso el
    # lote no se puede casar con ningún renglón de una lista, y hasta ahora los
    # tres campos eran opcionales: de 225 productos publicados, ninguno los
    # tenía. Se exigen solo en camarón adulto, que es donde aplican.
    #
    # La constrains se declara sobre estos campos a propósito: así solo se
    # comprueba al crear el lote o al editarlos, y los lotes viejos siguen
    # operando —se compran, se verifican y se cierran— sin tropezar.
    @api.constrains("verification_scope", "presentation", "size_grade_id", "uom_id")
    def _check_datos_para_cruce(self):
        for rec in self:
            if rec.verification_scope != "adult":
                continue
            if not rec.presentation:
                raise ValidationError(_(
                    "Indica si el lote es entero o cola. Sin eso no se puede "
                    "cruzar con las listas de precios de las empacadoras."))
            if not rec.size_grade_id:
                raise ValidationError(_(
                    "Indica la talla del lote. Es lo que determina a cuánto te "
                    "lo pagan: las listas cotizan por talla."))
            if not rec._clave_uom():
                raise ValidationError(_(
                    "La cantidad de un lote de camarón adulto se mide en libras, "
                    "no en «%s». Las listas cotizan el entero en $/Kg y la cola "
                    "en $/Lb, y sin saber la unidad el valor calculado sería "
                    "inventado."
                ) % (rec.uom_id.name or _("sin unidad")))

    # El marketplace no usa las unidades de Odoo sino su propio catálogo
    # shrimp.uom, que hoy tiene libras, millares y unidades. No hay kilo: la
    # finca pesa en libras, igual que los partes de planta. El kilo aparece
    # solo del lado de la empacadora, que cotiza el entero en $/Kg, así que la
    # conversión ocurre al cruzar y no en el producto.
    _CODIGOS_PESO = {"libra": "lb", "lb": "lb", "kilo": "kg", "kg": "kg",
                     "kilogramo": "kg"}

    def _clave_uom(self):
        """'kg', 'lb' o False, según el código de la unidad del lote."""
        self.ensure_one()
        codigo = (self.uom_id.code or "").strip().lower()
        return self._CODIGOS_PESO.get(codigo, False)

    def cantidad_en(self, unidad):
        """La cantidad disponible expresada en la unidad pedida ('kg' o 'lb')."""
        self.ensure_one()
        propia = self._clave_uom()
        qty = self.available_qty or 0.0
        if not propia or propia == unidad:
            return qty
        return qty / LB_POR_KG if unidad == "kg" else qty * LB_POR_KG

    # ------------------------------------------------------------------
    # Cruce con las listas de precios
    # ------------------------------------------------------------------
    def mejor_precio_hoy(self):
        """Qué es lo mejor que le pagan hoy por este lote.

        Cruza la talla y la presentación del lote contra las listas vigentes
        que recibe su dueño. Devuelve el mejor renglón, el segundo y el valor
        del lote completo, ya convertido a la unidad de la lista.

        Solo cruza la MISMA presentación: si el lote es entero y la empacadora
        solo cotiza cola, no se compara. Convertir entre las dos exige aplicar
        el rendimiento (~65 %), y hacerlo en silencio daría una cifra que el
        camaronero leería como firme cuando es una estimación.
        """
        self.ensure_one()
        if self.verification_scope != "adult" or not self.size_grade_id \
                or not self.presentation:
            return {}

        L = self.env["shrimp.price.list"].sudo()
        listas = L.visibles_para(self.seller_partner_id)
        if not listas:
            return {}

        candidatos = []
        for linea in listas.mapped("line_ids").filtered(
                lambda l: l.size_grade_id == self.size_grade_id
                and l.presentation == self.presentation):
            # En cola solo se toma la directa: la sobrante es lo que queda de
            # clasificar el entero, no un precio al que se pueda vender un lote.
            if self.presentation == "cola" and linea.channel != "directa":
                continue
            candidatos.append({
                "linea": linea,
                "lista": linea.price_list_id,
                "empacadora": linea.price_list_id.issuer_partner_id,
                "precio": linea.price,
                "uom": linea.uom,
                "calidad": linea.quality,
                "total": linea.price * self.cantidad_en(linea.uom),
            })
        if not candidatos:
            return {"sin_precio": True, "listas": len(listas)}

        candidatos.sort(key=lambda c: -c["precio"])
        mejor = candidatos[0]
        # El segundo solo cuenta si es de otra empacadora: comparar la calidad
        # A contra la B de la misma no dice nada sobre a quién despachar.
        segundo = next((c for c in candidatos
                        if c["empacadora"] != mejor["empacadora"]), None)
        return {
            "mejor": mejor,
            "segundo": segundo,
            "ventaja": (mejor["precio"] - segundo["precio"]) if segundo else 0.0,
            "diferencia_total": (mejor["total"] - segundo["total"]) if segundo else 0.0,
            "empacadoras": len({c["empacadora"].id for c in candidatos}),
        }
