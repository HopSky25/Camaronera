from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Factor exacto de conversión. Confundir kilos con libras es un error del 120 %,
# y las listas del sector cotizan el entero en $/Kg y la cola en $/Lb, así que
# la conversión ocurre en cada cruce.
LB_POR_KG = 2.2046226218


from odoo.addons.shrimp_marketplace.models.shrimp_product import (
    ShrimpProduct as ShrimpProductBase)

from .res_partner import TASA_DESCUENTO_DEFECTO


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

        Ordenar solo por precio es engañoso y por eso cada candidato lleva
        además dos cosas que la lista ya traía y nadie usaba:

        - CUÁNDO SE COBRA. dias_cobro, precio_efectivo y total_efectivo traen
          el precio a valor de hoy con la forma de pago de esa lista y con el
          costo del dinero de la propia camaronera. 3,20/Kg con el 60 % de
          anticipo a 2 días puede convenirle más que 3,28 a 21 días si el
          dinero le hace falta para la siembra siguiente: nominalmente pierde
          8 centavos, en caja llega muy distinto. De ahí salen mejor_efectivo
          y cambia_ganador, que es el aviso que justifica toda la función.

        - SI RECIBE ESE DÍA. La mejor tarifa no sirve de nada si esa planta no
          recibe el día que ella cosecha: recibe_en_fecha cruza la ventana de
          despacho de la lista con expected_delivery_date del lote.

        Todo esto se AÑADE. Las claves de siempre —mejor, segundo, ventaja,
        empate...— conservan su orden y su significado: hay pantallas que
        dependen de ellas.
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

        # El costo del dinero de ESTA camaronera. No se inventa una tasa de
        # mercado: descontar un cobro a 21 días solo significa algo contra lo
        # que a ella le cuesta esperar. Si no la ha fijado se usa un supuesto
        # y se devuelve marcado para que la pantalla lo diga.
        dueno = self.seller_partner_id
        tasa, tasa_supuesta = (dueno.sudo().tasa_descuento() if dueno
                               else (TASA_DESCUENTO_DEFECTO, True))
        # La fecha en que ella espera cosechar. Puede no estar: entonces no se
        # supone "hoy" ni "cualquier día", se dice que falta.
        fecha = fields.Date.to_date(self.expected_delivery_date) or False

        candidatos = []
        for linea in listas.mapped("line_ids").filtered(
                lambda l: l.size_grade_id == self.size_grade_id
                and l.presentation == self.presentation):
            # En cola solo se toma la directa: la sobrante es lo que queda de
            # clasificar el entero, no un precio al que se pueda vender un lote.
            if self.presentation == "cola" and linea.channel != "directa":
                continue
            lista = linea.price_list_id
            dias_cobro, pago_declarado = lista.dias_de_cobro()
            factor = lista.factor_valor_presente(tasa)
            total = linea.price * self.cantidad_en(linea.uom)
            candidatos.append({
                "linea": linea,
                "lista": lista,
                "empacadora": lista.issuer_partner_id,
                "precio": linea.price,
                "uom": linea.uom,
                "calidad": linea.quality,
                "calidad_txt": linea.etiqueta_columna(),
                "total": total,
                # --- Lo que el precio no dice: cuándo se cobra ---
                "pago_declarado": pago_declarado,
                "dias_cobro": dias_cobro,
                "pago_txt": lista.texto_pago(),
                "precio_efectivo": linea.price * factor,
                "total_efectivo": total * factor,
                # Lo que cuesta esperar, por unidad. Es la cifra que explica
                # el porqué cuando el ganador cambia.
                "costo_espera": linea.price * (1.0 - factor),
                # --- Lo que el precio tampoco dice: si recibe ese día ---
                "recibe_en_fecha": lista.recibe_el(fecha),
                "ventana_txt": lista.texto_ventana(),
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

        # ---------------------------------------------------------------
        # Quién paga mejor EN CAJA
        # ---------------------------------------------------------------
        # El mismo cuidado que con la resta de precios: dos precios efectivos
        # en unidades distintas tampoco se comparan. Se ordena solo entre los
        # que cotizan en la misma unidad que el ganador nominal, que es contra
        # quien se le va a comparar en pantalla.
        misma_uom = [c for c in candidatos if c["uom"] == mejor["uom"]]
        # candidatos ya viene ordenado por precio nominal, así que ante dos
        # efectivos iguales max() se queda con el de mayor precio de papel:
        # si en caja da lo mismo, mejor la cifra que ella puede defender.
        mejor_efectivo = max(misma_uom, key=lambda c: c["precio_efectivo"])
        cambia_ganador = mejor_efectivo["empacadora"] != mejor["empacadora"]
        # El aviso solo se da si las DOS listas declaran su forma de pago. Una
        # lista con el pago en blanco se descuenta con factor 1, o sea como si
        # pagara de contado, y anunciar por eso que "gana en caja" sería
        # premiar al que no llenó el campo.
        pagos_declarados = (mejor["pago_declarado"]
                            and mejor_efectivo["pago_declarado"])

        # ---------------------------------------------------------------
        # Quién puede recibir el día que ella cosecha
        # ---------------------------------------------------------------
        en_fecha = [c for c in candidatos if c["recibe_en_fecha"]] if fecha else []
        mejor_en_fecha = en_fecha[0] if en_fecha else None
        mejor_efectivo_en_fecha = None
        if mejor_en_fecha:
            mejor_efectivo_en_fecha = max(
                (c for c in en_fecha if c["uom"] == mejor_en_fecha["uom"]),
                key=lambda c: c["precio_efectivo"])

        return {
            "mejor": mejor,
            "segundo": segundo if comparables else None,
            "ventaja": (mejor["precio"] - segundo["precio"]) if comparables else 0.0,
            "diferencia_total": (mejor["total"] - segundo["total"]) if comparables else 0.0,
            # Empate: hay otra empacadora que paga lo mismo. No es lo mismo que
            # "eres el único que cotiza", y la tarjeta no lo distinguía.
            "empate": comparables and mejor["precio"] == segundo["precio"],
            "empacadoras": len({c["empacadora"].id for c in candidatos}),

            # ---- Precio efectivo (todo lo de abajo es añadido, no sustituye
            # nada de lo de arriba: hay pantallas que dependen de esas claves)
            "tasa": tasa,
            # La pantalla TIENE que poder decir "esto lo supusimos nosotros".
            # Un número que parece exacto y descansa en una suposición oculta
            # es peor que no dar el número.
            "tasa_supuesta": tasa_supuesta,
            "mejor_efectivo": mejor_efectivo,
            # El aviso que justifica toda esta función: el que más paga no es
            # el que mejor paga en caja.
            "cambia_ganador": cambia_ganador and pagos_declarados,
            # Cambia el ganador pero alguna de las dos listas no dice cuándo
            # paga: hay algo que mirar, pero no se puede afirmar.
            "efectivo_incierto": cambia_ganador and not pagos_declarados,
            "ventaja_efectiva": (mejor_efectivo["precio_efectivo"]
                                 - mejor["precio_efectivo"]),
            "diferencia_total_efectiva": (mejor_efectivo["total_efectivo"]
                                          - mejor["total_efectivo"]),

            # ---- Ventana de despacho
            "fecha_prevista": fecha,
            # El texto se arma aquí y no en la plantilla: QWeb es un mal sitio
            # para el formato de fechas.
            "fecha_prevista_txt": fecha.strftime("%d/%m/%Y") if fecha else "",
            # Sin fecha prevista no se dice nada sobre quién puede recibir: se
            # dice que falta la fecha. Suponerla es inventarle una respuesta.
            "sin_fecha_prevista": not fecha,
            "mejor_en_fecha": mejor_en_fecha,
            "mejor_efectivo_en_fecha": mejor_efectivo_en_fecha,
            "candidatos_en_fecha": len(en_fecha),
            # La mejor tarifa no vale nada si esa planta no recibe ese día.
            "alerta_fecha": bool(fecha) and not mejor["recibe_en_fecha"],
            "ninguna_en_fecha": bool(fecha) and not en_fecha,
        }
