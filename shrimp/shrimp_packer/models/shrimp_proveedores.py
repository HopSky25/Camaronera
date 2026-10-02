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
    # Lo primero que ve la empacadora al entrar: su mejor proveedor, por talla
    # ==================================================================
    # Orden comercial de las tallas. El código es texto libre ("U15", "21/25"),
    # así que ordenarlo alfabéticamente pone "U15" al final y "16/20" antes de
    # "21/25" solo por casualidad. Lo que no esté aquí va al final, en el orden
    # en que apareció: mejor una talla rara al fondo que una excepción.
    # Conviven dos nomenclaturas: la de cola (U15, 16/20, 21/25...) y la de
    # entero (20/30, 30/40, 40/50...), que es la que lleva un lote publicado
    # como entero. Van intercaladas por tamaño aproximado del animal: de más
    # grande a más chico, que es como se lee una lista de precios.
    ORDEN_TALLAS = ("U10", "U12", "U15", "16/20", "20/30", "21/25", "26/30",
                    "30/32", "30/40", "31/35", "36/40", "40/50", "41/50",
                    "50/60", "51/60", "60/70", "61/70", "70/80", "71/90",
                    "80/100", "91/110", "100/120")

    def _orden_talla(self, codigo):
        c = (codigo or "").strip().upper()
        return (self.ORDEN_TALLAS.index(c) if c in self.ORDEN_TALLAS
                else len(self.ORDEN_TALLAS))

    def por_talla(self, empacadora, proveedor):
        """Cuánto salió de cada talla, y de qué clase, en lo que ese proveedor
        le entregó a esta empacadora.

        Sale de shrimp.verification.line, que es donde el verificador anota
        libras por talla y clase. Se agrupa por talla y se suma por clase: el
        porcentaje de clase A de cada talla va sobre lo clasificado EN ESA
        talla, no sobre el lote, porque la pregunta del comprador es "la 31/35
        de este proveedor, ¿me sale A?", no "¿cuánto A trae en total?".

        El reparto ("share") va sobre el total clasificado del proveedor: dice
        qué tallas trae de verdad, que es lo que decide si conviene para el
        pedido que la planta tiene entre manos.
        """
        V = self.env["shrimp.verification"].sudo()
        verifs = V.search([
            ("buyer_partner_id", "=", empacadora.id),
            ("seller_partner_id", "=", proveedor.id),
            ("scope", "=", "adult"),
            ("state", "in", list(self.ESTADOS_VEREDICTO)),
        ])
        acum = {}
        for v in verifs:
            vistas = set()
            for l in v.line_ids:
                if not l.weight_lb or not l.size_code:
                    continue
                t = acum.setdefault(l.size_code.strip().upper(), {
                    "talla": l.size_code.strip().upper(),
                    "lb": 0.0, "a": 0.0, "b": 0.0, "c": 0.0, "lotes": 0,
                })
                t["lb"] += l.weight_lb
                t[l.quality_class or "c"] += l.weight_lb
                if l.size_code not in vistas:
                    t["lotes"] += 1
                    vistas.add(l.size_code)

        total = sum(t["lb"] for t in acum.values()) or 0.0
        filas = []
        for t in acum.values():
            t["clase_a"] = (100.0 * t["a"] / t["lb"]) if t["lb"] else None
            t["share"] = (100.0 * t["lb"] / total) if total else 0.0
            filas.append(t)
        filas.sort(key=lambda t: self._orden_talla(t["talla"]))

        # La "mejor talla" exige peso real detrás: una talla con 30 lb al 100 %
        # de clase A no es la fortaleza del proveedor, es un residuo.
        con_peso = [t for t in filas if total and t["share"] >= 10.0 and t["clase_a"] is not None]
        mejor = max(con_peso, key=lambda t: (t["clase_a"], t["lb"])) if con_peso else None
        return {"filas": filas, "total_lb": total, "mejor": mejor, "lotes": len(verifs)}

    def dashboard_empacadora(self, empacadora):
        """Lo que la empacadora necesita ver de entrada, sin buscarlo.

        Devuelve None cuando no hay nada verificado: la portada no debe
        enseñar un tablero vacío con ceros, que se lee como que el sistema no
        funciona. Sin datos, sencillamente no aparece.

        El líder es el mismo que corona /marketplace/proveedores —se reutiliza
        ranking() tal cual— para que la portada y el ranking nunca digan dos
        nombres distintos.
        """
        r = self.ranking(empacadora, orden="puntaje")
        if not r["filas"]:
            return None
        lider = r["filas"][0] if r["filas"][0]["suficiente"] else None
        if not lider:
            return None
        return {
            "r": r,
            "lider": lider,
            "tallas": self.por_talla(empacadora, lider["proveedor"]),
            # Los que vienen detrás, para que el titular tenga contexto: un
            # 72 % solo impresiona al lado de un 61 %.
            "resto": [f for f in r["filas"][1:4] if f["rendimiento"] is not None],
        }
