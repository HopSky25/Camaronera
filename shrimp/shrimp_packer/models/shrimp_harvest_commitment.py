"""El compromiso de la empacadora sobre una cosecha que todavía está en el agua.

La pregunta difícil de este fichero es EL PRECIO, y la respuesta corta es: los
dos modos, pero cada uno con su contrapeso obligatorio y cada uno con su
horizonte.

PRECIO CERRADO (`fijo`). Es lo que la camaronera quiere: saber hoy cuánto va a
cobrar dentro de un mes para poder presupuestar la siembra siguiente. Su riesgo
es que el camarón es un commodity que se mueve todas las semanas: si el mercado
sube, la camaronera siente que le robaron y busca la forma de romper; si baja,
es la empacadora la que busca la forma. Un precio cerrado a cuatro meses no es
un acuerdo, es una apuesta con dos perdedores posibles, y el que pierda va a
incumplir. Por eso aquí el precio cerrado SOLO se admite dentro de
`DIAS_MAX_PRECIO_FIJO` días: en ese horizonte el mercado no se mueve tanto como
para que a alguien le salga a cuenta romper.

PRECIO A LISTA DEL DÍA (`lista`). Se liquida con la lista de precios que esa
empacadora tenga vigente para esa camaronera el día de la cosecha —el mismo
documento que ya reparte cada semana—. Es el precio real del mercado y nadie
se siente estafado. Su defecto es fatal si se deja solo: una reserva sin precio
no sirve para planificar, que es TODA la razón por la que la camaronera usaría
esto. Sería una agenda, no una venta. Por eso el modo lista exige un PISO
(`price_floor_per_lb`): la camaronera sabe el peor caso y puede presupuestar
sobre él; la empacadora paga lo que valga el día, nunca menos del piso.

Y en los dos modos hay que resolver la talla, porque la talla que sale no es la
declarada casi nunca:

* En `lista`, se resuelve solo: se busca el renglón de la talla que REALMENTE
  salió. Es la ventaja grande de este modo.
* En `fijo` haría falta un escalón declarado (`step_delta_per_lb`): cuánto sube
  o baja el precio por cada talla de diferencia. Sin él, un precio cerrado es
  una lotería sobre la talla, y es el hueco por el que se rompen los acuerdos
  reales del sector.
"""

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .shrimp_product import LB_POR_KG
from .shrimp_harvest_forecast import _escalones_entre


# Hasta dónde se admite cerrar precio. Mes y medio es el horizonte en el que
# una empacadora ya tiene visibilidad de sus propias ventas al exterior; más
# allá estaría cotizando a ciegas y el número que pusiera sería malo para
# alguien. Coincide además con el horizonte largo del simulador de cosecha.
DIAS_MAX_PRECIO_FIJO = 45


class ShrimpHarvestCommitment(models.Model):
    """Lo que una empacadora se compromete a comprar y a qué precio.

    Sigue el mismo camino que una oferta de empaque —se envía, el dueño del
    producto acepta una, las demás se descartan— porque es el mismo problema:
    varias plantas responden y una sola se lleva el trabajo.
    """

    _name = "shrimp.harvest.commitment"
    _description = "Compromiso de compra anticipada"
    _inherit = ["mail.thread", "shrimp.uuid.mixin"]
    # Sin orden por precio: un compromiso a lista del dia no trae precio
    # cerrado, asi que ordenar por `price_per_lb` mandaria al final justo a
    # los que traen piso garantizado. La pantalla ordena por lo que se puede
    # comparar de verdad, que es el peor caso de cada uno.
    _order = "id"

    forecast_id = fields.Many2one(
        "shrimp.harvest.forecast", string="Cosecha declarada", required=True,
        ondelete="cascade", index=True)
    packer_partner_id = fields.Many2one(
        "res.partner", string="Empacadora", required=True, ondelete="restrict",
        index=True, tracking=True)
    farmer_partner_id = fields.Many2one(
        related="forecast_id.farmer_partner_id", string="Camaronera",
        store=True, index=True)
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)

    committed_lb = fields.Float(
        string="Libras que toma", required=True, digits=(16, 2), tracking=True,
        help="Puede ser menos de lo declarado: la camaronera decide si le sirve.")

    # --- el precio ---
    price_mode = fields.Selection(
        [
            ("fijo", "Precio cerrado"),
            ("lista", "Lista vigente el día de la cosecha, con piso"),
        ],
        string="Cómo se fija el precio", required=True, default="fijo",
        tracking=True)
    price_per_lb = fields.Monetary(
        string="Precio cerrado ($/lb)", currency_field="currency_id",
        tracking=True,
        help="Para la talla declarada. Si sale otra talla, se ajusta con el "
             "escalón.")
    step_delta_per_lb = fields.Monetary(
        string="Escalón de talla ($/lb)", currency_field="currency_id",
        help="Cuánto baja el precio por cada talla más pequeña de lo declarado, "
             "y cuánto sube por cada talla más grande.")
    price_floor_per_lb = fields.Monetary(
        string="Piso garantizado ($/lb)", currency_field="currency_id",
        tracking=True,
        help="Lo mínimo que se paga aunque la lista del día esté por debajo. "
             "Es lo que hace que una reserva a lista sirva para presupuestar.")
    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista aplicada", readonly=True,
        help="La lista vigente el día de la cosecha con la que se liquidó.")

    valid_until = fields.Date(
        string="Vale hasta", required=True,
        help="Hasta cuándo aguanta este compromiso si la camaronera no responde.")
    notes = fields.Text(string="Condiciones")

    state = fields.Selection(
        [
            ("sent", "Enviado"),
            ("accepted", "Aceptado"),
            ("rejected", "Descartado"),
            ("withdrawn", "Retirado"),
            ("to_confirm", "Pendiente de confirmar"),
            ("honored", "Cumplido"),
            ("released", "Liberado de común acuerdo"),
            ("broken", "Incumplido"),
            ("lapsed", "Sin efecto"),
        ],
        string="Estado", default="sent", required=True, index=True, tracking=True)

    accepted_at = fields.Datetime(string="Aceptado el", readonly=True)
    accepted_by_uid = fields.Many2one(
        "res.users", string="Aceptado por", readonly=True)

    # --- lo liquidado ---
    settled_lb = fields.Float(
        string="Libras liquidadas", digits=(16, 2), readonly=True)
    settled_price_per_lb = fields.Monetary(
        string="Precio liquidado ($/lb)", currency_field="currency_id",
        readonly=True)
    settled_total = fields.Monetary(
        string="Total liquidado", currency_field="currency_id",
        compute="_compute_settled_total", store=True)
    settled_steps = fields.Integer(
        string="Escalones de talla aplicados", readonly=True)
    floor_applied = fields.Boolean(
        string="Se aplicó el piso", readonly=True,
        help="La lista del día pagaba menos que el piso garantizado.")
    deviation_notes = fields.Text(
        string="Qué se salió de la banda", readonly=True)
    product_id = fields.Many2one(
        "shrimp.product", string="Lote", readonly=True)
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Transacción", readonly=True)

    # --- traza de quien se echa atrás ---
    # Se guarda aquí y no en un texto libre porque es lo único que le da peso
    # al compromiso: sin constancia de quién rompió y por qué, romper sale
    # gratis y el mecanismo entero deja de significar nada.
    break_side = fields.Selection(
        [("farmer", "La camaronera"), ("packer", "La empacadora")],
        string="Quién se echó atrás", readonly=True)
    broken_by_partner_id = fields.Many2one(
        "res.partner", string="Parte que rompió", readonly=True, index=True)
    broken_by_uid = fields.Many2one(
        "res.users", string="Registrado por", readonly=True)
    broken_at = fields.Datetime(string="Fecha de la ruptura", readonly=True)
    break_reason = fields.Text(string="Motivo", readonly=True)

    confirmation_ids = fields.One2many(
        "shrimp.harvest.confirmation", "commitment_id", string="Confirmaciones")

    _uniq_compromiso = models.Constraint(
        "UNIQUE(forecast_id, packer_partner_id)",
        "Cada empacadora hace un solo compromiso por cosecha declarada; para "
        "cambiarlo, se edita.")
    _chk_libras = models.Constraint(
        "CHECK(committed_lb > 0)",
        "Las libras comprometidas tienen que ser mayores que cero.")

    # ==================================================================
    # Cálculos y validaciones
    # ==================================================================
    @api.depends("settled_lb", "settled_price_per_lb")
    def _compute_settled_total(self):
        for rec in self:
            rec.settled_total = (rec.settled_lb or 0.0) * (rec.settled_price_per_lb or 0.0)

    @api.constrains("packer_partner_id")
    def _check_empacadora(self):
        for rec in self:
            if not rec.packer_partner_id._shrimp_has_role("empacadora"):
                raise ValidationError(_(
                    "El camarón de engorde lo compra una empacadora. «%s» no lo es.")
                    % (rec.packer_partner_id.name or ""))

    @api.constrains("packer_partner_id", "forecast_id")
    def _check_destinatario(self):
        """No se compromete quien no fue invitado.

        La declaración es confidencial y dirigida. Sin esta comprobación, una
        empacadora que averiguara la referencia por cualquier vía podría
        colarse en una negociación a la que no se la llamó.
        """
        for rec in self:
            declaracion = rec.forecast_id.sudo()
            grupo = set(rec.packer_partner_id.shrimp_grupo_ids())
            if not grupo & set(declaracion.recipient_ids.ids):
                raise ValidationError(_(
                    "Esta cosecha no se le ofreció a «%s».")
                    % (rec.packer_partner_id.name or ""))

    @api.constrains("committed_lb", "forecast_id")
    def _check_libras(self):
        """Comprometerse por más del techo de la banda no significa nada.

        La camaronera no puede entregar libras que no van a existir: el techo
        es el máximo que la declaración puede llegar a dar. Aceptar un
        compromiso por encima crearía un faltante garantizado el día de la
        cosecha, con la empacadora reclamando algo que nunca fue posible.
        """
        for rec in self:
            if rec.committed_lb <= 0:
                raise ValidationError(_(
                    "Las libras comprometidas tienen que ser mayores que cero."))
            techo = rec.forecast_id.sudo().lb_max
            if techo and rec.committed_lb > techo + 1e-6:
                raise ValidationError(_(
                    "Esta cosecha no puede dar más de %(techo)s lb ni en su mejor "
                    "caso, y te estás comprometiendo por %(pide)s lb.")
                    % {"techo": "{:,.2f}".format(techo),
                       "pide": "{:,.2f}".format(rec.committed_lb)})

    @api.constrains("price_mode", "price_per_lb", "step_delta_per_lb",
                    "price_floor_per_lb", "forecast_id")
    def _check_precio(self):
        """Cada modo de precio con su contrapeso obligatorio. Véase la cabecera."""
        for rec in self:
            declaracion = rec.forecast_id.sudo()
            if rec.price_mode == "fijo":
                if rec.price_per_lb <= 0:
                    raise ValidationError(_(
                        "Un precio cerrado tiene que traer el precio."))
                if rec.step_delta_per_lb < 0:
                    raise ValidationError(_(
                        "El escalón de talla se declara en positivo: es cuánto "
                        "baja el precio por cada talla más pequeña."))
                horizonte = (declaracion.expected_date
                             - fields.Date.context_today(rec)).days
                if horizonte > DIAS_MAX_PRECIO_FIJO:
                    raise ValidationError(_(
                        "Faltan %(dias)s días para esa cosecha: a más de "
                        "%(max)s días no se cierra precio, porque el mercado se "
                        "mueve y el que pierda la apuesta va a romper el trato. "
                        "Comprométete con la lista del día y un piso garantizado.")
                        % {"dias": horizonte, "max": DIAS_MAX_PRECIO_FIJO})
            else:
                if rec.price_floor_per_lb <= 0:
                    raise ValidationError(_(
                        "Un compromiso a lista del día necesita un piso. Sin él, "
                        "la camaronera no sabe con cuánto cuenta y la reserva no "
                        "le sirve para planificar, que es para lo único que la usa."))

    @api.constrains("valid_until", "forecast_id")
    def _check_vigencia(self):
        for rec in self:
            if not rec.valid_until:
                continue
            if rec.valid_until < fields.Date.context_today(rec):
                raise ValidationError(_(
                    "Un compromiso que ya venció no se puede enviar."))
            if (rec.forecast_id.expected_date
                    and rec.valid_until > rec.forecast_id.expected_date):
                raise ValidationError(_(
                    "El compromiso no puede seguir vivo después de la cosecha: "
                    "para entonces ya no hay nada que reservar."))

    def write(self, vals):
        """Editar un compromiso retirado lo vuelve a poner sobre la mesa.

        La clave única (cosecha, empacadora) impide crear otro, así que sin
        esto retirar sería una puerta de un solo sentido. Mismo criterio que la
        oferta de empaque: se revive solo si cambian las condiciones
        comerciales y si la declaración sigue admitiendo compromisos.
        """
        terminos = {"committed_lb", "price_mode", "price_per_lb",
                    "step_delta_per_lb", "price_floor_per_lb", "valid_until"}
        revivir = self.browse()
        if "state" not in vals and terminos.intersection(vals):
            revivir = self.filtered(
                lambda c: c.state == "withdrawn"
                and c.forecast_id.admite_compromisos)
        res = super().write(vals)
        if revivir:
            revivir.write({"state": "sent"})
        return res

    # ==================================================================
    # El precio del día de la cosecha
    # ==================================================================
    def _lista_vigente(self, fecha):
        """La lista de esa empacadora que rige para esa camaronera ese día.

        Se elige la más reciente cuya ventana de despacho cubra la fecha de la
        cosecha, usando el mismo `recibe_el()` que ya usa el comparador: si dos
        pantallas resolvieran "qué lista aplica" con criterios distintos, la
        liquidación diría una cosa y el comparador otra.
        """
        self.ensure_one()
        Lista = self.env["shrimp.price.list"].sudo()
        candidatas = Lista.search([
            ("issuer_partner_id", "=", self.packer_partner_id.id),
            ("state", "=", "published"),
            ("recipient_ids", "in", self.farmer_partner_id.shrimp_grupo_ids()),
        ], order="issue_date desc, id desc")
        for lista in candidatas:
            if lista.recibe_el(fecha):
                return lista
        return Lista.browse()

    def _precio_de_lista(self, lista, size_grade):
        """$/lb del renglón de esa talla, o None si la lista no la cotiza.

        La cola solo se toma por el canal directa —el sobrante es lo que queda
        de clasificar entero y no es lo que se reservó— y el entero se convierte
        de $/Kg a $/Lb. Las dos reglas son las mismas que ya aplica el lote al
        tomar su precio de una lista, y se repiten aquí y no se importan
        porque allí la unidad depende del propio lote y aquí siempre es libra.
        """
        if not lista or not size_grade:
            return None
        lineas = lista.line_ids.filtered(
            lambda l: l.size_grade_id == size_grade
            and (size_grade.presentation != "cola" or l.channel == "directa"))
        if not lineas:
            return None
        # La mejor calidad cotizada, igual que hace el lote: la reserva se
        # negocia sobre camarón sano, y castigar por omisión con el renglón
        # más barato sería una penalización que nadie pactó.
        linea = max(lineas, key=lambda l: l.price)
        return linea.price / LB_POR_KG if linea.uom == "kg" else linea.price

    def _precio_liquidado(self, size_grade, fecha):
        """(precio $/lb, escalones aplicados, se usó el piso, lista aplicada).

        Devuelve la tupla entera y no solo el número porque la pantalla tiene
        que poder explicar de dónde salió el precio. Un precio que aparece sin
        explicación en una liquidación es el principio de una discusión.
        """
        self.ensure_one()
        if self.price_mode == "fijo":
            pasos = _escalones_entre(
                self.env, self.forecast_id.size_grade_id, size_grade)
            if pasos is None:
                # No se sabe la distancia: se paga lo pactado sin ajustar. Es
                # el resultado conservador, y de todos modos una talla que no
                # está en la escalera ya sacó la cosecha de la banda, así que
                # esto pasa por la confirmación de las dos partes.
                pasos = 0
            precio = (self.price_per_lb or 0.0) - (self.step_delta_per_lb or 0.0) * pasos
            return max(precio, 0.0), pasos, False, self.env["shrimp.price.list"]

        lista = self._lista_vigente(fecha)
        de_lista = self._precio_de_lista(lista, size_grade)
        piso = self.price_floor_per_lb or 0.0
        if de_lista is None:
            # Sin lista que cubra ese día y esa talla, manda el piso. Es
            # exactamente para lo que existe: la camaronera pactó un peor caso
            # y no puede quedarse sin precio porque la otra parte no publicó.
            return piso, 0, True, lista
        return max(de_lista, piso), 0, de_lista < piso, lista

    # ==================================================================
    # Acciones
    # ==================================================================
    def action_accept(self, actor=None):
        """La camaronera acepta: nace el acuerdo y los demás se descartan.

        `actor` se comprueba siempre y no se delega en el ACL. El controlador
        trabaja en sudo tras comprobar la propiedad, así que apoyar esta regla
        en un permiso de otra tabla sería apoyarla en nada: una empacadora
        podría autoadjudicarse la cosecha.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        declaracion = self.forecast_id
        if actor != declaracion.farmer_partner_id:
            raise AccessError(_(
                "El compromiso lo acepta quien declaró la cosecha."))
        if self.state != "sent":
            raise ValidationError(_("Solo se acepta un compromiso vivo."))
        if not declaracion.admite_compromisos:
            raise ValidationError(_(
                "Esta declaración ya no admite compromisos."))
        if self.valid_until and self.valid_until < fields.Date.context_today(self):
            raise ValidationError(_(
                "Este compromiso venció el %s.")
                % fields.Date.to_string(self.valid_until))
        self.write({
            "state": "accepted",
            "accepted_at": fields.Datetime.now(),
            "accepted_by_uid": self.env.user.id,
        })
        # Una sola empacadora por cosecha. Partir una cosecha entre dos
        # compradores existe en la vida real, pero repartir la desviación de la
        # banda entre dos no tiene solución honesta con una sola banda, y
        # fingir que la tiene produciría liquidaciones que nadie acepta.
        (declaracion.commitment_ids - self).filtered(
            lambda c: c.state == "sent").write({"state": "rejected"})
        declaracion.state = "committed"
        self.message_post(body=_(
            "Compromiso aceptado: %(lb)s lb.")
            % {"lb": "{:,.2f}".format(self.committed_lb)})
        declaracion.message_post(body=_(
            "Cosecha comprometida con «%s».") % (self.packer_partner_id.name or ""))
        return True

    def action_reject(self, motivo=None, actor=None):
        """La camaronera descarta un compromiso que no le sirve."""
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.farmer_partner_id:
            raise AccessError(_("Lo descarta quien declaró la cosecha."))
        if self.state != "sent":
            raise ValidationError(_("Solo se descarta un compromiso vivo."))
        self.state = "rejected"
        if motivo:
            self.message_post(body=_("Descartado: %s") % motivo)
        return True

    def action_withdraw(self, actor=None):
        """La empacadora retira su compromiso ANTES de que se lo acepten.

        Retirar lo que nadie ha aceptado no es echarse atrás: nadie se apoyó en
        ello todavía. Por eso no deja marca de incumplimiento, y por eso se
        corta en seco si el compromiso ya está aceptado.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.packer_partner_id:
            raise AccessError(_(
                "Un compromiso lo retira la empacadora que lo hizo."))
        if self.state == "accepted":
            raise ValidationError(_(
                "Este compromiso ya fue aceptado: la camaronera está contando "
                "con él. Si no lo vas a cumplir, hay que desistir dejando el "
                "motivo, no retirarlo como si nunca hubiera existido."))
        if self.state != "sent":
            raise ValidationError(_("Solo se retira un compromiso vivo."))
        self.state = "withdrawn"
        self.message_post(body=_("Compromiso retirado por la empacadora."))
        return True

    def action_desistir(self, motivo=None, actor=None):
        """Echarse atrás de un compromiso ya aceptado. Con nombre y con motivo.

        Lo pueden hacer las dos partes, porque las dos se echan atrás en la
        vida real: la empacadora que se quedó sin cupo de exportación y la
        camaronera a la que otro le ofreció diez centavos más. Lo que no se
        puede es hacerlo en silencio.

        El motivo es obligatorio. No es burocracia: es lo único que distingue
        "se me murió la piscina por mancha blanca" de "encontré mejor precio",
        y esa distinción es la que la otra parte necesita para decidir si
        vuelve a comprometerse con esta.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor == self.farmer_partner_id:
            lado = "farmer"
        elif actor == self.packer_partner_id:
            lado = "packer"
        else:
            raise AccessError(_("De este compromiso solo desisten sus partes."))
        if self.state not in ("accepted", "to_confirm"):
            raise ValidationError(_(
                "Solo se desiste de un compromiso en pie."))
        motivo = (motivo or "").strip()
        if not motivo:
            raise ValidationError(_(
                "Para echarse atrás hay que decir por qué. Es lo que la otra "
                "parte va a leer la próxima vez que decida si se compromete "
                "contigo."))
        self._marcar_incumplido(
            lado=lado, partner=actor, motivo=motivo)
        return True

    def _marcar_incumplido(self, lado, partner, motivo):
        """Escribe la ruptura. Interno: lo llaman `action_desistir` y la
        cancelación de la declaración, que ya comprobaron quién y por qué."""
        self.ensure_one()
        self.write({
            "state": "broken",
            "break_side": lado,
            "broken_by_partner_id": partner.id,
            "broken_by_uid": self.env.user.id,
            "broken_at": fields.Datetime.now(),
            "break_reason": motivo,
        })
        # Las confirmaciones pendientes dejan de tener sentido: ya no hay
        # acuerdo que confirmar. Se archivan con su traza, no se borran.
        self.confirmation_ids.filtered(lambda c: c.active)._archivar(
            _("El compromiso se rompió antes de cerrar la confirmación."))
        if self.forecast_id.state == "committed":
            self.forecast_id.state = "published"
        self.message_post(body=_(
            "Compromiso incumplido por %(quien)s. Motivo: %(motivo)s")
            % {"quien": partner.name or "", "motivo": motivo})
        self.forecast_id.message_post(body=_(
            "«%(quien)s» se echó atrás del compromiso. Motivo: %(motivo)s")
            % {"quien": partner.name or "", "motivo": motivo})
        return True

    # ==================================================================
    # Liquidación
    # ==================================================================
    def _liquidar(self, diag):
        """Resuelve el compromiso contra la cosecha que realmente salió.

        Dentro de la banda se cumple solo: se liquidan las libras recortadas al
        techo —el excedente por encima queda libre para la camaronera, porque
        la empacadora nunca se obligó a tomarlo— al precio que resulte del modo
        pactado, y el compromiso queda `honored`.

        Fuera de la banda NO se declara incumplimiento de nadie. Se abre una
        confirmación de dos firmas: la cosecha que salió no es la que se
        vendió, y quien tiene que decir si aun así le sirve son las dos partes,
        no el sistema. Esa es la diferencia entre un compromiso que la gente
        usa y uno que solo genera peleas.
        """
        self.ensure_one()
        declaracion = self.forecast_id
        grade = declaracion.actual_size_grade_id
        fecha = declaracion.actual_date
        precio, pasos, piso, lista = self._precio_liquidado(grade, fecha)
        # Las libras que se liquidan nunca pasan de lo que esta empacadora se
        # comprometió a tomar: si declaró 40.000 y ella tomó 20.000, el
        # excedente de la cosecha no es suyo por mucho que la banda lo cubra.
        libras = min(diag["lb_banda"], self.committed_lb)
        self.write({
            "settled_lb": libras,
            "settled_price_per_lb": precio,
            "settled_steps": pasos,
            "floor_applied": piso,
            "price_list_id": lista.id if lista else False,
            "deviation_notes": "\n".join(diag["motivos"]) or False,
        })
        if diag["dentro"]:
            self.state = "honored"
            self.message_post(body=_(
                "Cosecha dentro de la banda. Se liquidan %(lb)s lb a "
                "%(precio)s $/lb.")
                % {"lb": "{:,.2f}".format(libras),
                   "precio": "{:,.4f}".format(precio)})
        else:
            self.state = "to_confirm"
            self._abrir_confirmacion(diag)
        return precio

    def _abrir_confirmacion(self, diag):
        """Dos filas, una por parte, con las cifras congeladas."""
        self.ensure_one()
        Conf = self.env["shrimp.harvest.confirmation"]
        ronda = max(self.confirmation_ids.mapped("ronda") or [0]) + 1
        base = {
            "commitment_id": self.id,
            "ronda": ronda,
            "frozen_expected_lb": self.forecast_id.expected_lb,
            "frozen_actual_lb": diag["actual_lb"],
            "frozen_lb_min": diag["lb_min"],
            "frozen_lb_max": diag["lb_max"],
            "frozen_settled_lb": self.settled_lb,
            "frozen_price_per_lb": self.settled_price_per_lb,
            "frozen_steps": diag["escalones"] if diag["escalones"] is not None else 0,
            "frozen_days": diag["dias_desvio"],
            "frozen_reason": "\n".join(diag["motivos"]) or False,
        }
        Conf.create([
            dict(base, role="farmer", partner_id=self.farmer_partner_id.id),
            dict(base, role="packer", partner_id=self.packer_partner_id.id),
        ])
        self.message_post(body=_(
            "La cosecha se salió de la banda. Hace falta que las dos partes "
            "confirmen:\n%s") % ("\n".join(diag["motivos"]) or ""))
        return True

    def _evaluar_confirmacion(self):
        """Lo llama cada firma. Dos conformes cierran; un no conforme libera."""
        self.ensure_one()
        vivas = self.confirmation_ids.filtered("active")
        if any(c.decision == "rejected" for c in vivas):
            self.state = "released"
            self.message_post(body=_(
                "Una de las partes no aceptó la cosecha que salió. El "
                "compromiso queda liberado SIN incumplimiento: lo que se "
                "cosechó no era lo que se vendió."))
            if self.forecast_id.state == "committed":
                self.forecast_id.state = "harvested"
            return False
        if vivas and all(c.decision == "accepted" for c in vivas):
            self.state = "honored"
            if self.product_id and self.product_id.state == "draft":
                self.product_id.sudo().write({
                    "state": "published",
                    "published_date": fields.Datetime.now(),
                    "price": self.settled_price_per_lb or 0.0,
                })
            self.message_post(body=_(
                "Las dos partes aceptaron la cosecha fuera de banda. Se "
                "liquidan %(lb)s lb a %(precio)s $/lb.")
                % {"lb": "{:,.2f}".format(self.settled_lb or 0.0),
                   "precio": "{:,.4f}".format(self.settled_price_per_lb or 0.0)})
            return True
        return False

    def action_comprar(self, actor=None, verifier=None, fee=None, mode=None):
        """La empacadora remata la compra del lote que nació de la reserva.

        No se dispara sola al cumplirse el compromiso, a propósito: comprar es
        un acto del comprador y dejarlo automático le quitaría a la empacadora
        el último control sobre su propia orden de compra. Lo que la reserva ya
        hizo es lo difícil —colocar el camarón y fijar el precio—; esto solo
        usa los caminos de compra de siempre, para no abrir una contabilidad
        paralela de ventas que después no cuadre con el marketplace.

        El camarón adulto exige verificación en campo, también cuando viene de
        una reserva: la reserva fija precio y libras, pero nadie vio todavía
        el producto. Por eso la compra arranca como compra VERIFICADA (con el
        verificador que elige la empacadora) y se cierra, como cualquier otra,
        cuando las dos partes firman el informe.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.packer_partner_id:
            raise AccessError(_("La compra la hace la empacadora comprometida."))
        if self.state != "honored":
            raise ValidationError(_(
                "Solo se compra sobre un compromiso cumplido."))
        if self.transaction_id and self.transaction_id.state != "cancel":
            raise ValidationError(_("Esta reserva ya se compró."))
        if not self.product_id:
            raise ValidationError(_("Esta reserva no llegó a generar lote."))
        # La empacadora compra COMO empacadora aunque en ese momento su cuenta
        # (empacadora + camaronera, por ejemplo) navegue con otro perfil.
        producto = self.product_id.sudo().with_context(shrimp_buyer_role="empacadora")
        # Modo de verificación (plataforma o declarada por las partes). Sin
        # modo explícito se deduce del verificador elegido, como antes.
        mode = mode or ("platform" if verifier else None)
        if producto._shrimp_requires_verification() or mode:
            if mode == "declared":
                resultado = producto.start_verified_purchase(
                    self.packer_partner_id, self.settled_lb, None, mode="declared")
                nota = _("Compra iniciada sobre el lote reservado, con verificación "
                         "declarada por las partes.")
                self.transaction_id = resultado["transaction"].id
                self.message_post(body=nota)
                return resultado["transaction"]
            if not verifier:
                raise ValidationError(_(
                    "El camarón adulto se compra con verificación: elige un "
                    "verificador acreditado de la plataforma o la verificación "
                    "declarada por las partes para cerrar la reserva."))
            if fee is None:
                fee = self.env["shrimp.verification.fee"].sudo().compute(self.settled_lb)
            resultado = producto.start_verified_purchase(
                self.packer_partner_id, self.settled_lb, verifier, fee=fee)
            nota = _("Compra iniciada sobre el lote reservado, con verificación en "
                     "campo de «%s».") % (verifier.name or "")
        else:
            resultado = producto.execute_purchase_flow(
                self.packer_partner_id, self.settled_lb)
            nota = _("Compra ejecutada sobre el lote reservado.")
        self.transaction_id = resultado["transaction"].id
        self.message_post(body=nota)
        return resultado["transaction"]

    @api.model
    def _cron_vencer(self):
        """Compromisos enviados que nadie contestó a tiempo."""
        hoy = fields.Date.context_today(self)
        vencidos = self.search([
            ("state", "=", "sent"),
            ("valid_until", "<", hoy),
        ])
        vencidos.write({"state": "lapsed"})
        return True
