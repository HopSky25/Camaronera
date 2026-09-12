from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Factor exacto de conversión. Confundir kilos con libras es un error del 120 %,
# y las listas del sector cotizan el entero en $/Kg y la cola en $/Lb, así que
# la conversión ocurre en cada cruce.
LB_POR_KG = 2.2046226218


from odoo.addons.shrimp_marketplace.models.shrimp_product import (
    ShrimpProduct as ShrimpProductBase)


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
    # Se exige en ENGORDE, no en todo lo que tenga alcance "adulto".
    # verification_scope vale "adult" para cualquier lote de una camaronera,
    # y eso incluye los juveniles de 2 a 5 g, que no son mercadería de
    # empacadora: se venden a otra finca para seguir engordando. Pedirles una
    # talla comercial obligaría a inventarla —un camarón de 3 g daría 230
    # piezas por libra, fuera de toda la matriz— y el comparador lo valoraría
    # como si fuera camarón de mesa.
    @api.constrains("verification_scope", "presentation", "size_grade_id",
                    "uom_id", "stage_id")
    def _check_datos_para_cruce(self):
        for rec in self:
            if rec.verification_scope != "adult":
                continue
            if (rec.stage_id.code or "").strip().upper() != "ENGORDE":
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
    # Precio tomado de una lista de precios (opcional)
    # ------------------------------------------------------------------
    # Si el lote se ata a una lista, su precio deja de escribirse a mano: se
    # toma del renglón que cruza (misma talla y presentación) y se mantiene
    # sincronizado. La lista cotiza el entero en $/Kg y la cola en $/Lb; el
    # lote se mide en libras, así que el entero se convierte a $/Lb.
    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista de precios", index=True,
        help="Si la asignas, el precio del lote se toma de esta lista según su "
             "talla y presentación, y queda fijo mientras esté asignada.")

    def _precio_desde_lista(self):
        """Precio unitario del lote (en libras) según la lista asignada, o None
        si no aplica o no hay renglón que cruce."""
        self.ensure_one()
        pl = self.price_list_id
        if not pl or not self.size_grade_id or not self.presentation:
            return None
        lineas = pl.line_ids.filtered(
            lambda l: l.size_grade_id == self.size_grade_id
            and l.presentation == self.presentation
            and (self.presentation != "cola" or l.channel == "directa"))
        if not lineas:
            return None
        linea = max(lineas, key=lambda l: l.price)
        propia = self._clave_uom() or "lb"
        if linea.uom == propia:
            return linea.price
        if propia == "lb" and linea.uom == "kg":
            return linea.price / LB_POR_KG
        if propia == "kg" and linea.uom == "lb":
            return linea.price * LB_POR_KG
        return linea.price

    def _sync_precio_lista(self):
        """Fija el precio del lote desde su lista, si tiene una asignada."""
        for rec in self:
            # Un lote ya vendido tiene el precio bloqueado por trazabilidad.
            if rec.price_list_id and not rec.has_purchases():
                precio = rec._precio_desde_lista()
                if precio is not None and abs((rec.price or 0.0) - precio) > 1e-6:
                    rec.with_context(_syncing_precio=True).write({"price": precio})

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._sync_precio_lista()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("_syncing_precio") and any(
                k in vals for k in ("price_list_id", "size_grade_id", "presentation", "uom_id")):
            self._sync_precio_lista()
        return res

    # ------------------------------------------------------------------
    # Quién puede comprar este lote
    # ------------------------------------------------------------------
    # La última pata de la cadena: el camarón adulto solo lo compra una
    # empacadora. El módulo base no la incluye porque el rol no existía allí.
    # Se amplía el diccionario y no se reimplementa el método: la cadena tiene
    # que estar descrita en un solo sitio o las dos copias se separan.
    _COMPRADOR_ESPERADO = dict(
        ShrimpProductBase._COMPRADOR_ESPERADO, camaronera="empacadora")

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
        if self.verification_scope != "adult":
            return {}
        # Solo el ENGORDE se cruza con una lista de precios. Un juvenil de 2 a
        # 5 g no es mercadería de empacadora: se vende a otra finca para seguir
        # engordando. Sin este corte, un lote llamado "Juvenil 3,6 g" aparecía
        # con "Mejor precio hoy $1,59/Lb · Directa A" y un total de $9.988,
        # que es una cifra que nadie le va a pagar.
        if (self.stage_id.code or "").strip().upper() != "ENGORDE":
            return {}
        # Si falta el dato con el que se cruza, hay que DECIRLO. Antes se
        # devolvía un diccionario vacío y la tarjeta no pintaba nada: la
        # camaronera no veía "Mejor precio hoy" ni un aviso, y no tenía forma
        # de saber que le faltaba rellenar la talla. Entre dos camaroneras eran
        # diez lotes callados, algunos llamados "Vannamei Entero 40/50" con el
        # campo de talla vacío.
        faltan = []
        if not self.presentation:
            faltan.append(_("si es entero o cola"))
        if not self.size_grade_id:
            faltan.append(_("la talla"))
        if faltan:
            return {"faltan": faltan}

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
                "calidad_txt": linea.etiqueta_columna(),
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
        # Restar dos precios en unidades distintas da una cifra sin sentido con
        # un factor de error de 2,2. Hoy no pasa —entero va en Kg y cola en Lb
        # en toda la base, y ahora hay una restricción que lo garantiza— pero
        # la resta no debe apoyarse en esa coincidencia.
        comparables = bool(segundo) and segundo["uom"] == mejor["uom"]
        return {
            "mejor": mejor,
            "segundo": segundo if comparables else None,
            "ventaja": (mejor["precio"] - segundo["precio"]) if comparables else 0.0,
            "diferencia_total": (mejor["total"] - segundo["total"]) if comparables else 0.0,
            # Empate: hay otra empacadora que paga lo mismo. No es lo mismo que
            # "eres el único que cotiza", y la tarjeta no lo distinguía.
            "empate": comparables and mejor["precio"] == segundo["precio"],
            "empacadoras": len({c["empacadora"].id for c in candidatos}),
        }
