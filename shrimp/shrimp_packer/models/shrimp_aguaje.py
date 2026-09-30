# -*- coding: utf-8 -*-
import datetime
import math

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


def _lunas(anio):
    """Lunas nuevas y llenas del año, por la serie de Meeus simplificada.

    Solo para SEMBRAR el calendario. El aguaje real lo publica el INOCAR cada
    año y depende de las tablas de marea, no solo de la luna: dos aguajes con
    la misma fase pueden tener alturas muy distintas segun la distancia de la
    luna. Por eso lo calculado nace marcado como borrador y se corrige contra
    el calendario oficial, que es el que usa todo el sector.
    """
    salida = []
    inicio = int((anio - 2000) * 12.3685) - 2
    for k2 in range(inicio * 2, (inicio + 15) * 2):
        k = k2 / 2.0                      # los medios ciclos son luna llena
        t = k / 1236.85
        jde = 2451550.09766 + 29.530588861 * k + 0.00015437 * t * t
        m = math.radians((2.5534 + 29.10535670 * k) % 360)
        mp = math.radians((201.5643 + 385.81693528 * k) % 360)
        jde += -0.40720 * math.sin(mp) + 0.17241 * math.sin(m) + 0.01608 * math.sin(2 * mp)
        fecha = (datetime.datetime(2000, 1, 1, 12)
                 + datetime.timedelta(days=jde - 2451545.0)).date()
        if fecha.year == anio:
            salida.append((fecha, "nueva" if float(k).is_integer() else "llena"))
    return sorted(salida)


class ShrimpAguaje(models.Model):
    """Un aguaje del calendario: el periodo de mareas vivas en que se cosecha.

    En el sector la semana no es la unidad, el aguaje si: la camaronera vacia y
    llena sus piscinas con la marea, asi que la cosecha se programa contra este
    calendario y no contra el almanaque. Por eso la empacadora reparte su lista
    de precios "para el aguaje del 5 al 11" y no "para la semana 41".

    Hay dos al mes, alrededor de la luna nueva y la llena, de unos cuatro o
    cinco dias cada uno. Dentro del periodo, los dias de marea mas alta son el
    MAXIMO aguaje, que es cuando se programa la cosecha grande.
    """

    _name = "shrimp.aguaje"
    _description = "Aguaje del calendario de mareas"
    _order = "date_from"
    _rec_name = "name"

    name = fields.Char(string="Aguaje", required=True, index=True)
    year = fields.Integer(string="Año", required=True, index=True)
    numero = fields.Integer(
        string="Número del año", required=True,
        help="Los aguajes se cuentan correlativos dentro del año: 1, 2, 3…")

    date_from = fields.Date(string="Desde", required=True, index=True)
    date_to = fields.Date(string="Hasta", required=True, index=True)

    # Los dias de marea mas alta dentro del periodo. Opcionales porque no se
    # pueden calcular de forma fiable: vienen del calendario del INOCAR.
    peak_from = fields.Date(string="Máximo aguaje desde")
    peak_to = fields.Date(string="Máximo aguaje hasta")

    fase = fields.Selection(
        [("nueva", "Luna nueva"), ("llena", "Luna llena")],
        string="Fase lunar", required=True)

    origen = fields.Selection(
        [("calculado", "Calculado (borrador)"), ("inocar", "Calendario INOCAR")],
        string="Origen", default="calculado", required=True,
        help="Lo calculado es una aproximación a partir de la luna. El dato "
             "bueno es el del INOCAR, que publica la Cámara de Acuacultura "
             "cada año.")

    notes = fields.Text(string="Observaciones")

    es_actual = fields.Boolean(
        string="En curso", compute="_compute_es_actual", search="_search_es_actual")

    _fechas_coherentes = models.Constraint(
        "CHECK(date_to >= date_from)",
        "Un aguaje no puede terminar antes de empezar.")
    _uniq_numero = models.Constraint(
        "UNIQUE(year, numero)",
        "Ya hay un aguaje con ese número en ese año.")

    @api.depends("date_from", "date_to")
    def _compute_es_actual(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.es_actual = bool(rec.date_from and rec.date_to
                                 and rec.date_from <= hoy <= rec.date_to)

    def _search_es_actual(self, operator, value):
        """Buscar por un booleano CALCULADO, sin almacenarlo.

        No se almacena porque cambia solo con el paso del dia: un campo guardado
        habria que recalcularlo cada madrugada y quedaria mintiendo en cuanto
        fallara ese recalculo.

        Dos cuidados, los dos aprendidos a golpes en este proyecto:

        1) Odoo 19 normaliza los dominios de booleano a operator 'in' con un
           conjunto. Dar por hecho '=' invierte el filtro EN SILENCIO, que es de
           los fallos mas caros porque la pantalla se ve bien y ensena justo lo
           contrario de lo que se pidio.

        2) Hay que devolver UNA sola condicion. Una lista de dos hojas no la
           sustituye bien y el filtro acaba dejando pasar todo: se probo, y
           daba los 49 aguajes como "en curso" cuando no habia ninguno. Se
           resuelven los ids y se devuelve un unico "id in". La tabla son dos
           docenas de filas al ano, asi que recorrerla no cuesta nada.
        """
        if operator in ("in", "not in"):
            valores = set(value) if not isinstance(value, bool) else {value}
            quiere = True in valores
            if operator == "not in":
                quiere = not quiere
        else:
            quiere = bool(value) if operator == "=" else not bool(value)
        hoy = fields.Date.context_today(self)
        en_curso = self.search([("date_from", "<=", hoy), ("date_to", ">=", hoy)])
        return [("id", "in" if quiere else "not in", en_curso.ids)]

    @api.constrains("peak_from", "peak_to", "date_from", "date_to")
    def _check_maximo(self):
        for rec in self:
            if not rec.peak_from and not rec.peak_to:
                continue
            if not (rec.peak_from and rec.peak_to):
                raise ValidationError(_(
                    "El máximo aguaje necesita fecha de inicio y de fin, o ninguna."))
            if rec.peak_to < rec.peak_from:
                raise ValidationError(_(
                    "El máximo aguaje no puede terminar antes de empezar."))
            if rec.peak_from < rec.date_from or rec.peak_to > rec.date_to:
                raise ValidationError(_(
                    "El máximo aguaje tiene que caer dentro del aguaje."))

    # ------------------------------------------------------------------
    @api.model
    def aguaje_de(self, fecha):
        """El aguaje al que pertenece una fecha, o vacío si cae entre dos."""
        if not fecha:
            return self.browse()
        return self.search([("date_from", "<=", fecha), ("date_to", ">=", fecha)], limit=1)

    @api.model
    def sembrar_anio(self, anio, dias_antes=1, dias_despues=3):
        """Crea los aguajes del año a partir de las lunas. Idempotente.

        El desfase por defecto sale de contrastar el calendario del INOCAR: la
        luna llena del 13 de abril de 2025 dio aguaje del 12 al 15, y la luna
        nueva del 27 dio del 27 al 30. O sea, del dia anterior a la fase hasta
        tres dias despues. Es una aproximacion y como tal queda marcada.
        """
        existentes = self.search([("year", "=", anio)])
        if existentes:
            return existentes
        creados = self.browse()
        for i, (fecha, fase) in enumerate(_lunas(anio), start=1):
            desde = fecha - datetime.timedelta(days=dias_antes)
            hasta = fecha + datetime.timedelta(days=dias_despues)
            creados |= self.create({
                "name": _("Aguaje %(n)s de %(a)s") % {"n": i, "a": anio},
                "year": anio, "numero": i,
                "date_from": desde, "date_to": hasta,
                "fase": fase, "origen": "calculado",
            })
        return creados

    @api.model
    def sembrar_desde_hoy(self, anios=2):
        """Se llama al instalar y actualizar: deja el calendario listo."""
        hoy = fields.Date.context_today(self)
        for a in range(hoy.year, hoy.year + anios):
            self.sembrar_anio(a)
        return True
