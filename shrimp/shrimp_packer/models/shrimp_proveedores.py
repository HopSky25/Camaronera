from odoo import models

# ----------------------------------------------------------------------
# Cuántos lotes hacen falta para que un promedio signifique algo
# ----------------------------------------------------------------------
# Con 1 lote no hay promedio: hay un dato. Con 2, un solo lote flojo mueve la
# media entre 10 y 30 puntos de rendimiento y no queda forma de distinguir un
# mal día de un mal proveedor. Desde 3 la media empieza a ser más estable que
# cualquiera de sus términos y —lo que de verdad decide una compra recurrente—
# ya existe un rango mínimo-máximo que enseña la dispersión.
#
# El número no es caprichoso: es el mínimo con el que el control de calidad por
# subgrupos acepta hablar de tendencia. Por debajo de eso esta pantalla NO
# esconde el dato (esconderlo sería peor: el comprador lo tiene y lo quiere
# ver), pero lo saca del ranking y lo muestra lote a lote, que es la única
# lectura honesta de una muestra de uno.
UMBRAL_LOTES = 3


def _media(valores):
    """Media aritmética simple, o None si no hay nada que promediar."""
    return (sum(valores) / len(valores)) if valores else None


class ShrimpProveedorRanking(models.AbstractModel):
    """Qué rindió de verdad cada camaronera, según el parte de planta.

    La empacadora ya decide a quién le compra, pero hoy decide con el único
    número que tiene delante: el precio que ella misma publicó. El rendimiento
    real de cada proveedor —cuánto producto salió de cada cien libras que
    entraron, cuánto fue clase A, cuántas veces el lote no era lo anunciado—
    está guardado en cada verificación y no lo lee nadie.

    Abstracto a propósito: no hay nada que almacenar. Todo sale de
    shrimp.verification, que es la fuente y no debe duplicarse; un campo
    calculado en res.partner quedaría desincronizado en cuanto se corrigiera
    un parte, y además el mismo proveedor rinde distinto para cada comprador.
    """

    _name = "shrimp.proveedor.ranking"
    _description = "Ranking de proveedores por rendimiento verificado"

    # Solo entra lo que ya tiene veredicto: antes de eso el parte todavía se
    # puede corregir, y promediar borradores es fabricar una estadística.
    # 'rejected' entra a propósito: un lote rechazado es justamente el dato que
    # el comprador necesita para no repetir la compra.
    ESTADOS_VEREDICTO = ("approved", "approved_obs", "rejected")
    ESTADOS_EN_CURSO = ("received", "assigned", "in_field", "done")

    # Reparto del puntaje. Se publica en pantalla para que nadie tenga que
    # confiar en una caja negra:
    #   rendimiento 45 — es dinero directo: cada punto es producto que sale o
    #                    no sale de la misma libra pagada.
    #   clase A     25 — a igual rendimiento, la clase A se exporta y la C no.
    #   cumplimiento 30 — que el lote sea lo anunciado. Pesa casi tanto como el
    #                    rendimiento porque un incumplimiento no cuesta puntos:
    #                    cuesta reprocesar, renegociar o perder el embarque.
    PESO_RENDIMIENTO = 0.45
    PESO_CLASE_A = 0.25
    PESO_CUMPLIMIENTO = 0.30

    # ------------------------------------------------------------------
    def _sin_porcentaje(self, texto):
        """Deja el texto apto para viajar en el qcontext de una página web.

        El render de website pasa el contexto por formato de cadena: un '%'
        suelto revienta la página entera con "incomplete format". Es el mismo
        motivo por el que el parte de planta no viaja en el qcontext del
        detalle de verificación.
        """
        return (texto or "").replace("%", " por ciento")

    # ------------------------------------------------------------------
    def _detalle_lote(self, v):
        """Una verificación reducida a lo que mira un comprador."""
        cumple, motivos = v.cumple_lo_publicado()
        return {
            "ref": v.name,
            "fecha": v.verified_date or v.create_date,
            "estado": v.state,
            "rendimiento": v.yield_pct or None,
            "clase_a": v.yield_class_a_pct or None,
            "metabisulfito": v.metabisulfite_ppm or None,
            "metabisulfito_res": v.metabisulfite_result,
            "sabor": v.taste_result,
            # La etiqueta se resuelve aquí y no en la plantilla: QWeb no
            # garantiza los builtins de Python, y duplicar el diccionario de
            # la selección en el XML lo condena a desincronizarse.
            "sabor_txt": dict(
                v._fields["taste_result"].selection).get(v.taste_result) or "—",
            "cumple": cumple,
            "motivos": [self._sin_porcentaje(m) for m in motivos],
            "lb_planta": v.weight_plant_lb or 0.0,
            "lb_enviadas": v.weight_sent_lb or 0.0,
            "basura_lb": v.trash_lb or 0.0,
            "sobrepeso_lb": v.overweight_lb or 0.0,
            "clase_a_lb": v.class_a_lb or 0.0,
            "clase_b_lb": v.class_b_lb or 0.0,
            "clase_c_lb": v.class_c_lb or 0.0,
        }

    # ------------------------------------------------------------------
    def _fila(self, proveedor, lotes):
        """Los promedios de un proveedor, cada uno con su propia muestra.

        Cada métrica lleva su contador aparte y no el total de lotes: un lote
        puede traer pesada y no medición de metabisulfito. Decir "5 lotes" al
        lado de un promedio calculado sobre 2 es mentir con la verdad.
        """
        detalles = [self._detalle_lote(v) for v in lotes]

        rendimientos = [d["rendimiento"] for d in detalles if d["rendimiento"]]
        clases_a = [d["clase_a"] for d in detalles if d["clase_a"] is not None
                    and d["rendimiento"]]
        metas = [d["metabisulfito"] for d in detalles if d["metabisulfito"]]

        # Merma y basura sobre lo realmente pesado, no sobre lo facturado: son
        # libras que la empacadora paga y no procesa.
        mermas = [
            100.0 * (d["lb_enviadas"] - d["lb_planta"]) / d["lb_enviadas"]
            for d in detalles if d["lb_enviadas"] and d["lb_planta"]
        ]
        basuras = [
            100.0 * d["basura_lb"] / d["lb_planta"]
            for d in detalles if d["lb_planta"] and d["basura_lb"]
        ]

        incumplidos = [d for d in detalles if not d["cumple"]]
        cumplimiento = 100.0 * (len(detalles) - len(incumplidos)) / len(detalles)

        rend = _media(rendimientos)
        clase_a = _media(clases_a)

        # Sin rendimiento medido no hay puntaje: un proveedor no puede quedar
        # primero por no tener datos. El resto de la fila sigue visible.
        puntaje = None
        if rend is not None:
            puntaje = (
                self.PESO_RENDIMIENTO * rend
                + self.PESO_CLASE_A * (clase_a if clase_a is not None else 0.0)
                + self.PESO_CUMPLIMIENTO * cumplimiento
            )

        return {
            "proveedor": proveedor,
            "lotes": len(detalles),
            "suficiente": len(detalles) >= UMBRAL_LOTES,
            "rendimiento": rend,
            "rendimiento_n": len(rendimientos),
            "rendimiento_min": min(rendimientos) if rendimientos else None,
            "rendimiento_max": max(rendimientos) if rendimientos else None,
            "clase_a": clase_a,
            "clase_a_n": len(clases_a),
            "metabisulfito": _media(metas),
            "metabisulfito_n": len(metas),
            "metabisulfito_fail": len(
                [d for d in detalles if d["metabisulfito_res"] == "fail"]),
            "sabor_rechazos": len(
                [d for d in detalles if d["sabor"] == "rejected"]),
            "merma": _media(mermas),
            "merma_n": len(mermas),
            "basura": _media(basuras),
            "basura_n": len(basuras),
            "incumplidos": len(incumplidos),
            "cumplimiento": cumplimiento,
            "rechazados": len([d for d in detalles if d["estado"] == "rejected"]),
            "lb_verificadas": sum(d["lb_planta"] for d in detalles),
            "ultima": max((d["fecha"] for d in detalles if d["fecha"]), default=False),
            "puntaje": puntaje,
            "detalles": detalles,
            "ofertas": self._ofertas_vivas(proveedor),
        }

    # ------------------------------------------------------------------
    def _ofertas_vivas(self, proveedor):
        """Lotes que ese proveedor tiene publicados ahora mismo.

        Sin esto el ranking es un informe; con esto es una compra: el que sale
        primero se puede comprar en el mismo clic.
        """
        return self.env["shrimp.product"].sudo().search_count([
            ("seller_partner_id", "=", proveedor.id),
            ("active", "=", True),
            ("state", "=", "published"),
            ("available_qty", ">", 0),
        ])

    # ------------------------------------------------------------------
    ORDENES = {
        # clave -> (campo, descendente)
        "puntaje": ("puntaje", True),
        "rendimiento": ("rendimiento", True),
        "clase_a": ("clase_a", True),
        "cumplimiento": ("cumplimiento", True),
        "lotes": ("lotes", True),
        "metabisulfito": ("metabisulfito", False),
    }

    def ranking(self, empacadora, orden="puntaje"):
        """Todo lo que la empacadora sabe de sus proveedores, ya comparado."""
        V = self.env["shrimp.verification"].sudo()

        # scope adult: el rendimiento, la clasificación y el metabisulfito no
        # existen en una verificación de larva, y una empacadora no compra
        # larva. Mezclarlas metería ceros que hundirían promedios ajenos.
        base = [("buyer_partner_id", "=", empacadora.id), ("scope", "=", "adult")]
        verificaciones = V.search(
            base + [("state", "in", list(self.ESTADOS_VEREDICTO))],
            order="create_date desc")

        grupos = {}
        for v in verificaciones:
            if not v.seller_partner_id:
                continue
            grupos.setdefault(v.seller_partner_id, []).append(v)

        filas = [self._fila(prov, lotes) for prov, lotes in grupos.items()]

        campo, desc = self.ORDENES.get(orden) or self.ORDENES["puntaje"]

        # Dos niveles a propósito. Primero manda la muestra: un proveedor con
        # un solo lote no puede encabezar un ranking por mucho que ese lote
        # haya salido redondo. Dentro de cada nivel manda el criterio pedido.
        # El None va siempre al fondo, no arriba por ser falsy.
        def clave(f):
            valor = f.get(campo)
            return (
                1 if f["suficiente"] else 0,
                1 if valor is not None else 0,
                (valor if desc else -valor) if valor is not None else 0.0,
            )

        filas.sort(key=clave, reverse=True)

        con_muestra = [f for f in filas if f["suficiente"]]
        return {
            "filas": filas,
            "orden": orden if orden in self.ORDENES else "puntaje",
            "umbral": UMBRAL_LOTES,
            "proveedores": len(filas),
            "con_muestra": len(con_muestra),
            "lotes": len(verificaciones),
            "lb": sum(f["lb_verificadas"] for f in filas),
            "incumplidos": sum(f["incumplidos"] for f in filas),
            # Lo que todavía no puede entrar: sirve para que el comprador sepa
            # que el ranking se va a mover, y cuánto.
            "en_curso": V.search_count(
                base + [("state", "in", list(self.ESTADOS_EN_CURSO))]),
            "pesos": {
                "rendimiento": int(self.PESO_RENDIMIENTO * 100),
                "clase_a": int(self.PESO_CLASE_A * 100),
                "cumplimiento": int(self.PESO_CUMPLIMIENTO * 100),
            },
        }

    # ==================================================================
    # El mismo cálculo, leído desde el otro lado del mostrador
    # ==================================================================
    # Todo lo de arriba responde a "¿a quién le compro?". Lo de abajo responde
    # a "¿qué puedo demostrar que rindo?", que es la misma cuenta cambiando el
    # eje de agrupación: por vendedor en vez de por comprador.
    #
    # Se reutiliza _fila() tal cual —no una copia adaptada— precisamente
    # porque el valor de esta pantalla es que el número que la camaronera
    # enseña sea el MISMO que la empacadora tiene en su ranking. Dos cálculos
    # del mismo rendimiento que se desincronizan convierten un argumento de
    # venta en una discusión.

    def historial(self, camaronera):
        """La trayectoria verificada de una camaronera, en sus propias manos.

        Agrupa por vendedor y no por comprador: a la camaronera no le sirve
        saber cómo rindió para una planta concreta —eso ya lo sabe la planta—
        sino cómo rinde en general, que es lo que puede poner por delante al
        ofrecer un lote a una empacadora con la que nunca ha trabajado.

        Cuenta también cuántos compradores y cuántos verificadores distintos
        hay detrás: un 71 % medido por tres verificadores en dos plantas vale
        como argumento; el mismo 71 % medido siempre por el mismo verificador
        de la misma planta es un dato bueno pero más discutible, y quien lo
        va a leer tiene derecho a distinguirlos.
        """
        V = self.env["shrimp.verification"].sudo()

        # Mismo recorte que el ranking: scope adulto y solo con veredicto. Si
        # aquí entrara el borrador, la camaronera vería una media que después
        # cambia sola, y peor todavía: distinta de la que ve su comprador.
        base = [("seller_partner_id", "=", camaronera.id), ("scope", "=", "adult")]
        verificaciones = V.search(
            base + [("state", "in", list(self.ESTADOS_VEREDICTO))],
            order="create_date desc")

        # _fila divide entre el número de lotes para el cumplimiento: sin
        # lotes no hay fila que calcular, y la pantalla ya tiene que tratar
        # ese caso aparte de todos modos.
        fila = self._fila(camaronera, verificaciones) if verificaciones else None
        detalles = fila["detalles"] if fila else []

        # Metabisulfito y sabor se cuentan sobre lo MEDIDO, no sobre el total
        # de lotes. Un lote sin prueba de sabor no es un lote que la aprobó:
        # meterlo en el denominador inflaría el cumplimiento, y meterlo en el
        # numerador de los fallos lo hundiría. Queda fuera y se dice cuántos.
        sabor_medidos = [d for d in detalles if d["sabor"]]
        meta_medidos = [d for d in detalles
                        if d["metabisulfito_res"] in ("pass", "fail")]

        lotes = fila["lotes"] if fila else 0
        return {
            "fila": fila,
            "lotes": lotes,
            "umbral": UMBRAL_LOTES,
            "suficiente": bool(fila) and fila["suficiente"],
            "faltan": max(0, UMBRAL_LOTES - lotes),
            "compradores": len({
                v.buyer_partner_id.id for v in verificaciones if v.buyer_partner_id}),
            "verificadores": len({
                v.verifier_partner_id.id for v in verificaciones
                if v.verifier_partner_id}),
            "sabor_n": len(sabor_medidos),
            "sabor_ok": len([d for d in sabor_medidos if d["sabor"] != "rejected"]),
            "metabisulfito_n": len(meta_medidos),
            "metabisulfito_ok": len(
                [d for d in meta_medidos if d["metabisulfito_res"] == "pass"]),
            "en_curso": V.search_count(
                base + [("state", "in", list(self.ESTADOS_EN_CURSO))]),
            "pesos": {
                "rendimiento": int(self.PESO_RENDIMIENTO * 100),
                "clase_a": int(self.PESO_CLASE_A * 100),
                "cumplimiento": int(self.PESO_CUMPLIMIENTO * 100),
            },
        }

    # ------------------------------------------------------------------
    # Lo único del historial que puede leer un tercero.
    #
    # Lista blanca y no lista negra a propósito: el día que _fila() gane una
    # clave nueva, lo que pase por defecto tiene que ser que NO se publique.
    # Fuera quedan 'detalles' (el lote a lote, con referencias de verificación
    # y los motivos de cada incumplimiento) y cualquier rastro de quién
    # compró: el comprador de un lote es información comercial de las dos
    # partes, y esta pantalla la mira cualquiera que abra el anuncio.
    CAMPOS_PUBLICOS = (
        "lotes", "suficiente", "rendimiento", "rendimiento_n",
        "rendimiento_min", "rendimiento_max", "clase_a", "clase_a_n",
        "cumplimiento", "incumplidos", "metabisulfito", "metabisulfito_fail",
        "sabor_rechazos", "lb_verificadas", "ultima",
    )
    CAMPOS_PUBLICOS_HIST = (
        "umbral", "faltan", "compradores", "verificadores",
        "sabor_n", "sabor_ok", "metabisulfito_n", "metabisulfito_ok",
    )

    def resumen_publico(self, vendedor):
        """El aval del vendedor, para enseñarlo en el anuncio de su lote.

        Devuelve {} cuando no hay nada verificado —un vendedor de larva, o una
        camaronera que todavía no ha pasado por planta—, para que la ficha del
        lote pueda callarse en vez de mostrar un bloque vacío.

        Lo que NO se recorta es la muestra: 'lotes' y 'suficiente' viajan
        siempre, porque el único uso deshonesto de esta caja sería enseñar un
        promedio sin decir de cuántos lotes sale.
        """
        # Un lote sin vendedor no debe acabar consultando por
        # seller_partner_id = False: ese dominio no devuelve "nada", devuelve
        # las verificaciones huérfanas, que son de cualquiera.
        if not vendedor:
            return {}

        h = self.historial(vendedor)
        if not h["fila"]:
            return {}
        resumen = {clave: h["fila"][clave] for clave in self.CAMPOS_PUBLICOS}
        resumen.update({clave: h[clave] for clave in self.CAMPOS_PUBLICOS_HIST})
        return resumen
