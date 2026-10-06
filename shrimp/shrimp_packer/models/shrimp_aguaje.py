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
    # 16 lunaciones desde dos antes de enero: con 15 se quedaban fuera las
    # fases de finales de diciembre en algunos años (2027 perdía la luna
    # nueva del 27 de diciembre y su aguaje).
    for k2 in range(inicio * 2, (inicio + 16) * 2):
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
    _inherit = "shrimp.uuid.mixin"
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
        [("calculado", "Calculado (borrador)"), ("inocar", "Calendario INOCAR"),
         ("manual", "Ajustado a mano")],
        string="Origen", default="calculado", required=True,
        help="Lo calculado es una aproximación a partir de la luna. El dato "
             "bueno es el del INOCAR, que publica la Cámara de Acuacultura "
             "cada año. Si la plataforma corrige las fechas a mano, queda "
             "«Ajustado a mano» y el generador automático ya no lo toca.")

    notes = fields.Text(string="Observaciones")

    es_actual = fields.Boolean(
        string="En curso", compute="_compute_es_actual", search="_search_es_actual")
    estado = fields.Selection(
        [("pasado", "Pasado"), ("actual", "En curso"), ("proximo", "Próximo")],
        string="Estado", compute="_compute_es_actual",
        help="Pasado: ya terminó y no se puede elegir para una lista nueva. "
             "En curso y próximos: los que ofrece el selector de las listas.")
    etiqueta = fields.Char(
        string="Etiqueta", compute="_compute_etiqueta",
        help="Cómo se ofrece en el selector de las listas de precios.")

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
            if rec.date_to and rec.date_to < hoy:
                rec.estado = "pasado"
            elif rec.es_actual:
                rec.estado = "actual"
            else:
                rec.estado = "proximo"

    @api.depends("numero", "date_from", "date_to", "peak_from", "peak_to")
    def _compute_etiqueta(self):
        """«Aguaje 18 · del 05/10 al 09/10 (máximo 06–07)»: lo que se lee en
        el selector. Con el número y las fechas basta para reconocerlo; el año
        solo se añade cuando no es el de hoy, para no confundir enero."""
        hoy = fields.Date.context_today(self)

        def dm(d):
            return d.strftime("%d/%m") if d else ""
        for rec in self:
            if not (rec.date_from and rec.date_to):
                rec.etiqueta = rec.name or ""
                continue
            hasta = dm(rec.date_to)
            if rec.date_to.year != hoy.year:
                hasta = rec.date_to.strftime("%d/%m/%Y")
            txt = _("Aguaje %(n)s · del %(d)s al %(h)s") % {
                "n": rec.numero or "", "d": dm(rec.date_from), "h": hasta}
            if rec.peak_from and rec.peak_to:
                txt += _(" (máximo %(d)s–%(h)s)") % {
                    "d": rec.peak_from.strftime("%d"), "h": rec.peak_to.strftime("%d")}
            rec.etiqueta = txt

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

    @api.constrains("date_from", "date_to")
    def _check_sin_solape(self):
        """Dos aguajes no pueden pisarse: una fecha pertenece a uno solo, que
        es lo que permite proponer el aguaje de una fecha de despacho y el
        siguiente al copiar una lista."""
        for rec in self:
            if not (rec.date_from and rec.date_to):
                continue
            if rec.date_to < rec.date_from:
                raise ValidationError(_("Un aguaje no puede terminar antes de empezar."))
            otro = self.search([
                ("id", "!=", rec.id),
                ("date_from", "<=", rec.date_to),
                ("date_to", ">=", rec.date_from),
            ], limit=1)
            if otro:
                raise ValidationError(_(
                    "«%(a)s» (del %(d1)s al %(h1)s) se cruza con «%(b)s» (del "
                    "%(d2)s al %(h2)s). Los aguajes no pueden solaparse.") % {
                    "a": rec.name, "d1": rec.date_from.strftime("%d/%m/%Y"),
                    "h1": rec.date_to.strftime("%d/%m/%Y"),
                    "b": otro.name, "d2": otro.date_from.strftime("%d/%m/%Y"),
                    "h2": otro.date_to.strftime("%d/%m/%Y")})

    def write(self, vals):
        """Corregir las fechas de un aguaje calculado lo marca como ajustado a
        mano, para que el generador automático sepa que ya no es suyo."""
        fechas = {"date_from", "date_to", "peak_from", "peak_to"}
        if fechas & set(vals) and "origen" not in vals:
            calculados = self.filtered(lambda a: a.origen == "calculado")
            if calculados:
                super(ShrimpAguaje, calculados).write(dict(vals, origen="manual"))
                resto = self - calculados
                return super(ShrimpAguaje, resto).write(vals) if resto else True
        return super().write(vals)

    # ------------------------------------------------------------------
    @api.model
    def aguaje_de(self, fecha):
        """El aguaje al que pertenece una fecha, o vacío si cae entre dos."""
        if not fecha:
            return self.browse()
        return self.search([("date_from", "<=", fecha), ("date_to", ">=", fecha)], limit=1)

    @api.model
    def seleccionables(self, incluir=None, limite=30):
        """Los aguajes que se pueden elegir para una lista: el que está en
        curso y los próximos. Los pasados nunca. ``incluir`` añade el que la
        lista ya tenga, para no borrárselo al guardar una lista histórica."""
        hoy = fields.Date.context_today(self)
        dominio = [("date_to", ">=", hoy)]
        if incluir:
            dominio = ["|", ("id", "in", incluir.ids)] + dominio
        return self.search(dominio, order="date_from", limit=limite)

    def siguiente(self):
        """El aguaje que viene después de este; sin este, el primero elegible
        desde hoy (el que está en curso o el próximo). Nunca uno pasado: si la
        lista de partida era de hace meses, se propone el de ahora."""
        hoy = fields.Date.context_today(self)
        dominio = [("date_to", ">=", hoy)]
        if self[:1].date_to:
            dominio.append(("date_from", ">", self[:1].date_to))
        return self.search(dominio, order="date_from", limit=1)

    @api.model
    def sembrar_anio(self, anio, dias_antes=1, dias_despues=3, completar=False):
        """Crea los aguajes del año a partir de las lunas. Idempotente.

        El desfase por defecto sale de contrastar el calendario del INOCAR: la
        luna llena del 13 de abril de 2025 dio aguaje del 12 al 15, y la luna
        nueva del 27 dio del 27 al 30. O sea, del dia anterior a la fase hasta
        tres dias despues. Es una aproximacion y como tal queda marcada.

        Nunca toca un aguaje que ya exista (ni calculado, ni del INOCAR, ni
        ajustado a mano). Sin ``completar``, si el año ya tiene aguajes no hace
        nada, como siempre. Con ``completar`` añade solo las fases que no
        caigan sobre un aguaje existente: sirve para tapar huecos sin duplicar.
        """
        anio = int(anio)
        existentes = self.search([("year", "=", anio)])
        if existentes and not completar:
            return existentes
        creados = self.browse()
        usados = set(existentes.mapped("numero"))
        for i, (fecha, fase) in enumerate(_lunas(anio), start=1):
            desde = fecha - datetime.timedelta(days=dias_antes)
            hasta = fecha + datetime.timedelta(days=dias_despues)
            if self.search_count([("date_from", "<=", hasta), ("date_to", ">=", desde)]):
                continue
            numero = i if i not in usados else max(usados | {0}) + 1
            usados.add(numero)
            creados |= self.create({
                "name": _("Aguaje %(n)s de %(a)s") % {"n": numero, "a": anio},
                "year": anio, "numero": numero,
                "date_from": desde, "date_to": hasta,
                "fase": fase, "origen": "calculado",
            })
        return existentes | creados

    @api.model
    def sembrar_desde_hoy(self, anios=2):
        """Se llama al instalar y actualizar: deja el calendario listo."""
        hoy = fields.Date.context_today(self)
        for a in range(hoy.year, hoy.year + anios):
            self.sembrar_anio(a)
        return True

    # Cuántos aguajes por delante tiene que haber siempre. Con seis (unos tres
    # meses) la empacadora siempre tiene a qué aguaje dirigir su lista.
    MINIMO_FUTUROS = 6

    @api.model
    def _cron_asegurar_calendario(self, minimo=None):
        """Que el selector de las listas nunca quede vacío.

        Siembra el año en curso y el siguiente si no tienen aguajes (lo de
        siempre) y, si aun así quedan menos de ``minimo`` por delante, completa
        los huecos de los años siguientes. Solo crea calculados que no pisen
        nada: lo ajustado a mano o traído del INOCAR no se toca nunca.
        """
        minimo = minimo or self.MINIMO_FUTUROS
        hoy = fields.Date.context_today(self)
        self.sembrar_desde_hoy()
        anio = hoy.year
        while self.search_count([("date_to", ">=", hoy)]) < minimo and anio <= hoy.year + 3:
            self.sembrar_anio(anio, completar=True)
            anio += 1
        return True


class ShrimpAguajeGenerar(models.TransientModel):
    """Asistente del gestor de la plataforma para generar un año de aguajes."""

    _name = "shrimp.aguaje.generar"
    _description = "Generar los aguajes de un año"

    year = fields.Integer(
        string="Año", required=True,
        default=lambda self: fields.Date.context_today(self).year + 1)
    completar = fields.Boolean(
        string="Completar huecos si el año ya tiene aguajes",
        help="Sin marcar, un año que ya tiene aguajes no se toca. Marcado, se "
             "añaden solo las fases que no caen sobre un aguaje existente: "
             "nunca se duplica ni se modifica lo que ya está (tampoco lo "
             "ajustado a mano o lo del INOCAR).")

    def action_generar(self):
        self.ensure_one()
        if not 2000 <= self.year <= 2100:
            raise ValidationError(_("Indica un año entre 2000 y 2100."))
        Aguaje = self.env["shrimp.aguaje"]
        antes = Aguaje.search_count([("year", "=", self.year)])
        Aguaje.sembrar_anio(self.year, completar=self.completar)
        nuevos = Aguaje.search_count([("year", "=", self.year)]) - antes
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("Calendario de aguajes"),
                "message": (_("Se crearon %(n)s aguajes para %(a)s.") % {"n": nuevos, "a": self.year})
                if nuevos else _("%s ya tenía sus aguajes: no se creó ninguno.") % self.year,
                "type": "success" if nuevos else "warning",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
