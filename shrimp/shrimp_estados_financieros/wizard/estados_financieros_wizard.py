"""Asistente de estados financieros.

Cada informe se arma como una estructura neutra (columnas + filas ya
calculadas) que luego se pinta igual en el PDF (QWeb) y en el Excel
(xlsxwriter): los dos salen siempre con los mismos números.

Convenciones de cálculo (detalle en README.md):

* Los saldos salen de ``account.move.line`` agrupados por cuenta con
  ``_read_group`` (una consulta por período, sin N+1), con dominio de
  compañía (y sus sucursales activas), estado y diarios.
* La clase de una cuenta es el primer dígito del código (plan de la
  Superintendencia de Compañías): 1 Activo, 2 Pasivo, 3 Patrimonio,
  4 Ingresos, 5 Costos y gastos, 6 (si existe) otros costos y gastos.
* Naturaleza: 1, 5 y 6 deudora (se presenta débito − crédito); 2, 3 y 4
  acreedora (se presenta crédito − débito). Así cada sección sale en
  positivo cuando la cuenta tiene su saldo normal.
* Los niveles superiores no son cuentas "vista": se arman agrupando por
  prefijo del código. Los cortes (cuántos dígitos tiene cada nivel) se
  deducen del plan de la compañía: prefijos de ``account.group`` y cuentas
  cuyo código es prefijo de otras.
"""
import base64
import io
from collections import defaultdict
from urllib.parse import quote

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

INFORMES = [
    ("comprobacion", "Balance de comprobación"),
    ("mayor", "Libro mayor"),
    ("diario", "Libro diario"),
    ("situacion", "Estado de situación financiera"),
    ("resultados", "Estado de resultados integral"),
    ("cartera", "Cartera por cobrar y por pagar por antigüedad"),
]
# Nombre de la hoja de Excel (máximo 31 caracteres) y del archivo.
HOJAS = {
    "comprobacion": ("Balance de comprobación", "balance_comprobacion"),
    "mayor": ("Libro mayor", "libro_mayor"),
    "diario": ("Libro diario", "libro_diario"),
    "situacion": ("Situación financiera", "estado_situacion_financiera"),
    "resultados": ("Resultados integral", "estado_resultados_integral"),
    "cartera": ("Cartera por antigüedad", "cartera_antiguedad"),
}
# Informes que se imprimen en hoja horizontal (muchas columnas).
HORIZONTALES = {"mayor", "cartera"}
# Informes "a una fecha" (no usan la fecha desde).
A_UNA_FECHA = {"situacion", "cartera"}

CLASES = {
    "1": "Activo",
    "2": "Pasivo",
    "3": "Patrimonio",
    "4": "Ingresos",
    "5": "Costos y gastos",
    "6": "Otros costos y gastos",
}
# +1: naturaleza deudora (débito − crédito); -1: acreedora (crédito − débito).
NATURALEZA = {"1": 1, "2": -1, "3": -1, "4": -1, "5": 1, "6": 1}
CLASES_BALANCE = ("1", "2", "3")
CLASES_RESULTADO = ("4", "5", "6")

TRAMOS = [
    ("no_vencido", "No vencido"),
    ("d1_30", "1-30 días"),
    ("d31_60", "31-60 días"),
    ("d61_90", "61-90 días"),
    ("d90", "Más de 90 días"),
]


def _col(nombre, tipo="texto", ancho=14, blanco_si_cero=False):
    return {"nombre": nombre, "tipo": tipo, "ancho": ancho, "blanco_si_cero": blanco_si_cero}


def _fila(celdas, estilo="normal", nivel=0, span=False):
    """estilo: normal | grupo | seccion | total | gran_total | aviso."""
    return {"celdas": list(celdas), "estilo": estilo, "nivel": nivel, "span": span}


class ShrimpEstadosFinancierosWizard(models.TransientModel):
    _name = "shrimp.estados.financieros.wizard"
    _description = "Estados financieros"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", string="Empresa", required=True, readonly=True,
        default=lambda self: self.env.company)
    informe = fields.Selection(INFORMES, string="Informe", required=True, default="comprobacion")
    date_from = fields.Date(
        "Desde", required=True,
        default=lambda self: self.env.company.compute_fiscalyear_dates(
            fields.Date.context_today(self))["date_from"])
    date_to = fields.Date("Hasta", required=True, default=fields.Date.context_today)
    solo_publicados = fields.Boolean("Solo asientos publicados", default=True)
    journal_ids = fields.Many2many(
        "account.journal", string="Diarios", check_company=True,
        help="Vacío: todos los diarios.")
    nivel = fields.Selection(
        selection="_selection_nivel", string="Nivel de detalle", required=True, default="todo",
        help="Cuántos dígitos del código se muestran. Los niveles superiores se arman "
             "sumando las cuentas que empiezan por ese prefijo.")
    mostrar_sin_saldo = fields.Boolean("Mostrar cuentas sin saldo", default=False)
    comparar = fields.Boolean(
        "Comparar con el año anterior",
        help="Agrega una columna con el mismo período (o la misma fecha) del año anterior.")
    account_ids = fields.Many2many(
        "account.account", string="Cuentas", check_company=True,
        help="Libro mayor: vacío = todas las cuentas con movimiento o saldo.")
    partner_id = fields.Many2one("res.partner", string="Contacto", help="Libro mayor: filtra por contacto.")
    excel_file = fields.Binary("Excel", readonly=True, attachment=True)
    excel_filename = fields.Char("Nombre del archivo", readonly=True)

    # ------------------------------------------------------------------
    # Estructura del plan de cuentas
    # ------------------------------------------------------------------
    @api.model
    def _estructura_plan(self, company):
        """Cortes de nivel, nombres por prefijo y cuentas de la compañía.

        Dos modos:

        * Con grupos de cuentas (``account.group``, los trae el plan ``ec``):
          los niveles son los prefijos reales (grupos y cuentas cuyo código es
          prefijo de otras). No se inventan niveles intermedios sin nombre.
        * Sin grupos: niveles por prefijo del código con el patrón de la
          Superintendencia (1, 3, 5, 7, 9 dígitos), más las cuentas que son
          prefijo de otras.

        :return: dict con ``cortes`` (longitudes ordenadas, siempre incluye 1),
            ``prefijos`` (set de prefijos reales, o None si todo prefijo vale),
            ``nombres`` {prefijo: nombre} y ``cuentas`` {id: (código, nombre)}
            (incluye archivadas).
        """
        Account = self.env["account.account"].sudo().with_company(company).with_context(active_test=False)
        cuentas = {
            acc.id: (acc.code or "", acc.name or "")
            for acc in Account.search_fetch(Account._check_company_domain(company), ["code", "name"])
        }
        grupos = self.env["account.group"].sudo().with_context(active_test=False).search_fetch(
            [("company_id", "=", company.root_id.id)], ["code_prefix_start", "name"])
        nombres = {}
        prefijos = set()
        for grupo in grupos:
            if grupo.code_prefix_start:
                prefijos.add(grupo.code_prefix_start)
                nombres.setdefault(grupo.code_prefix_start, grupo.name)
        codigos = {codigo for codigo, _nombre in cuentas.values() if codigo}
        for codigo, nombre in cuentas.values():
            if not codigo:
                continue
            nombres.setdefault(codigo, nombre)
            # Cuenta cuyo código es prefijo de otra: también es un nivel.
            for largo in range(1, len(codigo)):
                if codigo[:largo] in codigos:
                    prefijos.add(codigo[:largo])
        cortes = {1} | {len(p) for p in prefijos}
        if not grupos:
            largo_max = max((len(c) for c in codigos), default=0)
            cortes |= {largo for largo in (3, 5, 7, 9) if largo < largo_max}
        return {
            "cortes": sorted(cortes),
            "prefijos": prefijos if grupos else None,
            "nombres": nombres,
            "cuentas": cuentas,
        }

    @staticmethod
    def _ancestros(codigo, estructura):
        """Prefijos de ``codigo`` que son niveles, del más corto al más largo
        (el primero es siempre la clase, 1 dígito)."""
        prefijos = estructura["prefijos"]
        return [codigo[:largo] for largo in estructura["cortes"]
                if largo < len(codigo)
                and (largo == 1 or prefijos is None or codigo[:largo] in prefijos)]

    @api.model
    def _selection_nivel(self):
        estructura = self._estructura_plan(self.env.company)
        opciones = []
        for largo in estructura["cortes"]:
            ejemplo = next(
                (p for p in sorted(estructura["nombres"]) if len(p) == largo and p.isdigit()), None)
            etiqueta = "1 dígito" if largo == 1 else f"{largo} dígitos"
            if ejemplo:
                etiqueta = f"{etiqueta} (p. ej. {ejemplo} {estructura['nombres'][ejemplo]})"
            opciones.append((str(largo), etiqueta))
        opciones.append(("todo", "Todas las cuentas"))
        return opciones

    def _nivel_digitos(self):
        return None if self.nivel in (False, "todo") else int(self.nivel)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def _companias(self):
        return self.company_id._accessible_branches() or self.company_id

    def _dominio_base(self):
        dominio = [
            ("company_id", "in", self._companias().ids),
            ("account_id", "!=", False),
            ("parent_state", "in", ("posted",) if self.solo_publicados else ("draft", "posted")),
        ]
        if self.journal_ids:
            dominio.append(("journal_id", "in", self.journal_ids.ids))
        return dominio

    def _aml(self):
        return self.env["account.move.line"].with_company(self.company_id)

    def _saldos(self, dominio_extra):
        """{cuenta_id: (débito, crédito, saldo)} con una sola consulta agrupada."""
        grupos = self._aml()._read_group(
            self._dominio_base() + list(dominio_extra),
            groupby=["account_id"],
            aggregates=["debit:sum", "credit:sum", "balance:sum"])
        return {cuenta.id: (debe or 0.0, haber or 0.0, saldo or 0.0) for cuenta, debe, haber, saldo in grupos}

    def _r(self, valor):
        """Redondea a la moneda y evita el "-0.00"."""
        return self.company_id.currency_id.round(valor or 0.0) + 0.0

    def _es_cero(self, *valores):
        return all(self.company_id.currency_id.is_zero(v or 0.0) for v in valores)

    @staticmethod
    def _fmt_fecha(fecha):
        return fecha.strftime("%d/%m/%Y") if fecha else ""

    # ------------------------------------------------------------------
    # Jerarquía por prefijo
    # ------------------------------------------------------------------
    def _filas_jerarquia(self, montos, estructura, ncols, omitir_nivel_1=True):
        """Agrupa montos por prefijo del código según el nivel de detalle.

        :param montos: {cuenta_id: [valor_col_1, valor_col_2, ...]} con el
            signo de presentación ya aplicado.
        :return: lista de filas ``[código, nombre, valores...]`` en orden de
            código, con sangría por profundidad y en negrita los niveles que
            tienen hijos visibles.
        """
        nivel = self._nivel_digitos()
        nodos = {}  # clave -> {"codigo", "nombre", "prof", "valores", "hijos"}
        for cuenta_id, valores in montos.items():
            codigo, nombre_cuenta = estructura["cuentas"].get(cuenta_id, ("", ""))
            cadena = [p for p in self._ancestros(codigo, estructura)
                      if (len(p) > 1 or not omitir_nivel_1) and (nivel is None or len(p) <= nivel)]
            if nivel is None:
                cadena.append(("cta", cuenta_id))
            elif len(codigo) <= nivel and (len(codigo) > 1 or not omitir_nivel_1):
                cadena.append(codigo)
            for prof, clave in enumerate(cadena):
                nodo = nodos.get(clave)
                if nodo is None:
                    if isinstance(clave, tuple):
                        cod, nom = codigo, nombre_cuenta
                    else:
                        cod, nom = clave, estructura["nombres"].get(clave, "")
                    nodo = nodos[clave] = {
                        "codigo": cod, "nombre": nom, "prof": prof,
                        "valores": [0.0] * ncols, "hijos": False,
                        "orden": (cod, 1 if isinstance(clave, tuple) else 0)}
                nodo["valores"] = [a + (b or 0.0) for a, b in zip(nodo["valores"], valores)]
                if prof < len(cadena) - 1:
                    nodo["hijos"] = True
        filas = []
        for nodo in sorted(nodos.values(), key=lambda n: n["orden"]):
            valores = [self._r(v) for v in nodo["valores"]]
            if not self.mostrar_sin_saldo and self._es_cero(*valores):
                continue
            filas.append(_fila(
                [nodo["codigo"], nodo["nombre"], *valores],
                estilo="grupo" if nodo["hijos"] else "normal", nivel=nodo["prof"]))
        return filas

    # ------------------------------------------------------------------
    # a. Balance de comprobación
    # ------------------------------------------------------------------
    def _informe_comprobacion(self):
        estructura = self._estructura_plan(self.company_id)
        iniciales = self._saldos([("date", "<", self.date_from)])
        periodo = self._saldos([("date", ">=", self.date_from), ("date", "<=", self.date_to)])
        ids = set(iniciales) | set(periodo)
        if self.mostrar_sin_saldo:
            ids |= set(estructura["cuentas"])
        nivel = self._nivel_digitos()
        filas_por_clave = {}
        for cuenta_id in ids:
            codigo, nombre = estructura["cuentas"].get(cuenta_id, ("", ""))
            if nivel is None:
                clave, cod, nom = ("cta", cuenta_id), codigo, nombre
            elif len(codigo) <= nivel:
                clave, cod, nom = codigo, codigo, estructura["nombres"].get(codigo, nombre)
            else:
                clave = cod = ([p for p in self._ancestros(codigo, estructura) if len(p) <= nivel]
                               or [codigo[:nivel]])[-1]
                nom = CLASES.get(cod) or estructura["nombres"].get(cod, "")
            ini = iniciales.get(cuenta_id, (0.0, 0.0, 0.0))[2]
            debe, haber, _saldo = periodo.get(cuenta_id, (0.0, 0.0, 0.0))
            acum = filas_por_clave.setdefault(clave, {"codigo": cod, "nombre": nom, "v": [0.0, 0.0, 0.0]})
            acum["v"] = [acum["v"][0] + ini, acum["v"][1] + debe, acum["v"][2] + haber]
        filas = []
        tot = [0.0, 0.0, 0.0, 0.0]
        for item in sorted(filas_por_clave.values(), key=lambda i: (i["codigo"], i["nombre"])):
            ini, debe, haber = (self._r(v) for v in item["v"])
            fin = self._r(ini + debe - haber)
            if not self.mostrar_sin_saldo and self._es_cero(ini, debe, haber):
                continue
            filas.append(_fila([item["codigo"], item["nombre"], ini, debe, haber, fin]))
            tot = [tot[0] + ini, tot[1] + debe, tot[2] + haber, tot[3] + fin]
        tot = [self._r(v) for v in tot]
        filas.append(_fila([None, "TOTALES", *tot], estilo="gran_total"))
        notas = ["Saldos: positivo = deudor, negativo = acreedor. "
                 "Saldo inicial = todo lo contabilizado antes de la fecha desde."]
        diferencia = self._r(tot[1] - tot[2])
        if not self._es_cero(diferencia):
            filas.append(_fila([None, "DIFERENCIA (débitos − créditos)", None, diferencia, None, None],
                               estilo="aviso"))
        else:
            notas.append("Comprobación: débitos = créditos.")
        return {
            "columnas": [
                _col("Código", ancho=12), _col("Cuenta", ancho=48),
                _col("Saldo inicial", "monto", 16), _col("Débitos", "monto", 16),
                _col("Créditos", "monto", 16), _col("Saldo final", "monto", 16)],
            "filas": filas,
            "notas": notas,
            "col_sangria": 1,
            "resumen": {"saldo_inicial": tot[0], "debitos": tot[1], "creditos": tot[2],
                        "saldo_final": tot[3], "diferencia": diferencia},
        }

    # ------------------------------------------------------------------
    # b. Libro mayor
    # ------------------------------------------------------------------
    def _informe_mayor(self):
        estructura = self._estructura_plan(self.company_id)
        extra = []
        if self.account_ids:
            extra.append(("account_id", "in", self.account_ids.ids))
        if self.partner_id:
            extra.append(("partner_id", "=", self.partner_id.id))
        iniciales = self._saldos([("date", "<", self.date_from)] + extra)
        lineas = self._aml().search_fetch(
            self._dominio_base() + extra + [("date", ">=", self.date_from), ("date", "<=", self.date_to)],
            ["date", "move_name", "journal_id", "partner_id", "ref", "name", "debit", "credit", "account_id"],
            order="date, move_name, id")
        por_cuenta = defaultdict(list)
        for linea in lineas:
            por_cuenta[linea.account_id.id].append(linea)
        ids = set(por_cuenta) | {c for c, v in iniciales.items() if not self._es_cero(v[2])}
        if self.mostrar_sin_saldo:
            ids |= set(self.account_ids.ids) if self.account_ids else set(estructura["cuentas"])
        filas = []
        tot_debe = tot_haber = tot_final = 0.0
        for cuenta_id in sorted(ids, key=lambda c: estructura["cuentas"].get(c, ("", ""))):
            codigo, nombre = estructura["cuentas"].get(cuenta_id, ("", ""))
            saldo = self._r(iniciales.get(cuenta_id, (0.0, 0.0, 0.0))[2])
            filas.append(_fila([f"{codigo} {nombre}"], estilo="seccion", span=True))
            filas.append(_fila([None, None, None, None, "Saldo inicial", None, None, saldo], estilo="grupo"))
            debe_cta = haber_cta = 0.0
            for linea in por_cuenta.get(cuenta_id, []):
                saldo = self._r(saldo + linea.debit - linea.credit)
                debe_cta += linea.debit
                haber_cta += linea.credit
                referencia = " / ".join(dict.fromkeys(x for x in (linea.ref, linea.name) if x))
                filas.append(_fila([
                    linea.date, linea.move_name, linea.journal_id.code, linea.partner_id.name or "",
                    referencia, linea.debit, linea.credit, saldo]))
            filas.append(_fila([None, None, None, None, f"Total {codigo}",
                                self._r(debe_cta), self._r(haber_cta), saldo], estilo="total"))
            tot_debe += debe_cta
            tot_haber += haber_cta
            tot_final += saldo
        filas.append(_fila([None, None, None, None, "TOTAL GENERAL", self._r(tot_debe),
                            self._r(tot_haber), self._r(tot_final)], estilo="gran_total"))
        notas = ["Saldo: positivo = deudor, negativo = acreedor."]
        if self.partner_id:
            notas.append(f"Filtrado por contacto: {self.partner_id.display_name}.")
        if self.account_ids:
            notas.append("Filtrado por cuentas: " + ", ".join(
                estructura["cuentas"].get(c, ("", ""))[0] for c in self.account_ids.ids) + ".")
        return {
            "columnas": [
                _col("Fecha", "fecha", 11), _col("Asiento", ancho=20), _col("Diario", ancho=9),
                _col("Contacto", ancho=28), _col("Referencia", ancho=40),
                _col("Débito", "monto", 15, True), _col("Crédito", "monto", 15, True),
                _col("Saldo", "monto", 15)],
            "filas": filas,
            "notas": notas,
            "col_sangria": None,
            "resumen": {"debitos": self._r(tot_debe), "creditos": self._r(tot_haber),
                        "cuentas": len(ids)},
        }

    # ------------------------------------------------------------------
    # c. Libro diario
    # ------------------------------------------------------------------
    def _informe_diario(self):
        estructura = self._estructura_plan(self.company_id)
        lineas = self._aml().search_fetch(
            self._dominio_base() + [("date", ">=", self.date_from), ("date", "<=", self.date_to)],
            ["date", "move_id", "move_name", "journal_id", "partner_id", "name", "debit", "credit",
             "account_id"],
            order="date, move_name, move_id, id")
        filas = []
        asiento_actual = None
        tot_debe = tot_haber = 0.0
        asientos = 0
        for linea in lineas:
            if linea.move_id != asiento_actual:
                asiento_actual = linea.move_id
                asientos += 1
                encabezado = asiento_actual.journal_id.name or ""
                if asiento_actual.ref:
                    encabezado = f"{encabezado} — {asiento_actual.ref}"
                filas.append(_fila([linea.date, linea.move_name, encabezado, None, None, None],
                                   estilo="grupo"))
            codigo, nombre = estructura["cuentas"].get(linea.account_id.id, ("", ""))
            detalle = " — ".join(x for x in (linea.partner_id.name, linea.name) if x)
            filas.append(_fila([None, None, f"{codigo} {nombre}", detalle, linea.debit, linea.credit],
                               nivel=1))
            tot_debe += linea.debit
            tot_haber += linea.credit
        filas.append(_fila([None, None, f"TOTAL ({asientos} asientos)", None,
                            self._r(tot_debe), self._r(tot_haber)], estilo="gran_total"))
        return {
            "columnas": [
                _col("Fecha", "fecha", 11), _col("Asiento", ancho=20), _col("Cuenta", ancho=42),
                _col("Contacto / detalle", ancho=40), _col("Débito", "monto", 15, True),
                _col("Crédito", "monto", 15, True)],
            "filas": filas,
            "notas": [],
            "col_sangria": 2,
            "resumen": {"asientos": asientos, "debitos": self._r(tot_debe), "creditos": self._r(tot_haber)},
        }

    # ------------------------------------------------------------------
    # d. Estado de situación financiera
    # ------------------------------------------------------------------
    def _montos_situacion(self, fecha, estructura):
        """Saldos a una fecha: {cuenta: monto presentado} de las clases 1-3,
        resultado del ejercicio, resultados anteriores no cerrados y saldo de
        cuentas fuera de las clases 1-6."""
        saldos = self._saldos([("date", "<=", fecha)])
        inicio_ejercicio = self.company_id.compute_fiscalyear_dates(fecha)["date_from"]
        previos = self._saldos([("date", "<", inicio_ejercicio)])
        montos, resultado_total, anteriores, fuera = {}, 0.0, 0.0, 0.0
        for cuenta_id, (_debe, _haber, saldo) in saldos.items():
            clase = estructura["cuentas"].get(cuenta_id, ("",))[0][:1]
            if clase in CLASES_BALANCE:
                montos[cuenta_id] = saldo * NATURALEZA[clase]
            elif clase in CLASES_RESULTADO:
                resultado_total -= saldo
            else:
                fuera += saldo
        for cuenta_id, (_debe, _haber, saldo) in previos.items():
            if estructura["cuentas"].get(cuenta_id, ("",))[0][:1] in CLASES_RESULTADO:
                anteriores -= saldo
        return montos, self._r(resultado_total - anteriores), self._r(anteriores), self._r(fuera)

    def _informe_situacion(self):
        estructura = self._estructura_plan(self.company_id)
        fechas = [self.date_to] + ([self.date_to - relativedelta(years=1)] if self.comparar else [])
        ncols = len(fechas)
        montos = defaultdict(lambda: [0.0] * ncols)
        resultado, anteriores, fuera = [], [], []
        for i, fecha in enumerate(fechas):
            m, res, ant, fue = self._montos_situacion(fecha, estructura)
            for cuenta_id, valor in m.items():
                montos[cuenta_id][i] = valor
            resultado.append(res)
            anteriores.append(ant)
            fuera.append(fue)
        if self.mostrar_sin_saldo:
            for cuenta_id, (codigo, _n) in estructura["cuentas"].items():
                if codigo[:1] in CLASES_BALANCE:
                    montos[cuenta_id]  # noqa: B018 - crea la entrada en cero

        def total_clase(clase):
            return [self._r(sum(v[i] for c, v in montos.items()
                                if estructura["cuentas"].get(c, ("",))[0][:1] == clase))
                    for i in range(ncols)]

        def de_clase(clase):
            return {c: v for c, v in montos.items() if estructura["cuentas"].get(c, ("",))[0][:1] == clase}

        filas = []
        totales = {}
        for clase in CLASES_BALANCE:
            nombre = CLASES[clase].upper()
            filas.append(_fila([clase, nombre, *([None] * ncols)], estilo="seccion"))
            filas += self._filas_jerarquia(de_clase(clase), estructura, ncols)
            total = total_clase(clase)
            if clase == "3":
                filas.append(_fila([None, "Resultado del ejercicio", *resultado], estilo="grupo"))
                if not self._es_cero(*anteriores):
                    filas.append(_fila([None, "Resultados de ejercicios anteriores no cerrados", *anteriores],
                                       estilo="grupo"))
                total = [self._r(t + r + a) for t, r, a in zip(total, resultado, anteriores)]
            totales[clase] = total
            filas.append(_fila([None, f"TOTAL {nombre}", *total], estilo="total"))
        pasivo_patrimonio = [self._r(p + q) for p, q in zip(totales["2"], totales["3"])]
        filas.append(_fila([None, "TOTAL PASIVO + PATRIMONIO", *pasivo_patrimonio], estilo="gran_total"))
        diferencia = [self._r(a - pp) for a, pp in zip(totales["1"], pasivo_patrimonio)]
        notas = ["El resultado del ejercicio es Ingresos (4) − Costos y gastos (5 y 6) desde el inicio del "
                 "ejercicio fiscal hasta la fecha; lo de ejercicios anteriores sin asiento de cierre se "
                 "muestra aparte."]
        if not self._es_cero(*diferencia):
            filas.append(_fila([None, "DIFERENCIA (Activo − Pasivo − Patrimonio)", *diferencia],
                               estilo="aviso"))
        else:
            notas.append("Comprobación: Activo = Pasivo + Patrimonio.")
        if not self._es_cero(*fuera):
            notas.append("Hay saldo en cuentas fuera de las clases 1 a 6 (no incluidas): "
                         + ", ".join("{:,.2f}".format(f) for f in fuera) + ".")
        columnas = [_col("Código", ancho=12), _col("Cuenta", ancho=55)]
        columnas += [_col(f"Al {self._fmt_fecha(f)}", "monto", 18) for f in fechas]
        return {
            "columnas": columnas,
            "filas": filas,
            "notas": notas,
            "col_sangria": 1,
            "resumen": {"activo": totales["1"], "pasivo": totales["2"], "patrimonio": totales["3"],
                        "resultado_ejercicio": resultado, "resultados_anteriores": anteriores,
                        "diferencia": diferencia},
        }

    # ------------------------------------------------------------------
    # e. Estado de resultados integral
    # ------------------------------------------------------------------
    def _periodos_resultados(self):
        periodos = [(self.date_from, self.date_to)]
        if self.comparar:
            periodos.append((self.date_from - relativedelta(years=1), self.date_to - relativedelta(years=1)))
        return periodos

    def _informe_resultados(self):
        estructura = self._estructura_plan(self.company_id)
        periodos = self._periodos_resultados()
        ncols = len(periodos)
        montos = defaultdict(lambda: [0.0] * ncols)
        for i, (desde, hasta) in enumerate(periodos):
            for cuenta_id, (_d, _h, saldo) in self._saldos([("date", ">=", desde), ("date", "<=", hasta)]).items():
                clase = estructura["cuentas"].get(cuenta_id, ("",))[0][:1]
                if clase in CLASES_RESULTADO:
                    montos[cuenta_id][i] = saldo * NATURALEZA[clase]
        if self.mostrar_sin_saldo:
            for cuenta_id, (codigo, _n) in estructura["cuentas"].items():
                if codigo[:1] in CLASES_RESULTADO:
                    montos[cuenta_id]  # noqa: B018 - crea la entrada en cero
        filas = []
        totales = {}
        for clase in CLASES_RESULTADO:
            de_clase = {c: v for c, v in montos.items()
                        if estructura["cuentas"].get(c, ("",))[0][:1] == clase}
            if clase == "6" and not de_clase:
                totales[clase] = [0.0] * ncols
                continue
            nombre = CLASES[clase].upper()
            filas.append(_fila([clase, nombre, *([None] * ncols)], estilo="seccion"))
            filas += self._filas_jerarquia(de_clase, estructura, ncols)
            total = [self._r(sum(v[i] for v in de_clase.values())) for i in range(ncols)]
            totales[clase] = total
            filas.append(_fila([None, f"TOTAL {nombre}", *total], estilo="total"))
        utilidad = [self._r(totales["4"][i] - totales["5"][i] - totales["6"][i]) for i in range(ncols)]
        filas.append(_fila([None, "UTILIDAD (PÉRDIDA) DEL EJERCICIO", *utilidad], estilo="gran_total"))
        columnas = [_col("Código", ancho=12), _col("Cuenta", ancho=55)]
        columnas += [_col(f"{self._fmt_fecha(d)} al {self._fmt_fecha(h)}", "monto", 18) for d, h in periodos]
        return {
            "columnas": columnas,
            "filas": filas,
            "notas": ["Utilidad = Ingresos (4) − Costos y gastos (5) − clase 6 (si existe). "
                      "Un valor negativo es pérdida."],
            "col_sangria": 1,
            "resumen": {"ingresos": totales["4"], "costos_gastos": totales["5"], "otros": totales["6"],
                        "utilidad": utilidad},
        }

    # ------------------------------------------------------------------
    # f. Cartera por antigüedad
    # ------------------------------------------------------------------
    @staticmethod
    def _tramo(dias):
        if dias <= 0:
            return "no_vencido"
        if dias <= 30:
            return "d1_30"
        if dias <= 60:
            return "d31_60"
        if dias <= 90:
            return "d61_90"
        return "d90"

    def _informe_cartera(self):
        fecha = self.date_to
        # Abiertas a la fecha: saldo pendiente hoy, o conciliadas después de la fecha.
        dominio = self._dominio_base() + [
            ("account_id.account_type", "in", ("asset_receivable", "liability_payable")),
            ("date", "<=", fecha),
            "|", "|",
            ("amount_residual", "!=", 0.0),
            ("matched_debit_ids.max_date", ">", fecha),
            ("matched_credit_ids.max_date", ">", fecha),
        ]
        lineas = self._aml().search_fetch(
            dominio, ["account_id", "partner_id", "balance", "date", "date_maturity",
                      "matched_debit_ids", "matched_credit_ids"])
        datos = {"cobrar": defaultdict(lambda: dict.fromkeys(k for k, _n in TRAMOS)),
                 "pagar": defaultdict(lambda: dict.fromkeys(k for k, _n in TRAMOS))}
        for linea in lineas:
            # Pendiente a la fecha: saldo menos lo conciliado hasta esa fecha.
            pendiente = (linea.balance
                         - sum(p.amount for p in linea.matched_credit_ids if p.max_date <= fecha)
                         + sum(p.amount for p in linea.matched_debit_ids if p.max_date <= fecha))
            if self._es_cero(pendiente):
                continue
            tipo = "cobrar" if linea.account_id.account_type == "asset_receivable" else "pagar"
            monto = pendiente if tipo == "cobrar" else -pendiente
            tramo = self._tramo((fecha - (linea.date_maturity or linea.date)).days)
            acumulado = datos[tipo][linea.partner_id]
            acumulado[tramo] = (acumulado[tramo] or 0.0) + monto
        filas = []
        resumen = {}
        for tipo, titulo in (("cobrar", "CUENTAS POR COBRAR"), ("pagar", "CUENTAS POR PAGAR")):
            filas.append(_fila([titulo], estilo="seccion", span=True))
            totales = dict.fromkeys((k for k, _n in TRAMOS), 0.0)
            resumen[tipo] = {}
            for partner, tramos in sorted(datos[tipo].items(), key=lambda kv: (kv[0].name or "").lower()):
                valores = [self._r(tramos[k]) for k, _n in TRAMOS]
                total = self._r(sum(valores))
                for (k, _n), v in zip(TRAMOS, valores):
                    totales[k] += v
                filas.append(_fila([partner.name or "Sin contacto", partner.vat or "", *valores, total]))
                resumen[tipo][partner.id] = dict(zip((k for k, _n in TRAMOS), valores), total=total)
            valores_tot = [self._r(totales[k]) for k, _n in TRAMOS]
            filas.append(_fila([f"TOTAL {titulo}", None, *valores_tot, self._r(sum(valores_tot))],
                               estilo="total"))
            resumen[tipo]["total"] = dict(zip((k for k, _n in TRAMOS), valores_tot),
                                          total=self._r(sum(valores_tot)))
        columnas = [_col("Contacto", ancho=40), _col("RUC / identificación", ancho=18)]
        columnas += [_col(n, "monto", 15) for _k, n in TRAMOS] + [_col("Total", "monto", 16)]
        return {
            "columnas": columnas,
            "filas": filas,
            "notas": ["Días vencidos contados desde la fecha de vencimiento (o la fecha contable si no "
                      "tiene) hasta la fecha del informe. Por pagar en positivo = lo que se debe; un "
                      "valor negativo es un anticipo o saldo a favor."],
            "col_sangria": None,
            "resumen": resumen,
        }

    # ------------------------------------------------------------------
    # Ensamblado común
    # ------------------------------------------------------------------
    def _validar(self):
        if self.informe not in A_UNA_FECHA and self.date_from > self.date_to:
            raise UserError(_("La fecha desde no puede ser posterior a la fecha hasta."))

    def _texto_periodo(self):
        if self.informe in A_UNA_FECHA:
            texto = f"Al {self._fmt_fecha(self.date_to)}"
        else:
            texto = f"Del {self._fmt_fecha(self.date_from)} al {self._fmt_fecha(self.date_to)}"
        if self.comparar and self.informe in ("situacion", "resultados"):
            texto += " (comparado con el año anterior)"
        return texto

    def _texto_filtros(self):
        partes = ["Solo asientos publicados" if self.solo_publicados else "Incluye asientos en borrador"]
        if self.journal_ids:
            partes.append("Diarios: " + ", ".join(self.journal_ids.mapped("code")))
        return ". ".join(partes) + "."

    def _celda_texto(self, valor, columna):
        if valor is None or valor is False:
            return ""
        if columna["tipo"] == "monto":
            if columna["blanco_si_cero"] and self._es_cero(valor):
                return ""
            return "{:,.2f}".format(self._r(valor))
        if columna["tipo"] == "fecha":
            return self._fmt_fecha(valor)
        return str(valor)

    def _datos_informe(self):
        """Estructura completa del informe, lista para PDF o Excel."""
        self.ensure_one()
        self._validar()
        datos = getattr(self, f"_informe_{self.informe}")()
        columnas = datos["columnas"]
        for fila in datos["filas"]:
            if fila["span"]:
                fila["textos"] = [str(fila["celdas"][0] or "")]
            else:
                fila["textos"] = [self._celda_texto(v, c) for v, c in zip(fila["celdas"], columnas)]
        ahora = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        datos.update({
            "informe": self.informe,
            "titulo": dict(INFORMES)[self.informe],
            "empresa": self.company_id.name,
            "ruc": self.company_id.vat or "",
            "periodo": self._texto_periodo(),
            "filtros": self._texto_filtros(),
            "emision": ahora.strftime("%d/%m/%Y %H:%M"),
            "moneda": self.company_id.currency_id.name,
            "horizontal": self.informe in HORIZONTALES,
            "hoja": HOJAS[self.informe][0][:31],
        })
        return datos

    # ------------------------------------------------------------------
    # Salidas
    # ------------------------------------------------------------------
    def action_ver_pdf(self):
        self.ensure_one()
        self._validar()
        xmlid = ("shrimp_estados_financieros.action_report_ef_horizontal"
                 if self.informe in HORIZONTALES
                 else "shrimp_estados_financieros.action_report_ef")
        # config=False: este informe no usa el diseño de documento de la empresa.
        return self.env.ref(xmlid).report_action(self, config=False)

    def _generar_excel(self):
        """Bytes del .xlsx del informe elegido (una hoja por informe)."""
        import xlsxwriter

        datos = self._datos_informe()
        buf = io.BytesIO()
        libro = xlsxwriter.Workbook(buf, {"in_memory": True})
        hoja = libro.add_worksheet(datos["hoja"])
        formatos = {}

        def fmt(estilo, tipo, nivel=0):
            clave = (estilo, tipo, nivel)
            if clave not in formatos:
                props = {"font_size": 10, "valign": "top"}
                if tipo == "monto":
                    props["num_format"] = "#,##0.00"
                elif tipo == "fecha":
                    props.update(num_format="dd/mm/yyyy", align="left")
                if nivel:
                    props["indent"] = nivel
                if estilo in ("grupo", "seccion", "total", "gran_total", "aviso"):
                    props["bold"] = True
                if estilo == "seccion":
                    props["bg_color"] = "#E9EEF2"
                if estilo == "total":
                    props["top"] = 1
                if estilo == "gran_total":
                    props.update(top=6, bg_color="#F2F2F2")
                if estilo == "aviso":
                    props["font_color"] = "#B00020"
                formatos[clave] = libro.add_format(props)
            return formatos[clave]

        columnas = datos["columnas"]
        ncols = len(columnas)
        titulo_fmt = libro.add_format({"bold": True, "font_size": 14})
        sub_fmt = libro.add_format({"font_size": 10})
        cab_fmt = libro.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#123E5C",
                                    "border": 1, "text_wrap": True, "valign": "vcenter",
                                    "align": "center"})
        nota_fmt = libro.add_format({"italic": True, "font_size": 9, "font_color": "#5A6B74"})
        hoja.write(0, 0, datos["empresa"], titulo_fmt)
        hoja.write(1, 0, f"RUC: {datos['ruc']}", sub_fmt)
        hoja.write(2, 0, datos["titulo"], libro.add_format({"bold": True, "font_size": 12}))
        hoja.write(3, 0, f"{datos['periodo']} — {datos['moneda']}", sub_fmt)
        hoja.write(4, 0, f"{datos['filtros']} Emitido el {datos['emision']}.", sub_fmt)
        fila_cab = 6
        for i, columna in enumerate(columnas):
            hoja.write(fila_cab, i, columna["nombre"], cab_fmt)
            hoja.set_column(i, i, columna["ancho"])
        hoja.freeze_panes(fila_cab + 1, 0)
        r = fila_cab + 1
        for fila in datos["filas"]:
            if fila["span"]:
                if ncols > 1:
                    hoja.merge_range(r, 0, r, ncols - 1, fila["textos"][0], fmt(fila["estilo"], "texto"))
                else:
                    hoja.write(r, 0, fila["textos"][0], fmt(fila["estilo"], "texto"))
            else:
                for i, (valor, columna) in enumerate(zip(fila["celdas"], columnas)):
                    nivel = fila["nivel"] if i == datos["col_sangria"] else 0
                    formato = fmt(fila["estilo"], columna["tipo"], nivel)
                    if valor is None or valor is False:
                        hoja.write_blank(r, i, None, formato)
                    elif columna["tipo"] == "monto":
                        if columna["blanco_si_cero"] and self._es_cero(valor):
                            hoja.write_blank(r, i, None, formato)
                        else:
                            hoja.write_number(r, i, valor, formato)
                    elif columna["tipo"] == "fecha":
                        hoja.write_datetime(r, i, fields.Datetime.to_datetime(valor), formato)
                    else:
                        hoja.write_string(r, i, str(valor), formato)
            r += 1
        r += 1
        for nota in datos["notas"]:
            hoja.write(r, 0, nota, nota_fmt)
            r += 1
        libro.close()
        return buf.getvalue()

    def action_descargar_excel(self):
        self.ensure_one()
        contenido = self._generar_excel()
        nombre = f"{HOJAS[self.informe][1]}_{self.date_to.isoformat()}.xlsx"
        self.write({"excel_file": base64.b64encode(contenido), "excel_filename": nombre})
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self._name}/{self.id}/excel_file/{quote(nombre)}?download=true",
            "target": "download",
        }
