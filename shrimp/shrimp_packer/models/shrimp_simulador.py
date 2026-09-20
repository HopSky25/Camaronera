"""Simulador de cosecha: ¿cosecho ahora o espero?

El sistema ya tenía los dos datos y no los cruzaba: cuánto crece el animal
—histórico de pesajes del propio lote— y cuánto paga cada empacadora por cada
talla. Este fichero los une para responder la pregunta que se hace un
camaronero cada semana.

Regla de oro de todo lo que hay aquí: lo MEDIDO y lo ESTIMADO no se mezclan
nunca en la misma cifra sin decirlo. El camaronero lee una proyección como una
promesa, y la única forma honesta de dársela es que en pantalla se distinga qué
sale de sus pesajes y qué sale de un supuesto del sector.

Va en un fichero propio y no dentro de shrimp_product.py para no chocar con el
desarrollo que se hace en paralelo sobre ese fichero; la clase extiende el
mismo modelo, que es lo que importa.
"""
import re
from datetime import timedelta

from odoo import fields, models, _

from .shrimp_product import LB_POR_KG

# Rendimiento de cola sobre el animal entero. Es el mismo ~65 % que ya usa el
# módulo de verificación y que documenta mejor_precio_hoy(): el caparazón y la
# cabeza son un tercio del peso. Se necesita aquí porque la talla de cola se
# cuenta en piezas por LIBRA DE COLA, no del animal entero, y el peso que mide
# la finca es el del animal entero.
RENDIMIENTO_COLA = 0.65

# Gramos por libra. Se deriva del factor ya definido en shrimp_product para que
# no haya dos constantes de conversión que puedan separarse.
G_POR_LB = 1000.0 / LB_POR_KG

# Referencia del sector para camarón blanco de engorde cuando el lote no tiene
# pesajes suficientes para medir su propio ritmo. NO es un dato del lote y la
# pantalla lo marca como estimación en todas sus filas.
REF_MG_DIA = 200.0          # 0,20 g/día
REF_MG_DIA_MIN = 150.0      # 0,15 g/día
REF_MG_DIA_MAX = 250.0      # 0,25 g/día

# Por encima de esto el ritmo medido deja de ser creíble: 0,35 g/día son 2,4 g
# por semana, y un engorde comercial no da eso de forma sostenida. No se
# descarta el dato —es la medición del cliente— pero la pantalla avisa. El
# umbral es deliberadamente más alto que REF_MG_DIA_MAX: 0,27 g/día es una
# buena corrida, no un error, y marcar en rojo todo lo que pase de 0,25
# convertiría el aviso en ruido que nadie lee.
MAX_MG_DIA_CREIBLE = 350.0

# Días mínimos entre el primer y el último pesaje para que la pendiente
# signifique algo. Dos pesos tomados con dos días de diferencia se diferencian
# menos que el error de la balanza, y la recta que sale de ahí se dispara.
MIN_DIAS_SERIE = 5

# Horizontes que se muestran. El 7 y el 15 son las dos decisiones reales
# (esta semana o la que viene); el 30 y el 45 sirven para ver si hay un salto
# de talla que valga la pena esperar.
HORIZONTES = (0, 7, 15, 30, 45)

# Hasta dónde se busca el próximo salto de talla. Más allá de tres meses la
# proyección ya no es una decisión de cosecha.
DIAS_BUSQUEDA_SALTO = 120


def _rango_talla(nombre):
    """('30/40') -> (30.0, 40.0). ('U/12') -> (0.0, 12.0). Otro -> None.

    Las tallas del sector son rangos de piezas: en entero, piezas por kilo; en
    cola, piezas por libra. Las "U" (under) son un tope sin suelo: U/12 es todo
    lo que dé menos de 12 piezas por libra, o sea lo más grande que hay.
    """
    texto = (nombre or "").strip().upper().replace(" ", "")
    m = re.match(r"^U/?(\d+)$", texto)
    if m:
        return (0.0, float(m.group(1)))
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", texto)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return (min(a, b), max(a, b))
    return None


def _pendiente(puntos):
    """Ganancia por día por mínimos cuadrados sobre (fecha, valor).

    Se ajusta una recta sobre TODOS los pesajes y no se restan simplemente el
    primero y el último: un pesaje malo —una muestra de 30 animales tomada en
    la orilla— arrastraría toda la proyección si cae justo en un extremo.
    """
    n = len(puntos)
    if n < 2:
        return None
    origen = puntos[0][0]
    xs = [(fecha - origen).days for fecha, _v in puntos]
    ys = [valor for _f, valor in puntos]
    if (max(xs) - min(xs)) < MIN_DIAS_SERIE:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if not den:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


class ShrimpProductSimulador(models.Model):
    _name = "shrimp.product"
    _inherit = "shrimp.product"

    # ------------------------------------------------------------------
    # Lo medido: el propio historial del lote
    # ------------------------------------------------------------------
    def _mediciones(self, campo):
        """[(fecha, valor)] del historial de evolución, una por día y en orden.

        Se colapsa por día a propósito: un mismo día hay varias filas de
        evolución porque cada venta deja una —con el mismo peso y distinta
        cantidad—, y contarlas como mediciones independientes le daría peso
        de más a los días en que hubo movimiento.
        """
        self.ensure_one()
        puntos = {}
        for ev in self.sudo().evolution_ids.sorted("date"):
            valor = ev[campo] or 0.0
            if valor <= 0:
                continue
            puntos[fields.Datetime.to_datetime(ev.date).date()] = valor
        return sorted(puntos.items())

    def ritmo_de_crecimiento(self):
        """Cuánto gana al día este lote, y de dónde sale ese número.

        Devuelve siempre 'medido': True solo si sale de los pesajes del propio
        lote. Cuando es False el número es la referencia del sector y la
        pantalla tiene que decirlo; presentar una estimación como medición es
        prometer una cosecha que nadie garantizó.
        """
        self.ensure_one()
        puntos = self._mediciones("avg_size_mg")
        mg_dia = _pendiente(puntos) if len(puntos) >= 2 else None

        def _txt(fecha):
            return fecha.strftime("%d/%m/%Y") if fecha else ""

        if mg_dia and mg_dia > 0:
            return {
                "mg_dia": mg_dia,
                "g_dia": mg_dia / 1000.0,
                "medido": True,
                "n": len(puntos),
                "desde": puntos[0][0],
                "hasta": puntos[-1][0],
                "desde_txt": _txt(puntos[0][0]),
                "hasta_txt": _txt(puntos[-1][0]),
                "dias": (puntos[-1][0] - puntos[0][0]).days,
                # El ritmo medido puede salirse de lo creíble: pasa cuando
                # los pesajes vienen de muestreos flojos. Se usa igual —es la
                # medición del lote, no la nuestra— pero se avisa.
                "fuera_de_rango": mg_dia > MAX_MG_DIA_CREIBLE,
            }
        motivo = _("este lote no tiene dos pesajes separados en el tiempo")
        if len(puntos) >= 2:
            if (puntos[-1][0] - puntos[0][0]).days < MIN_DIAS_SERIE:
                motivo = _("sus pesajes están demasiado juntos en el tiempo")
            else:
                motivo = _("sus pesajes no muestran crecimiento")
        return {
            "mg_dia": REF_MG_DIA,
            "g_dia": REF_MG_DIA / 1000.0,
            "medido": False,
            "motivo": motivo,
            "n": len(puntos),
            "desde": puntos[0][0] if puntos else False,
            "hasta": puntos[-1][0] if puntos else False,
            "desde_txt": _txt(puntos[0][0] if puntos else False),
            "hasta_txt": _txt(puntos[-1][0] if puntos else False),
            "dias": (puntos[-1][0] - puntos[0][0]).days if len(puntos) >= 2 else 0,
            "fuera_de_rango": False,
        }

    def _ritmo_supervivencia(self):
        """Puntos de supervivencia que pierde al día, medidos sobre su historial.

        Solo se usa en el modo que proyecta biomasa. Si no hay serie, se
        devuelve 0 y se marca no medido: inventar una mortalidad sería
        exactamente el tipo de cifra que este simulador no debe producir.
        """
        self.ensure_one()
        puntos = self._mediciones("survival_rate")
        pendiente = _pendiente(puntos) if len(puntos) >= 2 else None
        if pendiente is None:
            return {"pts_dia": 0.0, "medido": False,
                    "pct_hoy": self.survival_rate or 0.0}
        return {
            # Solo interesa la pérdida. Una serie que "sube" de supervivencia
            # es un error de registro, no un lote que resucita animales.
            "pts_dia": min(pendiente, 0.0),
            "medido": True,
            "pct_hoy": puntos[-1][1],
        }

    # ------------------------------------------------------------------
    # Peso -> piezas -> talla
    # ------------------------------------------------------------------
    def _piezas_por_unidad(self, peso_mg):
        """Piezas por kilo (entero) o piezas por libra de cola (cola).

        Es la conversión que define la talla. En cola se aplica el rendimiento
        porque lo que se cuenta son colas, no animales enteros.
        """
        self.ensure_one()
        gramos = (peso_mg or 0.0) / 1000.0
        if gramos <= 0:
            return 0.0
        if self.presentation == "cola":
            return G_POR_LB / (gramos * RENDIMIENTO_COLA)
        return 1000.0 / gramos

    def _escalera_tallas(self):
        """Las tallas de su presentación, ordenadas de animal pequeño a grande.

        Menos piezas = animal más grande, así que la escalera va de más piezas
        a menos. Se lee del catálogo y no de una lista escrita aquí: si mañana
        una empacadora añade una talla, el simulador la sube solo.
        """
        self.ensure_one()
        grados = self.env["shrimp.size.grade"].sudo().search(
            [("presentation", "=", self.presentation)])
        escalera = []
        for g in grados:
            rango = _rango_talla(g.name)
            if rango:
                escalera.append((g, rango))
        escalera.sort(key=lambda t: (-t[1][1], -t[1][0]))
        return escalera

    @staticmethod
    def _indice_por_piezas(escalera, piezas):
        """A qué peldaño de la escalera corresponde ese número de piezas.

        Primero por contención —la talla cuyo rango contiene el conteo—, y
        entre varias la más estrecha, que es la más específica (un 31 por kilo
        es un 30/32 antes que un 30/40). Si el conteo cae en un hueco entre dos
        rangos, se toma la frontera más cercana en vez de no devolver nada: el
        animal existe aunque la matriz del sector tenga saltos.
        """
        if not escalera:
            return None
        contienen = [(hi - lo, i) for i, (_g, (lo, hi)) in enumerate(escalera)
                     if lo <= piezas <= hi]
        if contienen:
            return min(contienen)[1]
        return min(range(len(escalera)),
                   key=lambda i: min(abs(piezas - escalera[i][1][0]),
                                     abs(piezas - escalera[i][1][1])))

    # ------------------------------------------------------------------
    # Precio de una talla hipotética
    # ------------------------------------------------------------------
    def _precio_para(self, talla, cantidad):
        """Qué le pagarían por este mismo lote si tuviera esa talla y cantidad.

        No se reimplementa el cruce con las listas: se construye un registro EN
        MEMORIA (self.new) con la talla y la cantidad proyectadas y se llama al
        mismo mejor_precio_hoy() que usa la tarjeta del lote. Dos copias de esa
        lógica se separarían en la primera corrección —el canal de la cola, el
        desempate entre empacadoras, la conversión Kg/Lb— y el simulador
        empezaría a decir una cifra distinta de la del resto del sistema.

        El registro en memoria no toca la base: no hay create, no hay write.
        """
        self.ensure_one()
        virtual = self.new({"size_grade_id": talla.id,
                            "available_qty": cantidad}, origin=self)
        return virtual.mejor_precio_hoy()

    # ------------------------------------------------------------------
    # La simulación
    # ------------------------------------------------------------------
    def simulador_cosecha(self, modo_cantidad="constante"):
        """Qué pasa con este lote si se espera 7, 15, 30 o 45 días.

        modo_cantidad:
          'constante' — se cosechan las mismas libras de hoy. Conservador:
              ignora que el animal gana peso, así que subestima lo que se gana
              esperando, y también ignora la mortalidad, así que sobrestima.
          'biomasa'  — se proyectan las libras con el crecimiento medido y con
              la mortalidad medida del propio lote. Solo tiene sentido si las
              dos series existen, y la pantalla dice cuál de las dos falta.
        """
        self.ensure_one()
        hoy = fields.Date.context_today(self)

        if (self.stage_id.code or "").strip().upper() != "ENGORDE":
            return {"ok": False, "motivo": _(
                "El simulador es para lotes de engorde. Un juvenil de 2 a 5 g "
                "no se vende por talla a una empacadora.")}
        if not self.presentation or not self.size_grade_id:
            return {"ok": False, "motivo": _(
                "Al lote le falta la talla o la presentación. Sin eso no hay "
                "escalera de tallas por la que subir.")}
        if not (self.avg_size_mg or 0.0) > 0:
            return {"ok": False, "motivo": _(
                "El lote no tiene peso promedio registrado. Es el punto de "
                "partida de toda la proyección.")}

        ritmo = self.ritmo_de_crecimiento()
        superv = self._ritmo_supervivencia()
        escalera = self._escalera_tallas()
        peso_hoy = self.avg_size_mg or 0.0
        cantidad_hoy = self.available_qty or 0.0

        # El peso de hoy es el del último pesaje, no el de hoy. Si ese pesaje
        # es viejo, el animal ya está por encima y toda la proyección arranca
        # corta: hay que decirlo en vez de disimularlo.
        dias_sin_pesar = (hoy - ritmo["hasta"]).days if ritmo.get("hasta") else None

        idx_hoy = self._indice_por_piezas(escalera, self._piezas_por_unidad(peso_hoy))
        # La talla de HOY es la que declaró la camaronera, no la que deduce la
        # fórmula: es su dato y es el que ya usa el resto del sistema para
        # cotizar el lote. La fórmula solo decide CUÁNDO sube de peldaño, y ese
        # salto se aplica sobre la talla declarada. Así la fila de hoy nunca
        # contradice a la tarjeta del lote por un redondeo de media pieza.
        idx_declarada = next((i for i, (g, _r) in enumerate(escalera)
                              if g == self.size_grade_id), None)
        if idx_declarada is None or idx_hoy is None:
            return {"ok": False, "motivo": _(
                "La talla del lote no está en la matriz de tallas y no se "
                "puede situar en la escalera.")}

        def _talla_a(dias):
            """(talla, subio_peldanos) al cabo de esos días."""
            peso = peso_hoy + ritmo["mg_dia"] * dias
            idx = self._indice_por_piezas(escalera, self._piezas_por_unidad(peso))
            destino = idx_declarada + (idx - idx_hoy)
            tope = destino >= len(escalera)
            destino = max(0, min(destino, len(escalera) - 1))
            return peso, escalera[destino][0], destino, tope

        def _cantidad_a(dias, peso):
            if modo_cantidad != "biomasa" or dias == 0:
                return cantidad_hoy
            factor_peso = peso / peso_hoy if peso_hoy else 1.0
            factor_superv = 1.0
            if superv["medido"] and superv["pct_hoy"] > 0:
                futura = max(superv["pct_hoy"] + superv["pts_dia"] * dias, 0.0)
                factor_superv = futura / superv["pct_hoy"]
            return cantidad_hoy * factor_peso * factor_superv

        filas = []
        base_total = None
        talla_previa = None
        for dias in HORIZONTES:
            peso, talla, idx_destino, tope = _talla_a(dias)
            cantidad = _cantidad_a(dias, peso)
            cruce = self._precio_para(talla, cantidad)
            mejor = cruce.get("mejor")
            total = mejor["total"] if mejor else None
            if dias == 0:
                base_total = total
            fecha = hoy + timedelta(days=dias)
            fila = {
                "dias": dias,
                "fecha": fecha,
                # El texto se arma aquí y no en la plantilla: QWeb es un mal
                # sitio para la lógica de idioma y para el formato de fechas.
                "etiqueta": _("Hoy") if dias == 0 else _("En %s días") % dias,
                "fecha_txt": fecha.strftime("%d/%m/%Y"),
                "peso_g": peso / 1000.0,
                "piezas": self._piezas_por_unidad(peso),
                "talla": talla,
                "tope": tope,
                "salto": bool(talla_previa) and talla != talla_previa,
                "cantidad": cantidad,
                "precio": mejor["precio"] if mejor else None,
                "uom": mejor["uom"] if mejor else None,
                "empacadora": mejor["empacadora"] if mejor else None,
                "calidad": mejor["calidad_txt"] if mejor else None,
                "total": total,
                "sin_precio": not mejor,
                "delta": (total - base_total) if (total is not None and base_total) else None,
            }
            filas.append(fila)
            talla_previa = talla

        # El salto de talla casi nunca cae en un horizonte redondo: si el lote
        # sube de talla el día 11, decir solo "a 7 días no ha subido y a 15 sí"
        # obliga al camaronero a adivinar la fecha. Se calcula el día exacto y
        # se valora ese día con el mismo cruce que el resto de la tabla.
        salto = self._proximo_salto(escalera, idx_hoy, idx_declarada,
                                    peso_hoy, ritmo, hoy)
        if salto:
            peso_salto = peso_hoy + ritmo["mg_dia"] * salto["dias"]
            cantidad_salto = _cantidad_a(salto["dias"], peso_salto)
            cruce = self._precio_para(salto["talla"], cantidad_salto)
            mejor = cruce.get("mejor")
            salto["precio"] = mejor["precio"] if mejor else None
            salto["uom"] = mejor["uom"] if mejor else None
            salto["empacadora"] = mejor["empacadora"] if mejor else None
            salto["total"] = mejor["total"] if mejor else None
            salto["delta"] = ((mejor["total"] - base_total)
                              if (mejor and base_total) else None)

        return {
            "ok": True,
            "hoy": hoy,
            "ritmo": ritmo,
            "superv": superv,
            "modo_cantidad": modo_cantidad,
            "peso_hoy_g": peso_hoy / 1000.0,
            "piezas_hoy": self._piezas_por_unidad(peso_hoy),
            "cantidad_hoy": cantidad_hoy,
            "dias_sin_pesar": dias_sin_pesar,
            "talla_por_peso": escalera[idx_hoy][0],
            "discrepa": idx_hoy != idx_declarada,
            "filas": filas,
            "salto": salto,
            "mejor_fila": max(
                (f for f in filas if f["total"] is not None),
                key=lambda f: f["total"], default=None),
            "tope_matriz": any(f["tope"] for f in filas),
        }

    def _proximo_salto(self, escalera, idx_hoy, idx_declarada, peso_hoy, ritmo, hoy):
        """El primer día en que el lote cambia de talla, si llega en 120 días.

        Es la respuesta corta a "¿cosecho o espero?": esperar solo paga de
        verdad cuando se cruza una frontera de talla, porque el precio no sube
        de forma continua con el peso, sube a escalones.
        """
        self.ensure_one()
        if not ritmo["mg_dia"]:
            return None
        for dias in range(1, DIAS_BUSQUEDA_SALTO + 1):
            peso = peso_hoy + ritmo["mg_dia"] * dias
            idx = self._indice_por_piezas(escalera, self._piezas_por_unidad(peso))
            if idx > idx_hoy:
                destino = idx_declarada + (idx - idx_hoy)
                if destino > len(escalera) - 1:
                    return None
                fecha = hoy + timedelta(days=dias)
                return {
                    "dias": dias,
                    "fecha": fecha,
                    "fecha_txt": fecha.strftime("%d/%m/%Y"),
                    "talla": escalera[destino][0],
                    "peso_g": peso / 1000.0,
                }
        return None
