"""Seguimiento del despacho: de la piscina a la balanza de la planta.

POR QUÉ EXISTE
--------------
Hoy, confirmada la compra, nadie le dice al técnico verificador CUÁNDO tiene
que estar en la planta. Se le avisa cuando se le asigna el trabajo, que puede
ser días antes, y lo único parecido a una fecha que guarda el sistema son
``harvest_date`` y ``process_date`` de la verificación: dos fechas sin hora que
además escribe el propio técnico DESPUÉS de haber ido. Si el técnico no está
cuando llega el camarón, no hay verificación; y sin verificación no hay
producto. Esto es lo que arregla: una cita con hora y minuto, puesta por quien
sabe cuándo sale el camión —el vendedor—, y avisada a quien tiene que estar.

POR QUÉ EN MODELO PROPIO Y NO EN CAMPOS DE shrimp.transaction
-------------------------------------------------------------
Por el reparto de escritura. El cliente fue explícito: la llegada real la
estampa el técnico, y ni el vendedor ni la empacadora deben poder ponerla,
porque es la hora que después nadie puede discutir. ``shrimp.transaction`` le
da al portal escritura sobre el registro entero a comprador y vendedor
(``access_shrimp_transaction_portal`` y su ``ir.rule`` sin ``perm_*``), y una
regla de registro no distingue campos: si la llegada real viviera ahí, el
vendedor tendría permiso ORM para escribirla y lo único que lo frenaría sería
que el controlador no se lo ofrezca. Aquí, en cambio, el portal no escribe
nada: lee, y todo cambio entra por ``registrar_plan`` o ``registrar_llegada``,
que comprueban quién es cada uno.

Va además con su propio chatter porque el dato que de verdad hace daño no es
la cita sino el CAMBIO de cita: un atraso de dos horas sin avisar es lo que
hace que el técnico se vaya. Cada cambio queda fechado y firmado.

POR QUÉ EN shrimp_verification Y NO EN shrimp_marketplace
----------------------------------------------------------
El dato es de la compra, que vive en ``shrimp_marketplace``, pero los avisos
van a la empresa verificadora y a su técnico, que viven en
``shrimp_verification``. Como ``shrimp_verification`` ya depende de
``shrimp_marketplace``, ponerlo aquí no necesita ninguna dependencia nueva;
ponerlo allí obligaría a que el marketplace supiera qué es un verificador, que
es exactamente la dependencia al revés.
"""

import logging

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class ShrimpDispatch(models.Model):
    _name = "shrimp.dispatch"
    _description = "Seguimiento del despacho hasta la planta"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin",
                "shrimp.notify.mixin"]
    _order = "eta asc, id desc"

    # Cuánto se le perdona a un camión antes de contarlo como atrasado.
    # Media hora es lo que un técnico espera de pie sin irse a otro trabajo;
    # más que eso y la cita dejó de servir, que es justo lo que se mide.
    TOLERANCIA_MIN = 30

    # ------------------------------------------------------------------
    # A qué compra pertenece
    # ------------------------------------------------------------------
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Compra", required=True,
        ondelete="cascade", index=True,
    )
    name = fields.Char(
        string="Referencia", compute="_compute_name", store=True, readonly=True)

    seller_partner_id = fields.Many2one(
        "res.partner", string="Vendedor",
        related="transaction_id.seller_partner_id", store=True, index=True,
    )
    buyer_partner_id = fields.Many2one(
        "res.partner", string="Comprador (empacadora)",
        related="transaction_id.buyer_partner_id", store=True, index=True,
    )
    product_id = fields.Many2one(
        "shrimp.product", string="Producto",
        related="transaction_id.product_id", store=True,
    )

    # La verificación es un One2many en la compra; aquí interesa la única que
    # puede haber, y almacenada, para poder relacionar el verificador y el
    # técnico sin recorrer la colección en cada pantalla.
    verification_id = fields.Many2one(
        "shrimp.verification", string="Verificación",
        compute="_compute_verification", store=True, readonly=True,
    )
    verifier_partner_id = fields.Many2one(
        "res.partner", string="Empresa verificadora",
        related="verification_id.verifier_partner_id", store=True, index=True,
    )
    technician_partner_id = fields.Many2one(
        "res.partner", string="Técnico asignado",
        related="verification_id.technician_partner_id", store=True, index=True,
    )

    # ------------------------------------------------------------------
    # Lo que declara el VENDEDOR
    # ------------------------------------------------------------------
    harvest_date = fields.Date(
        string="Fecha de pesca", tracking=True,
        help="El día que se cosechó la piscina.")
    farm_departure = fields.Datetime(
        string="Salida de la finca", tracking=True,
        help="Fecha y hora a la que el camión salió de la camaronera.")
    eta = fields.Datetime(
        string="Llegada estimada a planta", tracking=True, index=True,
        help="LA CITA. Es la hora a la que el técnico verificador tiene que "
             "estar en la planta. Si cambia, hay que volver a fijarla: se "
             "avisa automáticamente a la empacadora, a la verificadora y al "
             "técnico.")

    carrier_name = fields.Char(string="Transportista", tracking=True)
    vehicle_plate = fields.Char(string="Placa del vehículo", tracking=True)
    carrier_phone = fields.Char(
        string="Teléfono del transportista",
        help="Para poder llamar si el camión se atrasa.")
    notes = fields.Text(string="Observaciones del vendedor")

    # ------------------------------------------------------------------
    # Lo que estampa el TÉCNICO
    # ------------------------------------------------------------------
    actual_arrival = fields.Datetime(
        string="Llegada real a planta", readonly=True, copy=False, tracking=True,
        help="La estampa el técnico verificador, que ya está en la planta "
             "esperando. Es un tercero: la hora que anote no la puede "
             "discutir ni el vendedor ni la empacadora.")
    arrival_registered_by_id = fields.Many2one(
        "res.partner", string="Llegada registrada por", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Historia de la cita: cuántas veces se movió
    # ------------------------------------------------------------------
    # El chatter ya guarda cada cambio, pero para poder mirarlo en una lista
    # hace falta tenerlo en columnas. La primera cita es la que el técnico
    # apuntó en su agenda; contra esa se juzga el desplazamiento.
    eta_first = fields.Datetime(
        string="Primera cita fijada", readonly=True, copy=False)
    eta_changes = fields.Integer(
        string="Veces que se movió la cita", readonly=True, default=0, copy=False)

    # ------------------------------------------------------------------
    # Derivados
    # ------------------------------------------------------------------
    delay_minutes = fields.Float(
        string="Desfase (min)", compute="_compute_puntualidad", store=True,
        digits=(16, 1),
        help="Minutos entre la llegada estimada y la real. Positivo = llegó "
             "tarde; negativo = llegó antes de la cita.")
    on_time = fields.Boolean(
        string="Llegó a tiempo", compute="_compute_puntualidad", store=True)

    state = fields.Selection(
        [
            ("sin_cita", "Sin cita"),
            ("programado", "Cita fijada"),
            ("en_ruta", "En camino"),
            ("llegado", "Llegó a planta"),
        ],
        string="Situación", compute="_compute_state", store=True, index=True,
    )

    # ==================================================================
    # Cálculos
    # ==================================================================
    @api.depends("transaction_id.name")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Despacho de %s") % (rec.transaction_id.name or "—")

    @api.depends("transaction_id.verification_id")
    def _compute_verification(self):
        for rec in self:
            rec.verification_id = rec.transaction_id.verification_id[:1]

    @api.depends("eta", "actual_arrival")
    def _compute_puntualidad(self):
        for rec in self:
            if rec.eta and rec.actual_arrival:
                rec.delay_minutes = (
                    rec.actual_arrival - rec.eta).total_seconds() / 60.0
                rec.on_time = abs(rec.delay_minutes) <= rec.TOLERANCIA_MIN
            else:
                rec.delay_minutes = 0.0
                rec.on_time = False

    @api.depends("eta", "farm_departure", "actual_arrival")
    def _compute_state(self):
        for rec in self:
            if rec.actual_arrival:
                rec.state = "llegado"
            elif rec.farm_departure:
                rec.state = "en_ruta"
            elif rec.eta:
                rec.state = "programado"
            else:
                rec.state = "sin_cita"

    # ==================================================================
    # Restricciones
    # ==================================================================
    # models.Constraint y no _sql_constraints: en Odoo 19 el atributo antiguo
    # se ignora EN SILENCIO y la restricción nunca llega a la base de datos.
    _uniq_dispatch_per_tx = models.Constraint(
        "unique(transaction_id)",
        "Esa compra ya tiene un seguimiento de despacho.",
    )

    @api.constrains("harvest_date", "farm_departure", "eta", "actual_arrival")
    def _check_cronologia(self):
        """Las cuatro marcas tienen que poder contarse como un viaje.

        No es quisquillosería: una cita anterior a la salida del camión es una
        cita que el técnico no puede cumplir, y aquí el error se paga con un
        viaje perdido, no con un dato feo.
        """
        for rec in self:
            if rec.farm_departure and rec.harvest_date:
                # En la zona del usuario, no en UTC: la fecha de pesca es un
                # día del calendario de la finca, y comparar contra la fecha
                # UTC del Datetime inventa un desfase de un día en cuanto la
                # salida cae cerca de medianoche.
                salida = fields.Datetime.context_timestamp(
                    rec, rec.farm_departure).date()
                if salida < rec.harvest_date:
                    raise ValidationError(_(
                        "El camión no puede salir de la finca antes de la "
                        "fecha de pesca."))
            if rec.eta and rec.farm_departure and rec.eta <= rec.farm_departure:
                raise ValidationError(_(
                    "La llegada estimada a la planta tiene que ser posterior a "
                    "la salida de la finca."))
            if rec.actual_arrival:
                if rec.farm_departure and rec.actual_arrival < rec.farm_departure:
                    raise ValidationError(_(
                        "La llegada a la planta no puede ser anterior a la "
                        "salida de la finca."))
                if rec.actual_arrival > fields.Datetime.now():
                    raise ValidationError(_(
                        "La llegada a la planta no puede estar en el futuro: "
                        "se registra cuando el camarón ya está en la balanza."))

    # ==================================================================
    # Quién puede hacer qué
    # ==================================================================
    def _es_vendedor(self, partner):
        self.ensure_one()
        return bool(partner) and partner == self.seller_partner_id

    def _puede_estampar_llegada(self, partner):
        """El técnico asignado o, si no lo hay todavía, el administrador de la
        empresa verificadora.

        Es el mismo corte que ya aplica ``action_start_field``: la empresa es
        la acreditada y a veces el que pisa el campo es el propio dueño. Lo que
        NO entra por aquí, y ese era el punto del cliente, es el vendedor ni la
        empacadora.
        """
        self.ensure_one()
        if not partner:
            return False
        if self.technician_partner_id and partner == self.technician_partner_id:
            return True
        return bool(self.verifier_partner_id) and partner == self.verifier_partner_id

    def _bloqueado(self):
        """¿Se acabó el tiempo de editar el plan?

        Una vez el camarón está en la balanza, mover la cita ya no informa a
        nadie: solo maquilla el retraso con el que se llegó.
        """
        self.ensure_one()
        return bool(self.actual_arrival)

    # ==================================================================
    # Escritura del plan (VENDEDOR)
    # ==================================================================
    CAMPOS_PLAN = ("harvest_date", "farm_departure", "eta", "carrier_name",
                   "vehicle_plate", "carrier_phone", "notes")

    def registrar_plan(self, partner, vals):
        """Única puerta por la que el vendedor llena o corrige el despacho.

        Devuelve True si la cita se fijó o se movió (y por tanto se avisó).
        """
        self.ensure_one()
        if not self._es_vendedor(partner):
            raise UserError(_(
                "Solo el vendedor puede registrar el despacho de esta compra."))
        if self._bloqueado():
            raise UserError(_(
                "El camarón ya llegó a la planta: el despacho queda cerrado."))

        limpios = {k: v for k, v in vals.items() if k in self.CAMPOS_PLAN}
        eta_previa = self.eta
        nueva_eta = limpios.get("eta", eta_previa)

        if eta_previa and not nueva_eta:
            # Borrar la cita en vez de moverla es la forma silenciosa de
            # dejar al técnico plantado: no hay cambio del que avisar y el
            # aviso anterior sigue en pie diciendo una hora que ya no vale.
            raise UserError(_(
                "No se puede dejar la llegada estimada en blanco. Si el "
                "despacho se atrasa, pon la hora nueva: así se avisa a todos. "
                "Si se cayó el despacho, hay que cancelar la compra."))

        if nueva_eta and nueva_eta != eta_previa:
            limpios["eta_changes"] = self.eta_changes + (1 if eta_previa else 0)
            if not self.eta_first:
                limpios["eta_first"] = nueva_eta

        self.sudo().write(limpios)

        if nueva_eta and nueva_eta != eta_previa:
            self.sudo()._notify_eta(eta_previa)
            return True
        return False

    # ==================================================================
    # Llegada real (TÉCNICO VERIFICADOR)
    # ==================================================================
    def registrar_llegada(self, partner, cuando=None):
        """El técnico estampa la hora a la que el camarón entró a la planta."""
        self.ensure_one()
        if not self._puede_estampar_llegada(partner):
            raise UserError(_(
                "La llegada real la registra el técnico verificador. Ni el "
                "vendedor ni la empacadora pueden ponerla."))
        if self.actual_arrival:
            raise UserError(_("La llegada de este despacho ya está registrada."))

        # Se permite estampar la llegada aunque el vendedor nunca declarara la
        # cita. Bloquearlo sería castigar al técnico por un descuido del
        # vendedor, y además esta marca es lo que congela el registro: una vez
        # puesta, el vendedor ya no puede inventarse una hora estimada que le
        # deje bien. Ese despacho simplemente no cuenta para la puntualidad,
        # porque no hubo promesa contra la que medirlo.
        self.sudo().write({
            "actual_arrival": cuando or fields.Datetime.now(),
            "arrival_registered_by_id": partner.id,
        })
        # La cita ya se cumplió: la actividad en la agenda del técnico sobra.
        self.sudo().activity_unlink(["mail.mail_activity_data_todo"])
        self.sudo()._notify_arrival()
        return True

    # ==================================================================
    # Avisos
    # ==================================================================
    def _dispatch_recipients(self):
        """[(socio, url_de_su_portal)] de quien tiene que enterarse de la cita.

        Los tres que el cliente nombró: la empacadora que compró, la empresa
        verificadora y el técnico asignado. El vendedor NO está: es quien puso
        la cita, avisarle de su propio cambio es ruido y el ruido es lo que
        hace que se dejen de leer estos correos.

        Cada uno entra por una puerta distinta, así que el botón del correo no
        puede ser el mismo para todos.
        """
        self.ensure_one()
        base = self.get_base_url()
        ruta_verificador = (
            "/verificador/verificacion/%s" % self.verification_id.uuid_ref
            if self.verification_id else "/verificador/bandeja")
        mapa = [
            (self.buyer_partner_id, "/marketplace/despacho/%s" % self.transaction_id.uuid_ref),
            (self.verifier_partner_id, ruta_verificador),
            (self.technician_partner_id, ruta_verificador),
        ]
        salida, vistos = [], set()
        for partner, ruta in mapa:
            if not partner or not partner.email or partner.id in vistos:
                continue
            vistos.add(partner.id)
            salida.append((partner, base + ruta))
        return salida

    def _notify_eta(self, eta_previa=False, solo=None):
        """Avisa de la cita nueva, o del cambio de cita.

        Es el aviso que justifica todo el modelo: un atraso de dos horas que no
        se comunica es exactamente lo que hace que el técnico se vaya y se
        pierda la verificación.

        ``solo`` acota los destinatarios. Se usa cuando se asigna un técnico a
        una orden que YA tenía cita: él no estaba cuando se avisó y la cita es
        suya, pero volver a escribirle a la empacadora y a la verificadora un
        correo que ya recibieron es el camino más corto a que dejen de leerlos.
        """
        self.ensure_one()
        cambio = bool(eta_previa)
        destinatarios = [
            (partner, url) for partner, url in self._dispatch_recipients()
            if solo is None or partner in solo
        ]
        entregados = [
            (partner, self._send_template(
                "shrimp_verification.mail_template_dispatch_eta",
                partner.email,
                ctx={
                    "portal_url": url,
                    "destinatario": partner.name,
                    "es_cambio": cambio,
                    "eta_previa": self.fmt_dt_valor(eta_previa) if cambio else "",
                },
            ))
            for partner, url in destinatarios
        ]
        self._log_notificacion(entregados)
        self._reprogramar_actividad()

    def _reprogramar_actividad(self):
        """Deja (o corrige) la cita en la agenda del técnico.

        El correo se lee una vez; la actividad es lo que le queda al técnico en
        su bandeja el día que tiene que ir.

        Siempre se borra la anterior antes de poner la nueva, sin mirar si esto
        es un cambio de hora o una reasignación: dos actividades con dos horas
        distintas —o una en la agenda del administrador y otra en la del
        técnico— son peor que ninguna.
        """
        self.ensure_one()
        tecnico = self.technician_partner_id or self.verifier_partner_id
        if not tecnico or not self.eta:
            return
        usuario = self.env["res.users"].sudo().search(
            [("partner_id", "=", tecnico.id)], limit=1)
        if not usuario:
            return
        try:
            self.activity_unlink(["mail.mail_activity_data_todo"])
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                # La fecha del recordatorio, en la zona del técnico. Con la
                # fecha UTC, una cita de las ocho de la noche en Ecuador se le
                # programaría para el día siguiente.
                date_deadline=fields.Datetime.context_timestamp(self, self.eta).date(),
                user_id=usuario.id,
                summary=_("Cita en planta: %s") % (self.product_id.display_name or ""),
                note=_(
                    "Llegada estimada %(eta)s. Compra %(compra)s, vendedor "
                    "%(vendedor)s. Transporte: %(transporte)s."
                ) % {
                    "eta": self.fmt_dt("eta"),
                    "compra": self.transaction_id.name or "—",
                    "vendedor": self.seller_partner_id.name or "—",
                    "transporte": self.transporte_texto(),
                },
            )
        except Exception:
            # Un fallo de agenda no puede tumbar el registro del despacho.
            _logger.warning("No se pudo programar la actividad del despacho %s",
                            self.id, exc_info=True)

    def _notify_arrival(self):
        """La llegada se le avisa al VENDEDOR y a la EMPACADORA.

        Decisión propia, no la pidió el cliente: es el mismo dato que ahora se
        pregunta por WhatsApp («¿ya llegó?»), y como lo estampa un tercero vale
        como acuse. La verificadora y el técnico no entran porque son quienes
        lo acaban de registrar.
        """
        self.ensure_one()
        base = self.get_base_url()
        url = base + "/marketplace/despacho/%s" % self.transaction_id.uuid_ref
        entregados = []
        for partner in (self.seller_partner_id, self.buyer_partner_id):
            if not partner or not partner.email:
                continue
            entregados.append((partner, self._send_template(
                "shrimp_verification.mail_template_dispatch_arrival",
                partner.email,
                ctx={"portal_url": url, "destinatario": partner.name},
            )))
        self._log_notificacion(entregados)

    # ==================================================================
    # Presentación
    # ==================================================================
    # Todo el formateo se hace aquí y no en QWeb a propósito. Uno: en Odoo 19
    # el contexto de una página de website se pasa por formato de cadena, así
    # que un '%' suelto dentro de un t-esc revienta el render con "incomplete
    # format". Dos: estos campos son Datetime, o sea UTC en base de datos, y
    # convertirlos a la zona del usuario en la plantilla es la forma más
    # segura de que la cita salga con una hora que no es.
    def _tz(self):
        return pytz.timezone(self.env.user.tz or "UTC")

    @api.model
    def fmt_dt_valor(self, valor):
        """Un Datetime UTC en la zona del usuario, como 'dd/mm/aaaa HH:MM'."""
        if not valor:
            return "—"
        local = pytz.UTC.localize(fields.Datetime.to_datetime(valor)).astimezone(self._tz())
        return local.strftime("%d/%m/%Y %H:%M")

    def fmt_dt(self, campo):
        self.ensure_one()
        return self.fmt_dt_valor(self[campo])

    def fmt_hora(self, campo):
        """Solo la hora y el minuto: para la tarjeta de la bandeja."""
        self.ensure_one()
        valor = self[campo]
        if not valor:
            return "—"
        local = pytz.UTC.localize(fields.Datetime.to_datetime(valor)).astimezone(self._tz())
        return local.strftime("%d/%m %H:%M")

    def fmt_fecha(self, campo):
        self.ensure_one()
        valor = self[campo]
        return valor.strftime("%d/%m/%Y") if valor else "—"

    def fmt_input(self, campo):
        """Valor para un <input type="datetime-local">, en hora local."""
        self.ensure_one()
        valor = self[campo]
        if not valor:
            return ""
        local = pytz.UTC.localize(fields.Datetime.to_datetime(valor)).astimezone(self._tz())
        return local.strftime("%Y-%m-%dT%H:%M")

    def fmt_input_fecha(self, campo):
        self.ensure_one()
        valor = self[campo]
        return valor.strftime("%Y-%m-%d") if valor else ""

    @api.model
    def desde_local(self, texto):
        """Convierte lo que escribió el usuario en el navegador a UTC.

        Un <input type="datetime-local"> manda '2026-09-29T14:30' SIN zona. Si
        se guarda tal cual, Odoo lo toma por UTC y la cita se corre las horas
        que separen al usuario de Greenwich: en Ecuador, cinco. Cinco horas de
        error en el único dato que sirve para citar a alguien.
        """
        texto = (texto or "").strip().replace("T", " ")
        if not texto:
            return False
        if len(texto) == 16:
            texto += ":00"
        try:
            ingenuo = fields.Datetime.to_datetime(texto)
        except (ValueError, TypeError):
            return False
        if not ingenuo:
            return False
        tz = pytz.timezone(self.env.user.tz or "UTC")
        return tz.localize(ingenuo).astimezone(pytz.UTC).replace(tzinfo=None)

    def transporte_texto(self):
        """'Transportes Vera · placa GBA-1234', o lo que haya."""
        self.ensure_one()
        partes = [p for p in (self.carrier_name, self.vehicle_plate) if p]
        if not partes:
            return _("sin transportista declarado")
        if self.carrier_name and self.vehicle_plate:
            return _("%(quien)s · placa %(placa)s") % {
                "quien": self.carrier_name, "placa": self.vehicle_plate}
        return partes[0]

    def puntualidad_texto(self):
        """El desfase en castellano, para la pantalla y el correo."""
        self.ensure_one()
        if not self.actual_arrival:
            return ""
        if not self.eta:
            return _("no había hora estimada declarada")
        minutos = int(round(self.delay_minutes))
        if abs(minutos) <= self.TOLERANCIA_MIN:
            return _("llegó a la hora")
        if minutos > 0:
            return _("llegó %s tarde") % self._duracion(minutos)
        return _("llegó %s antes de la cita") % self._duracion(-minutos)

    @api.model
    def _duracion(self, minutos):
        """90 -> '1 h 30 min'. Sin formato '%' y sin decimales inventados."""
        minutos = int(abs(minutos))
        horas, resto = divmod(minutos, 60)
        if horas and resto:
            return _("%(h)s h %(m)s min") % {"h": horas, "m": resto}
        if horas:
            return _("%s h") % horas
        return _("%s min") % resto

    def falta_para_la_cita(self):
        """'faltan 2 h 10 min' / 'la cita era hace 40 min' / ''.

        Es lo que el técnico mira en su bandeja para decidir si sale ya.
        """
        self.ensure_one()
        if not self.eta or self.actual_arrival:
            return ""
        delta = (self.eta - fields.Datetime.now()).total_seconds() / 60.0
        if delta >= 0:
            return _("faltan %s") % self._duracion(delta)
        return _("la cita era hace %s") % self._duracion(delta)
