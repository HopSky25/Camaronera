"""Reserva anticipada: vender la cosecha antes de sacarla del agua.

Hoy una camaronera solo puede vender lo que ya cosechó. Este fichero le deja
declarar la cosecha que viene —fecha, talla y libras estimadas, y de qué
piscina— para que las empacadoras se comprometan por adelantado a un precio.
Cuando la cosecha llega, ese compromiso se convierte en el lote y en la venta.

El problema de fondo, y lo que decide todo el diseño de aquí abajo, es que LA
ESTIMACIÓN FALLA SIEMPRE. Un camarón no se pesa hasta que sale, y la muestra
que se toma con atarraya en la orilla se equivoca en libras y en talla.
Cualquier modelo que trate la declaración como una promesa exacta produce
incumplimientos en el 100 % de los casos, y un incumplimiento que ocurre
siempre deja de significar nada.

La respuesta es la BANDA. El compromiso no es sobre un punto (40.000 lb de
30/40 el día 12), es sobre un rango que la camaronera declara por adelantado y
la empacadora acepta a sabiendas:

  * LIBRAS: ±`tolerance_lb_pct` sobre lo declarado. Dentro de la banda, la
    cosecha real ES la cosecha comprometida y se liquida por lo que salió.
    Por debajo del suelo hay faltante; por encima del techo, la empacadora
    tiene preferencia pero no obligación (véase `_evaluar`).
  * TALLA: ±`tolerance_size_steps` escalones en la escalera de tallas de esa
    presentación. La talla no es una cuestión de cantidad sino de precio, así
    que dentro de la banda el compromiso sobrevive y lo que se ajusta es el
    precio (véase `shrimp.harvest.commitment._precio_liquidado`).
  * FECHA: ±`date_tolerance_days`. Una planta reserva cámara y personal para
    una ventana; fuera de ella el compromiso ya no le sirve aunque las libras
    cuadren.

Fuera de la banda NO hay incumplimiento automático: hay reconciliación. Las dos
partes firman si aceptan la cosecha que realmente salió
(`shrimp.harvest.confirmation`). Esa es la línea que hace que el compromiso no
sea ni rígido —nadie lo usaría— ni blando —no valdría nada—: dentro de la banda
obliga, fuera de la banda obliga a hablar y a dejarlo por escrito.

Va en fichero propio dentro de shrimp_packer, sin tocar los modelos que se
desarrollan en paralelo.
"""

from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .shrimp_simulador import _rango_talla


# Banda de libras por defecto. El 20 % no es un número redondo elegido al azar:
# es el orden de magnitud del error de una biometría hecha con atarraya sobre
# una piscina de varias hectáreas. Ponerlo más fino haría que casi ninguna
# cosecha cayera dentro y el mecanismo se convertiría en papel mojado.
TOLERANCIA_LB_PCT_DEF = 20.0

# Techo duro de la banda de libras. Por encima de esto el compromiso deja de
# decir nada: "entre 20.000 y 60.000 libras" no es una reserva, es una
# intención. Se corta aquí para que nadie pueda vaciar de contenido su propio
# compromiso ensanchando la banda.
TOLERANCIA_LB_PCT_MAX = 40.0

# Escalones de talla. Uno es lo normal: 30/40 y 40/50 son piscinas vecinas en
# la misma corrida y la diferencia se resuelve con el precio. Cuatro escalones
# (30/40 -> 70/80) no es la misma cosecha que se vendió, y fingir que sí lo es
# sería cargarle a la empacadora un producto que no compró.
TOLERANCIA_ESCALONES_DEF = 1
TOLERANCIA_ESCALONES_MAX = 3

# Ventana de fecha. Una semana es lo que una planta puede reacomodar en su
# programación sin romper nada.
TOLERANCIA_DIAS_DEF = 7
TOLERANCIA_DIAS_MAX = 21

# Por debajo de esto no hay reserva que valga: vender para pasado mañana es
# vender, no reservar, y para eso ya está el marketplace. Se exige al publicar
# y no al crear, para que un borrador viejo se pueda seguir corrigiendo.
HORIZONTE_MIN_DIAS = 3


def _escalera(env, presentation):
    """Las tallas activas de una presentación, de la más grande a la más chica.

    La escalera se ordena por el número de piezas (20/30 va antes que 30/40:
    menos piezas es animal más grande) reutilizando el mismo parser de rangos
    que ya usa el simulador de cosecha, para que no haya dos ideas distintas de
    qué es "la talla siguiente" en el mismo módulo.

    Las tallas cuyo nombre no es un rango legible se van al final ordenadas por
    su secuencia: colarlas en medio falsearía la distancia entre dos tallas que
    sí son legibles, que es justo el número del que depende la banda.
    """
    tallas = env["shrimp.size.grade"].sudo().search(
        [("presentation", "=", presentation), ("active", "=", True)])

    def clave(t):
        rango = _rango_talla(t.name)
        if not rango:
            return (1, float(t.sequence or 0), t.name or "")
        return (0, (rango[0] + rango[1]) / 2.0, "")

    return tallas.sorted(clave)


def _escalones_entre(env, talla_a, talla_b):
    """Cuántos escalones hay de `talla_a` a `talla_b`. Positivo = más pequeña.

    Devuelve None cuando no se puede responder (presentaciones distintas, o una
    de las dos fuera de la escalera). None no es cero: significa "no lo sé", y
    quien llama tiene que tratarlo como desviación material en vez de dar por
    bueno que no hubo cambio.
    """
    if not talla_a or not talla_b:
        return None
    if talla_a == talla_b:
        return 0
    if talla_a.presentation != talla_b.presentation:
        return None
    escalera = list(_escalera(env, talla_a.presentation))
    if talla_a not in escalera or talla_b not in escalera:
        return None
    return escalera.index(talla_b) - escalera.index(talla_a)


class ShrimpHarvestForecast(models.Model):
    """Lo que la camaronera declara que va a cosechar.

    Es un documento dirigido y confidencial, igual que una lista de precios: la
    cosecha que viene y de qué piscina sale es información comercial de la
    finca, y publicarla al mundo le quitaría poder de negociación en vez de
    dárselo. La visibilidad se resuelve con `recipient_ids` y con reglas de
    registro, no escondiendo botones.
    """

    _name = "shrimp.harvest.forecast"
    _description = "Cosecha declarada por anticipado"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "expected_date, id desc"

    name = fields.Char(
        string="Referencia", required=True, copy=False, readonly=True,
        default=lambda self: _("Nueva"))

    farmer_partner_id = fields.Many2one(
        "res.partner", string="Camaronera", required=True, ondelete="restrict",
        index=True, tracking=True)

    # De qué piscina. No es un adorno: es lo que permite que el lote que nazca
    # de esta reserva arranque con su origen puesto, y lo que le deja a la
    # empacadora cruzar la declaración con el rendimiento histórico de esa
    # misma piscina.
    facility_id = fields.Many2one(
        "shrimp.partner.facility", string="Instalación", ondelete="set null",
        index=True)
    pond_id = fields.Many2one(
        "shrimp.partner.pond", string="Piscina", required=True,
        ondelete="restrict", index=True, tracking=True)

    # --- lo estimado ---
    expected_date = fields.Date(
        string="Fecha estimada de cosecha", required=True, tracking=True)
    expected_lb = fields.Float(
        string="Libras estimadas", required=True, digits=(16, 2), tracking=True)
    presentation = fields.Selection(
        [("entero", "Entero"), ("cola", "Cola")],
        string="Presentación", required=True, default="entero")
    size_grade_id = fields.Many2one(
        "shrimp.size.grade", string="Talla estimada", required=True,
        ondelete="restrict", index=True, tracking=True)

    # --- la banda: hasta dónde sigue valiendo el compromiso ---
    tolerance_lb_pct = fields.Float(
        string="Tolerancia en libras (%)", default=TOLERANCIA_LB_PCT_DEF,
        required=True, digits=(5, 2), tracking=True,
        help="Cuánto puede desviarse la cosecha real de lo declarado sin que el "
             "compromiso se caiga. Dentro de este margen se liquida por lo que "
             "salió; fuera, las dos partes tienen que confirmar.")
    tolerance_size_steps = fields.Integer(
        string="Tolerancia de talla (escalones)", default=TOLERANCIA_ESCALONES_DEF,
        required=True, tracking=True,
        help="Cuántas tallas arriba o abajo sigue valiendo el compromiso, con "
             "el precio ajustado. Un escalón es 30/40 a 40/50.")
    date_tolerance_days = fields.Integer(
        string="Tolerancia de fecha (días)", default=TOLERANCIA_DIAS_DEF,
        required=True, tracking=True)

    lb_min = fields.Float(
        string="Piso de la banda (lb)", compute="_compute_banda", store=True,
        digits=(16, 2))
    lb_max = fields.Float(
        string="Techo de la banda (lb)", compute="_compute_banda", store=True,
        digits=(16, 2))

    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)

    # --- a quién se le enseña ---
    # Misma idea que en la lista de precios y por el mismo motivo: cada
    # productor negocia con quien quiere, y que la competencia vea qué va a
    # cosechar y cuándo es un problema comercial, no un detalle de pantalla.
    recipient_ids = fields.Many2many(
        "res.partner", "shrimp_harvest_forecast_recipient_rel",
        "forecast_id", "partner_id", string="Dirigida a",
        help="Las empacadoras que pueden verla y comprometerse.")
    recipient_count = fields.Integer(
        compute="_compute_counts", string="Destinatarios")
    open_call = fields.Boolean(
        string="Abrirla a todas las empacadoras",
        help="Al publicar se añaden como destinatarias todas las empacadoras "
             "que aceptan reservas. Queda la foto de a quiénes se les mandó.")

    notes = fields.Text(string="Observaciones")

    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("published", "Publicada"),
            ("committed", "Comprometida"),
            ("harvested", "Cosechada"),
            ("cancelled", "Cancelada"),
            ("expired", "Vencida"),
        ],
        string="Estado", default="draft", required=True, index=True, tracking=True)

    commitment_ids = fields.One2many(
        "shrimp.harvest.commitment", "forecast_id", string="Compromisos")
    commitment_count = fields.Integer(
        compute="_compute_counts", string="Compromisos")

    # --- lo que realmente salió ---
    actual_date = fields.Date(string="Fecha real de cosecha", readonly=True)
    actual_lb = fields.Float(
        string="Libras reales", digits=(16, 2), readonly=True)
    actual_size_grade_id = fields.Many2one(
        "shrimp.size.grade", string="Talla real", ondelete="restrict",
        readonly=True)
    product_id = fields.Many2one(
        "shrimp.product", string="Lote generado", readonly=True, copy=False,
        help="El lote que nació de esta cosecha. Es el puente entre la reserva "
             "y la trazabilidad normal del marketplace.")

    # --- traza de la cancelación ---
    cancel_reason = fields.Text(string="Motivo de la cancelación", readonly=True)
    cancelled_by_uid = fields.Many2one(
        "res.users", string="Cancelada por", readonly=True)
    cancelled_at = fields.Datetime(string="Cancelada el", readonly=True)

    admite_compromisos = fields.Boolean(
        string="Admite compromisos", compute="_compute_admite_compromisos",
        search="_search_admite_compromisos")

    _chk_libras = models.Constraint(
        "CHECK(expected_lb > 0)",
        "Las libras estimadas tienen que ser mayores que cero.")
    _chk_tolerancia_lb = models.Constraint(
        "CHECK(tolerance_lb_pct >= 0 AND tolerance_lb_pct <= 100)",
        "La tolerancia en libras es un porcentaje entre 0 y 100.")

    # ==================================================================
    # Cálculos
    # ==================================================================
    @api.depends("commitment_ids", "recipient_ids")
    def _compute_counts(self):
        for rec in self:
            rec.commitment_count = len(rec.commitment_ids)
            rec.recipient_count = len(rec.recipient_ids)

    @api.depends("expected_lb", "tolerance_lb_pct")
    def _compute_banda(self):
        for rec in self:
            factor = (rec.tolerance_lb_pct or 0.0) / 100.0
            rec.lb_min = (rec.expected_lb or 0.0) * (1.0 - factor)
            rec.lb_max = (rec.expected_lb or 0.0) * (1.0 + factor)

    @api.depends("state", "expected_date", "date_tolerance_days")
    def _compute_admite_compromisos(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.admite_compromisos = (
                rec.state == "published"
                and bool(rec.expected_date)
                and rec._limite_para_comprometerse() >= hoy
            )

    def _limite_para_comprometerse(self):
        """Último día en que todavía tiene sentido comprometerse.

        Es la fecha estimada más su tolerancia: mientras la cosecha pueda
        seguir cayendo dentro de la banda, la declaración sigue viva.
        """
        self.ensure_one()
        return (self.expected_date or fields.Date.context_today(self)) + timedelta(
            days=max(self.date_tolerance_days or 0, 0))

    def _search_admite_compromisos(self, operator, value):
        # Odoo 19 normaliza los dominios de booleano a operator 'in' con un
        # CONJUNTO. Dar por hecho que llega '=' invierte el filtro en silencio,
        # que aquí significaría enseñarle a la empacadora justo las
        # declaraciones a las que ya no puede responder.
        if operator in ("in", "not in"):
            valores = set(value) if not isinstance(value, bool) else {value}
            quiere = True in valores
            if operator == "not in":
                quiere = not quiere
        else:
            quiere = bool(value) if operator == "=" else not bool(value)
        hoy = fields.Date.context_today(self)
        vivas = self.search([("state", "=", "published")]).filtered(
            lambda r: r.expected_date and r._limite_para_comprometerse() >= hoy)
        return [("id", "in" if quiere else "not in", vivas.ids)]

    # ==================================================================
    # Validaciones
    # ==================================================================
    @api.constrains("farmer_partner_id")
    def _check_camaronera(self):
        for rec in self:
            if rec.farmer_partner_id.shrimp_user_type != "camaronera":
                raise ValidationError(_(
                    "Una cosecha la declara quien la produce: una camaronera. "
                    "«%s» no lo es.") % (rec.farmer_partner_id.name or ""))

    @api.constrains("recipient_ids")
    def _check_destinatarios(self):
        for rec in self:
            ajenos = rec.recipient_ids.filtered(
                lambda p: p.shrimp_user_type != "empacadora")
            if ajenos:
                raise ValidationError(_(
                    "Una reserva de cosecha se le ofrece a empacadoras, que son "
                    "las que compran camarón de engorde. No lo son: %s.")
                    % ", ".join(ajenos.mapped("name")))

    @api.constrains("pond_id", "facility_id", "farmer_partner_id")
    def _check_origen(self):
        """La piscina declarada tiene que ser de quien declara.

        Sin esto se podría declarar la cosecha de la finca del vecino, y el
        lote que nace de la reserva arrastraría ese origen hasta el certificado
        de trazabilidad. Ensuciar el dato de otro es peor que equivocarse en el
        propio.

        Las lecturas van en sudo para poder dar este mensaje: apuntar a una
        piscina ajena revienta antes con un AccessError que no explica nada.
        """
        for rec in self:
            if rec.pond_id and rec.pond_id.sudo().partner_id != rec.farmer_partner_id:
                raise ValidationError(_(
                    "La piscina «%s» no es de esta camaronera.")
                    % (rec.pond_id.sudo().display_name or ""))
            if rec.facility_id and rec.facility_id.sudo().partner_id != rec.farmer_partner_id:
                raise ValidationError(_(
                    "La instalación «%s» no es de esta camaronera.")
                    % (rec.facility_id.sudo().display_name or ""))
            if (rec.pond_id and rec.facility_id
                    and rec.pond_id.sudo().facility_id != rec.facility_id):
                raise ValidationError(_(
                    "La piscina elegida no pertenece a esa instalación."))

    @api.constrains("presentation", "size_grade_id", "actual_size_grade_id")
    def _check_talla(self):
        for rec in self:
            if rec.size_grade_id and rec.size_grade_id.presentation != rec.presentation:
                raise ValidationError(_(
                    "La talla estimada no corresponde a la presentación elegida."))
            if (rec.actual_size_grade_id
                    and rec.actual_size_grade_id.presentation != rec.presentation):
                raise ValidationError(_(
                    "La talla real no corresponde a la presentación de la "
                    "declaración: una cosecha de entero no sale en talla de cola."))

    @api.constrains("expected_lb", "tolerance_lb_pct", "tolerance_size_steps",
                    "date_tolerance_days")
    def _check_banda(self):
        """La banda se puede ensanchar, pero no hasta vaciar el compromiso.

        Sin topes, una camaronera podría declarar 40.000 lb con un ±90 % y una
        tolerancia de cinco tallas: la empacadora se estaría comprometiendo con
        "algo de camarón, algún día". Los topes son lo que mantiene que
        aceptar un compromiso signifique algo.
        """
        for rec in self:
            if rec.expected_lb <= 0:
                raise ValidationError(_(
                    "Las libras estimadas tienen que ser mayores que cero."))
            if not 0 < rec.tolerance_lb_pct <= TOLERANCIA_LB_PCT_MAX:
                raise ValidationError(_(
                    "La tolerancia en libras va del 1 %% al %(max)s %%. Una banda "
                    "más ancha no es una reserva: es una intención, y ninguna "
                    "empacadora puede comprometer precio sobre eso.")
                    % {"max": "{:.0f}".format(TOLERANCIA_LB_PCT_MAX)})
            if not 0 <= rec.tolerance_size_steps <= TOLERANCIA_ESCALONES_MAX:
                raise ValidationError(_(
                    "La tolerancia de talla va de 0 a %(max)s escalones.")
                    % {"max": TOLERANCIA_ESCALONES_MAX})
            if not 0 <= rec.date_tolerance_days <= TOLERANCIA_DIAS_MAX:
                raise ValidationError(_(
                    "La tolerancia de fecha va de 0 a %(max)s días.")
                    % {"max": TOLERANCIA_DIAS_MAX})

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nueva")) == _("Nueva"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.harvest.forecast") or _("Nueva")
        return super().create(vals_list)

    def write(self, vals):
        """Una declaración publicada no se reescribe por debajo.

        Cambiar las libras, la talla, la fecha o la banda de una declaración
        sobre la que ya hay compromisos es cambiarle el trato a la otra parte
        sin decírselo. Para corregir hay que cancelar —lo que deja traza y
        avisa a quien se había comprometido— y volver a declarar.
        """
        terminos = {"expected_lb", "expected_date", "size_grade_id", "presentation",
                    "tolerance_lb_pct", "tolerance_size_steps", "date_tolerance_days",
                    "pond_id", "facility_id"}
        if terminos.intersection(vals):
            bloqueadas = self.filtered(lambda f: f.state not in ("draft",))
            if bloqueadas:
                raise ValidationError(_(
                    "«%s» ya está publicada: sus condiciones no se pueden cambiar "
                    "por debajo de quien esté mirándola. Cancélala indicando el "
                    "motivo y declara la cosecha corregida.")
                    % (bloqueadas[0].name or ""))
        return super().write(vals)

    # ==================================================================
    # Visibilidad
    # ==================================================================
    def visible_para(self, partner):
        """¿Este socio puede ver la declaración?

        La ve quien la hizo y la ve el destinatario —o cualquiera que cuelgue
        de un destinatario, para el grupo con varias razones sociales—. Mismo
        criterio que la lista de precios, porque es el mismo tipo de documento:
        dirigido y confidencial.
        """
        self.ensure_one()
        if not partner:
            return False
        if partner == self.farmer_partner_id:
            return True
        if self.state == "draft":
            return False
        return bool(set(partner.shrimp_grupo_ids()) & set(self.recipient_ids.ids))

    @api.model
    def visibles_para(self, partner, solo_vivas=True):
        """La bandeja de la empacadora: las declaraciones que le dirigieron."""
        if not partner:
            return self.browse()
        declaraciones = self.sudo().search([
            ("state", "in", ("published", "committed", "harvested")),
            ("recipient_ids", "in", partner.shrimp_grupo_ids()),
        ])
        if solo_vivas:
            return declaraciones.filtered("admite_compromisos")
        return declaraciones

    # ==================================================================
    # La banda
    # ==================================================================
    def _evaluar(self, actual_lb, actual_grade, actual_date):
        """¿La cosecha que salió cae dentro de lo que se comprometió?

        Devuelve el diagnóstico completo en vez de un booleano porque la
        pantalla y el acta tienen que poder decir QUÉ se salió de la banda: "no
        cuadra" sin el porqué es lo que hace que la gente deje de usar un
        mecanismo como este.

        Las tres asimetrías que hay aquí son deliberadas:

        * POR DEBAJO del piso hay faltante y no hay banda que lo tape. La
          empacadora reservó cámara y camiones para un volumen; si sale la
          mitad, eso no es "la misma cosecha con ruido".
        * POR ENCIMA del techo la empacadora NO queda obligada. Tiene
          preferencia sobre el excedente al mismo precio si lo quiere, pero
          obligarla a tomar libras sin tope es exactamente lo que haría que
          ninguna planta firmara una reserva. El sobrante por encima del techo
          la camaronera lo vende libremente.
        * La TALLA no rompe el compromiso dentro de su banda: ajusta el precio.
          Es una cuestión de cuánto vale el animal, no de si es el animal que
          se compró.
        """
        self.ensure_one()
        actual_lb = float(actual_lb or 0.0)
        motivos = []

        lb_min, lb_max = self.lb_min, self.lb_max
        falta = actual_lb < lb_min
        sobra = actual_lb > lb_max
        # Lo que queda comprometido: lo que salió, RECORTADO POR ARRIBA al
        # techo. El piso NO se usa para subir esta cifra, solo para detectar el
        # faltante: si salieron 25.000 lb no se pueden entregar las 32.000 del
        # piso porque ese camarón no existe, y una liquidación por libras que
        # no hay acabaría en una compra que el stock del lote rechaza.
        lb_banda = min(actual_lb, lb_max)
        desvio_pct = (
            100.0 * (actual_lb - self.expected_lb) / self.expected_lb
            if self.expected_lb else 0.0)
        if falta:
            motivos.append(_(
                "Salieron %(real)s lb y el piso de la banda era %(piso)s lb.")
                % {"real": "{:,.2f}".format(actual_lb),
                   "piso": "{:,.2f}".format(lb_min)})

        escalones = _escalones_entre(self.env, self.size_grade_id, actual_grade)
        talla_ok = escalones is not None and abs(escalones) <= self.tolerance_size_steps
        if not talla_ok:
            if escalones is None:
                motivos.append(_(
                    "No se puede medir la distancia entre la talla declarada "
                    "«%(dec)s» y la que salió «%(real)s».")
                    % {"dec": self.size_grade_id.display_name or "",
                       "real": actual_grade.display_name if actual_grade else ""})
            else:
                motivos.append(_(
                    "La talla se movió %(n)s escalones (de %(dec)s a %(real)s) y "
                    "la tolerancia era de %(tol)s.")
                    % {"n": abs(escalones),
                       "dec": self.size_grade_id.display_name or "",
                       "real": actual_grade.display_name if actual_grade else "",
                       "tol": self.tolerance_size_steps})

        dias = 0
        fecha_ok = True
        if actual_date and self.expected_date:
            dias = (fields.Date.to_date(actual_date) - self.expected_date).days
            fecha_ok = abs(dias) <= (self.date_tolerance_days or 0)
            if not fecha_ok:
                motivos.append(_(
                    "La cosecha se movió %(n)s días y la ventana era de %(tol)s.")
                    % {"n": abs(dias), "tol": self.date_tolerance_days or 0})

        return {
            "actual_lb": actual_lb,
            "lb_min": lb_min,
            "lb_max": lb_max,
            "lb_banda": lb_banda,
            "lb_falta": falta,
            "lb_sobra": sobra,
            "lb_excedente": max(0.0, actual_lb - lb_max),
            "lb_desvio_pct": desvio_pct,
            "lb_ok": not falta,
            "escalones": escalones,
            "talla_ok": talla_ok,
            "dias_desvio": dias,
            "fecha_ok": fecha_ok,
            # Sobrar por encima del techo NO saca la cosecha de la banda: el
            # compromiso se cumple sobre el techo y el excedente queda libre.
            "dentro": (not falta) and talla_ok and fecha_ok,
            "motivos": motivos,
        }

    # ==================================================================
    # Acciones
    # ==================================================================
    def action_publish(self, actor=None):
        """Publica la declaración y, si se pidió, la abre a todas.

        `open_call` se resuelve AQUÍ y se materializa en `recipient_ids` en vez
        de dejarse como una bandera que las reglas de acceso tengan que
        interpretar. Dos motivos: la confidencialidad queda gobernada por un
        solo campo —el mismo que la lista de precios— y queda la foto de a
        quién se le mandó realmente, que es lo que se mira el día que alguien
        pregunta por qué no se enteró.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.farmer_partner_id:
            raise AccessError(_("Una cosecha la publica quien la va a cosechar."))
        if self.state != "draft":
            raise ValidationError(_("Solo se publica una declaración en borrador."))
        hoy = fields.Date.context_today(self)
        if self.expected_date < hoy + timedelta(days=HORIZONTE_MIN_DIAS):
            raise ValidationError(_(
                "Para reservar hace falta anticipación: la cosecha tiene que "
                "estar a %(n)s días o más. Para algo que sale esta semana, "
                "publica el lote directamente.") % {"n": HORIZONTE_MIN_DIAS})
        if self.open_call and not self.recipient_ids:
            abiertas = self.env["res.partner"].sudo().search([
                ("shrimp_user_type", "=", "empacadora"),
                ("active", "=", True),
                ("reserva_acepta", "=", True),
            ])
            self.recipient_ids = [(6, 0, abiertas.ids)]
        if not self.recipient_ids:
            raise ValidationError(_(
                "Elige a qué empacadoras se la mandas, o marca «abrirla a todas»."))
        self.state = "published"
        self.message_post(body=_(
            "Cosecha declarada y publicada a %s empacadoras.")
            % len(self.recipient_ids))
        return True

    def action_cancel(self, motivo=None, actor=None):
        """Retirar la declaración. Con compromisos vivos, es echarse atrás.

        Esta es la parte que no se puede maquillar: si había una empacadora
        comprometida y la camaronera cancela, eso NO es una cancelación
        neutral. Se arrastran los compromisos aceptados a `broken` con el
        motivo, el usuario y la hora, y esa marca cuenta en la fiabilidad de
        quien canceló. Si no, echarse atrás saldría gratis y el compromiso no
        valdría nada.

        Los compromisos que todavía nadie había aceptado caen como `lapsed`:
        nadie se apoyó en ellos, no hay incumplimiento que anotar.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.farmer_partner_id:
            raise AccessError(_("Una declaración la cancela quien la publicó."))
        if self.state not in ("draft", "published", "committed"):
            raise ValidationError(_(
                "Una cosecha ya registrada no se cancela: lo que pasó, pasó."))
        motivo = (motivo or "").strip()
        if self.state != "draft" and not motivo:
            raise ValidationError(_(
                "Hay empacadoras mirando esta declaración: para retirarla hay "
                "que decir por qué."))
        aceptados = self.commitment_ids.filtered(lambda c: c.state == "accepted")
        for compromiso in aceptados:
            compromiso._marcar_incumplido(
                lado="farmer", partner=self.farmer_partner_id,
                motivo=_("La camaronera canceló la cosecha declarada: %s") % motivo)
        self.commitment_ids.filtered(lambda c: c.state == "sent").write({
            "state": "lapsed"})
        self.write({
            "state": "cancelled",
            "cancel_reason": motivo or False,
            "cancelled_by_uid": self.env.user.id,
            "cancelled_at": fields.Datetime.now(),
        })
        self.message_post(body=_("Declaración cancelada. Motivo: %s")
                          % (motivo or _("sin indicar")))
        return True

    def action_registrar_cosecha(self, actual_lb, actual_size_grade_id,
                                 actual_date=None, actor=None):
        """Llegó la cosecha: nace el lote y se resuelve el compromiso.

        Este es el punto donde la reserva deja de ser una promesa. El lote se
        crea siempre —la cosecha existió, con compromiso o sin él— y a partir
        de ahí sigue la trazabilidad normal del marketplace, que es lo que
        permite que el certificado del lote cuente su origen de piscina sin
        inventar una vía paralela.

        El estado en que nace el lote no es cosmético:

        * Con un compromiso que la banda absorbe, nace PUBLICADO y con el
          precio ya liquidado. La empacadora solo tiene que rematar la compra
          por el camino de siempre.
        * Sin compromiso vivo, o con uno pendiente de confirmar, nace en
          BORRADOR. Publicar a precio cero un camarón que aún se está
          negociando sería peor que no publicarlo.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.farmer_partner_id:
            raise AccessError(_("La cosecha la registra quien la cosechó."))
        if self.state not in ("published", "committed"):
            raise ValidationError(_(
                "Solo se registra la cosecha de una declaración viva."))
        actual_lb = float(actual_lb or 0.0)
        if actual_lb <= 0:
            raise ValidationError(_(
                "Las libras cosechadas tienen que ser mayores que cero."))
        grade = self.env["shrimp.size.grade"].sudo().browse(
            int(actual_size_grade_id or 0)).exists()
        if not grade:
            raise ValidationError(_("Indica la talla que salió."))
        if grade.presentation != self.presentation:
            raise ValidationError(_(
                "La talla real no corresponde a la presentación declarada."))
        fecha = fields.Date.to_date(actual_date) or fields.Date.context_today(self)

        self.write({
            "actual_lb": actual_lb,
            "actual_size_grade_id": grade.id,
            "actual_date": fecha,
        })

        compromiso = self.commitment_ids.filtered(lambda c: c.state == "accepted")[:1]
        diag = self._evaluar(actual_lb, grade, fecha)
        precio = None
        if compromiso:
            precio = compromiso._liquidar(diag)

        # El estadío se pone explícito. Un lote de engorde sin estadío se salta
        # la validación que exige talla, presentación y unidad para poder
        # cruzarlo con las listas de precios, y nacería justo el lote que esa
        # validación existe para evitar: uno que no se puede cotizar.
        engorde = self.env.ref(
            "shrimp_marketplace.shrimp_stage_engorde", raise_if_not_found=False)

        lote = self.env["shrimp.product"].sudo().create({
            "name": _("Cosecha %(piscina)s · %(ref)s") % {
                "piscina": self.pond_id.sudo().display_name or "",
                "ref": self.name or ""},
            "seller_partner_id": self.farmer_partner_id.id,
            "seller_role": "camaronera",
            "stage_id": engorde.id if engorde else False,
            "initial_qty": actual_lb,
            "presentation": self.presentation,
            "size_grade_id": grade.id,
            "price": precio or 0.0,
            "origin_facility_id": self.facility_id.id or False,
            "origin_pond_id": self.pond_id.id or False,
            "expected_delivery_date": fecha,
            "location": self.pond_id.sudo().location or False,
            "traceability_notes": _(
                "Lote nacido de la reserva anticipada %s.") % (self.name or ""),
            "state": "published" if (compromiso and compromiso.state == "honored")
                     else "draft",
        })
        if lote.state == "published":
            lote.published_date = fields.Datetime.now()
        self.write({"product_id": lote.id, "state": "harvested"})
        # Los compromisos que nadie aceptó mueren aquí, sin nota de
        # incumplimiento: eran ofertas, no acuerdos.
        self.commitment_ids.filtered(lambda c: c.state == "sent").write({
            "state": "lapsed"})
        if compromiso:
            compromiso.product_id = lote.id
        self.message_post(body=_(
            "Cosecha registrada: %(lb)s lb, talla %(talla)s, el %(fecha)s.")
            % {"lb": "{:,.2f}".format(actual_lb),
               "talla": grade.display_name or "",
               "fecha": fields.Date.to_string(fecha)})
        return lote

    @api.model
    def _cron_vencer(self):
        """Declaraciones que pasaron de fecha sin cosecha registrada.

        No es un incumplimiento de nadie: puede ser que la cosecha se atrasara
        o que la camaronera no volviera a entrar. Pero dejarlas «publicadas»
        para siempre llenaría de ruido la bandeja de las empacadoras, que es
        justo lo que hace que una bandeja se deje de mirar.
        """
        hoy = fields.Date.context_today(self)
        vencidas = self.search([("state", "in", ("published", "committed"))]).filtered(
            lambda f: f.expected_date and f._limite_para_comprometerse() < hoy)
        for declaracion in vencidas:
            declaracion.commitment_ids.filtered(
                lambda c: c.state in ("sent", "accepted")).write({"state": "lapsed"})
            declaracion.state = "expired"
            declaracion.message_post(body=_(
                "Venció sin cosecha registrada. Los compromisos quedan sin efecto."))
        return True
