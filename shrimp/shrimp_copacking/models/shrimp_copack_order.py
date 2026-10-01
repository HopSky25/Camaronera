from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class ShrimpCopackOrder(models.Model):
    """El trabajo de empaque, desde que se adjudica hasta que se cuadra.

    Aqui no hay rendimiento que conciliar: el camaron llega listo para empacar,
    sin cabeza ni basura. Lo que hay que cuadrar es mas simple y mas exigente:
    entraron X libras y tienen que salir X libras empacadas. La diferencia, si
    la hay, se declara y la firman los dos.

    La propiedad del camaron NUNCA cambia de manos. Esto no es una compraventa
    y por eso no cuelga de shrimp.transaction: es un servicio que se cobra por
    libra empacada.
    """

    _name = "shrimp.copack.order"
    _description = "Orden de servicio de empaque"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Referencia", required=True, copy=False, readonly=True,
        default=lambda self: _("Nueva"))

    request_id = fields.Many2one(
        "shrimp.copack.request", string="Solicitud", ondelete="set null", index=True)
    offer_id = fields.Many2one(
        "shrimp.copack.offer", string="Oferta aceptada", ondelete="set null")

    client_partner_id = fields.Many2one(
        "res.partner", string="Cliente (dueño del camarón)", required=True,
        ondelete="restrict", index=True, tracking=True)
    copacker_partner_id = fields.Many2one(
        "res.partner", string="Maquilador", required=True,
        ondelete="restrict", index=True, tracking=True)
    product_id = fields.Many2one(
        "shrimp.product", string="Lote", ondelete="set null", index=True,
        help="Enlaza el empaque con la trazabilidad del lote.")

    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)
    agreed_qty_lb = fields.Float(
        string="Libras acordadas", required=True, digits=(16, 2), tracking=True)
    # Margen de lo real sobre lo acordado. Ver _check_libras_vs_acordado: una
    # cosecha no sale nunca clavada a lo que se pacto, pero tampoco al doble.
    agreed_overrun_pct = fields.Float(
        string="Margen sobre lo acordado (%)", default=10.0, digits=(5, 2),
        tracking=True,
        help="Cuánto pueden superar las libras reales a las acordadas. "
             "Por encima de esto no se acepta el registro: o es un error de "
             "captura o es un trabajo distinto al que se adjudicó.")
    rate_per_lb = fields.Monetary(
        string="Tarifa por libra", required=True, currency_field="currency_id")

    supplies_notes = fields.Text(string="Insumos que lleva el cliente")
    supplies_received = fields.Boolean(
        string="Insumos recibidos completos", tracking=True,
        help="Si llegan incompletos, la línea se para y la culpa es del cliente. "
             "Dejarlo marcado aquí evita esa discusión después.")
    supplies_issue = fields.Text(string="Qué faltó de los insumos")

    # --- recepcion ---
    received_lb = fields.Float(string="Libras recibidas", digits=(16, 2), tracking=True)
    received_date = fields.Datetime(string="Fecha de recepción", readonly=True, copy=False)

    # --- entrega ---
    packed_lb = fields.Float(string="Libras empacadas", digits=(16, 2), tracking=True)
    packed_date = fields.Datetime(string="Fecha de entrega", readonly=True, copy=False)
    boxes = fields.Integer(string="Cajas / masters")
    packed_presentation = fields.Char(string="Presentación empacada")

    # --- el cuadre ---
    difference_lb = fields.Float(
        string="Diferencia (lb)", compute="_compute_cuadre", store=True, digits=(16, 2),
        help="Recibidas menos empacadas. Positivo significa que faltaron libras.")
    difference_pct = fields.Float(
        string="Diferencia (%)", compute="_compute_cuadre", store=True, digits=(5, 2))
    cuadra = fields.Boolean(
        string="Cuadra", compute="_compute_cuadre", store=True,
        help="Verdadero cuando la diferencia está dentro de la tolerancia pactada.")
    tolerance_pct = fields.Float(
        string="Tolerancia (%)", default=0.5, digits=(5, 2),
        help="Merma de manipulación que las partes dan por normal.")

    # --- dinero ---
    service_amount = fields.Monetary(
        string="A cobrar por el empaque", compute="_compute_importes", store=True,
        currency_field="currency_id",
        help="Tarifa por las libras efectivamente empacadas.")
    platform_rate_per_lb = fields.Monetary(
        string="Comisión CamaronMarket por libra", currency_field="currency_id",
        default=0.01,
        help="Lo que la plataforma cobra al maquilador por libra empacada.")
    platform_amount = fields.Monetary(
        string="Comisión CamaronMarket", compute="_compute_importes", store=True,
        currency_field="currency_id")

    # Un solo sitio donde se decide si una orden se puede cobrar y si puede
    # salir en el certificado. Antes cada pantalla repetia
    # state in ('packed','signed','closed'), y ese criterio dejaba dentro las
    # ordenes en disputa: se facturaba un empaque que una de las partes habia
    # declarado por escrito que no aceptaba.
    #
    # Es `store=True` a proposito: asi el campo se puede usar en un dominio de
    # busqueda normal. Un booleano calculado sin almacenar obligaria a escribir
    # un `_search`, y en Odoo 19 ese `_search` recibe operator='in' con un
    # conjunto (no un '=' con True/False); asumir lo contrario invierte el
    # filtro en silencio.
    es_facturable = fields.Boolean(
        string="Facturable", compute="_compute_es_facturable", store=True, index=True,
        help="El empaque está hecho, nadie lo ha rechazado y hay libras que cobrar.")

    state = fields.Selection(
        [
            ("confirmed", "Adjudicada"),
            ("received", "Recibida en planta"),
            ("packed", "Empacada"),
            ("signed", "Acta firmada"),
            ("closed", "Cerrada"),
            ("cancelled", "Cancelada"),
        ],
        string="Estado", default="confirmed", required=True, index=True, tracking=True)

    acceptance_ids = fields.One2many(
        "shrimp.copack.acceptance", "order_id", string="Firmas del acta")
    acceptance_state = fields.Selection(
        [
            ("na", "Sin abrir"),
            ("open", "A la firma"),
            ("closed", "Firmada por los dos"),
            ("disputed", "En disputa"),
        ],
        string="Acta", default="na", required=True, readonly=True, index=True)

    @api.depends("received_lb", "packed_lb", "tolerance_pct")
    def _compute_cuadre(self):
        for rec in self:
            dif = (rec.received_lb or 0.0) - (rec.packed_lb or 0.0)
            rec.difference_lb = dif
            rec.difference_pct = (100.0 * dif / rec.received_lb) if rec.received_lb else 0.0
            rec.cuadra = bool(rec.received_lb) and abs(rec.difference_pct) <= rec.tolerance_pct

    @api.depends("packed_lb", "rate_per_lb", "platform_rate_per_lb")
    def _compute_importes(self):
        for rec in self:
            rec.service_amount = (rec.packed_lb or 0.0) * rec.rate_per_lb
            rec.platform_amount = (rec.packed_lb or 0.0) * rec.platform_rate_per_lb

    @api.depends("state", "acceptance_state", "packed_lb")
    def _compute_es_facturable(self):
        for rec in self:
            rec.es_facturable = (
                rec.state in ("packed", "signed", "closed")
                and rec.acceptance_state != "disputed"
                # Sin libras empacadas no hay nada que cobrar; cobrar cero es
                # ruido en la liquidacion, no un cobro.
                and (rec.packed_lb or 0.0) > 0
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nueva")) == _("Nueva"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.copack.order") or _("Nueva")
        return super().create(vals_list)

    @api.constrains("client_partner_id", "copacker_partner_id")
    def _check_partes(self):
        for rec in self:
            if rec.client_partner_id.shrimp_user_type not in ("camaronera", "empacadora"):
                raise ValidationError(_(
                    "El cliente del empaque es quien es dueño del camarón: una "
                    "camaronera o una empacadora."))
            if rec.copacker_partner_id.shrimp_user_type != "maquilador":
                raise ValidationError(_("Quien empaca tiene que ser un maquilador."))
            if rec.client_partner_id == rec.copacker_partner_id:
                raise ValidationError(_("Nadie se contrata a sí mismo."))

    @api.constrains("product_id", "client_partner_id")
    def _check_lote_del_cliente(self):
        """El lote empacado tiene que ser del cliente que contrata el empaque.

        Apuntar la orden al lote de otro inyecta un paso de empaque falso en el
        certificado de trazabilidad de ese tercero: su comprador leeria que su
        camaron paso por una planta por la que nunca paso. Es el unico error de
        este modulo que ensucia datos ajenos.

        La lectura va en sudo para poder dar este mensaje: sin ella, apuntar a
        un lote ajeno revienta antes con un AccessError que no explica nada.
        """
        for rec in self:
            if not rec.product_id:
                continue
            dueno = rec.product_id.sudo().seller_partner_id
            if dueno != rec.client_partner_id:
                raise ValidationError(_(
                    "El lote «%(lote)s» es de «%(dueno)s», no de «%(cliente)s». "
                    "Solo se puede mandar a empacar camarón propio.")
                    % {"lote": rec.product_id.display_name or "",
                       "dueno": dueno.name or _("otro titular"),
                       "cliente": rec.client_partner_id.name or ""})

    @api.constrains("agreed_qty_lb", "rate_per_lb", "received_lb", "packed_lb",
                    "tolerance_pct", "platform_rate_per_lb", "agreed_overrun_pct",
                    "boxes")
    def _check_numeros(self):
        for rec in self:
            if rec.agreed_qty_lb <= 0:
                raise ValidationError(_("Las libras acordadas deben ser mayores que cero."))
            for campo, etiqueta in (
                    ("rate_per_lb", _("La tarifa por libra")),
                    ("received_lb", _("Las libras recibidas")),
                    ("packed_lb", _("Las libras empacadas")),
                    ("tolerance_pct", _("La tolerancia")),
                    ("agreed_overrun_pct", _("El margen sobre lo acordado")),
                    ("platform_rate_per_lb", _("La comisión por libra"))):
                if (rec[campo] or 0.0) < 0:
                    raise ValidationError(_("%s no puede ser negativa.") % etiqueta)
            # Las cajas no se validaban: una orden podia declarar -40 masters,
            # que ademas de imposible descuadra cualquier conteo posterior.
            if (rec.boxes or 0) < 0:
                raise ValidationError(_("Las cajas no pueden ser negativas."))
            if rec.packed_lb and rec.received_lb and rec.packed_lb > rec.received_lb:
                raise ValidationError(_(
                    "No se pueden entregar más libras de las que entraron: "
                    "recibidas %(r).2f, empacadas %(e).2f.")
                    % {"r": rec.received_lb, "e": rec.packed_lb})

    @api.constrains("agreed_qty_lb", "received_lb", "packed_lb", "agreed_overrun_pct")
    def _check_libras_vs_acordado(self):
        """Techo a lo que el maquilador puede declarar, y solo techo.

        DECISION DE DISEÑO. Lo que se factura sale de `packed_lb`, que hasta
        ahora declaraba el maquilador sin ningun limite: una orden adjudicada
        por 10.000 lb podia liquidar 50.000 y nadie lo notaba hasta el pago.

        Un tope rigido en `agreed_qty_lb` seria peor que el problema. Una
        cosecha real no sale clavada a lo que se pacto: se pesa en playa, se
        transporta, y llegan a planta unas libras que se parecen a las
        acordadas, no que son las acordadas. Un tope al milimetro dejaria
        tirada media orden legitima en la puerta de la planta.

        El equilibrio elegido:

        * Solo hay techo, no suelo. Traer menos de lo acordado es lo normal
          (se pesco menos, se quedo camaron en la piscina) y ademas solo
          perjudica al que cobra. Poner suelo seria molestar sin proteger nada.
        * El techo es `agreed_qty_lb` mas un margen configurable por orden
          (`agreed_overrun_pct`, 10 % por defecto) porque el margen razonable
          no es el mismo en 2.000 lb que en 200.000, ni con todos los
          clientes. Se deja por orden y no en un parametro global para que las
          partes puedan pactarlo al adjudicar.
        * Se aplica a `received_lb` y a `packed_lb`. La segunda ya estaba
          acotada por la primera, pero se comprueban las dos por separado para
          que el mensaje señale el dato equivocado, no el de al lado.
        * Si alguien de verdad tiene que empacar mucho mas de lo acordado, eso
          no es una correccion de captura: es otro trabajo, y le toca subir el
          margen a la vista de los dos o levantar otra orden.
        """
        for rec in self:
            if rec.agreed_qty_lb <= 0:
                continue
            techo = rec.agreed_qty_lb * (1.0 + (rec.agreed_overrun_pct or 0.0) / 100.0)
            for campo, etiqueta in (
                    ("received_lb", _("recibidas")),
                    ("packed_lb", _("empacadas"))):
                valor = rec[campo] or 0.0
                # Medio centesimo de holgura: los campos son de 2 decimales y
                # no tiene sentido tumbar un registro por el redondeo.
                if valor > techo + 0.005:
                    raise ValidationError(_(
                        "Las libras %(que)s (%(valor).2f) superan lo acordado "
                        "(%(acordado).2f) más el margen del %(margen).2f %%, que "
                        "son %(techo).2f lb. Corrija la cifra o acuerden con la "
                        "otra parte un margen mayor antes de registrarla.")
                        % {"que": etiqueta, "valor": valor,
                           "acordado": rec.agreed_qty_lb,
                           "margen": rec.agreed_overrun_pct or 0.0,
                           "techo": techo})

    # ------------------------------------------------------------------
    # Recorrido
    # ------------------------------------------------------------------
    def action_register_reception(self):
        for rec in self:
            if rec.state != "confirmed":
                raise ValidationError(_("La recepción se registra sobre una orden adjudicada."))
            if not rec.received_lb:
                raise ValidationError(_("Hay que indicar cuántas libras se recibieron."))
            rec.write({"state": "received", "received_date": fields.Datetime.now()})

    def action_register_packing(self):
        for rec in self:
            if rec.state != "received":
                raise ValidationError(_("Primero hay que registrar la recepción."))
            if not rec.packed_lb:
                raise ValidationError(_("Hay que indicar cuántas libras se empacaron."))
            rec.write({"state": "packed", "packed_date": fields.Datetime.now()})
            rec._abrir_acta()

    def _abrir_acta(self):
        """Dos firmas, una por parte. El acta la cierran ellos, no la plataforma.

        Cada vez que se registra un empaque se abre una ronda nueva. Lo de la
        ronda anterior no se borra: las firmas que llevaban decision se
        archivan (son la prueba de la discusion) y solo se descartan las que
        seguian en blanco, que no dicen nada de nadie.
        """
        self.ensure_one()
        Firma = self.env["shrimp.copack.acceptance"]
        # `filtered("active")` es cinturon y tirantes: el one2many ya excluye
        # las archivadas, salvo que alguien lea la orden con active_test=False.
        # Sin esto, una firma vieja podria volver a archivarse (pisando su
        # traza) o contarse como decision de la ronda en curso.
        vigentes = self.acceptance_ids.filtered("active")
        vigentes.filtered(lambda f: f.decision != "pending")._archivar(_(
            "Se registró un empaque nuevo sobre esta acta."))
        vigentes.filtered(lambda f: f.decision == "pending").unlink()
        # La ronda se calcula sobre TODAS las firmas, incluidas las archivadas:
        # la clave unica es (orden, rol, ronda) y no sabe de archivados.
        # En sudo: la numeracion de rondas es un detalle interno y si una regla
        # de acceso escondiera una firma de la otra parte, el numero se repetiria
        # y la clave unica tumbaria la apertura del acta.
        anteriores = Firma.sudo().with_context(active_test=False).search_read(
            [("order_id", "=", self.id)], ["ronda"])
        ronda = max([f["ronda"] for f in anteriores] or [0]) + 1
        for rol, socio in (("client", self.client_partner_id),
                           ("copacker", self.copacker_partner_id)):
            Firma.create({
                "order_id": self.id, "role": rol,
                "partner_id": socio.id, "ronda": ronda,
            })
        self.acceptance_state = "open"

    def _evaluar_acta(self):
        self.ensure_one()
        # Solo una orden empacada tiene acta que evaluar. Si llega aqui en otro
        # estado es que algo la movio por detras, y cerrar el acta entonces
        # equivaldria a dar por bueno un trabajo que nadie hizo.
        if self.state != "packed":
            return
        decisiones = self.acceptance_ids.filtered("active").mapped("decision")
        if "rejected" in decisiones:
            self.acceptance_state = "disputed"
        elif decisiones and all(d == "accepted" for d in decisiones):
            self.write({"acceptance_state": "closed", "state": "signed"})

    def action_reabrir_acta(self, motivo, actor=None):
        """Saca del atasco a una orden cuya acta quedo en disputa.

        Sin esto, `disputed` era un estado sin salida: una parte firmaba "no
        conforme", la orden se quedaba en `packed` + `disputed` para siempre y
        no habia forma ni de rectificar el empaque ni de cerrarla. Reabrir la
        devuelve a `received`, que es donde el maquilador puede volver a
        declarar lo empacado y abrir un acta nueva que firmen los dos.

        QUIEN PUEDE LLAMARLA: cualquiera de las dos partes de la orden, y
        nadie mas. Se penso en reservarla al maquilador (es su trabajo el que
        se corrige) y en reservarla al cliente (es su camaron), y las dos
        opciones reproducen el mismo atasco por el otro lado: el que no la
        tiene se queda sin salida si el otro no quiere moverse.

        Darsela a los dos no le regala nada a ninguno, porque reabrir CUESTA:
        la orden sale de los estados facturables (`es_facturable` pasa a
        falso) y las firmas vuelven a cero, asi que el maquilador que reabre
        pierde el cobro hasta que el cliente vuelva a firmar, y el cliente que
        reabre no puede tocar ni una libra por su cuenta. Y solo se puede
        reabrir lo que ya esta en disputa: sobre un acta firmada y conforme
        esto no hace nada.

        El motivo es obligatorio y viaja a la traza de cada firma archivada:
        una reapertura sin explicacion es exactamente lo que un dia hay que
        poder enseñarle a un tercero.
        """
        self.ensure_one()
        motivo = (motivo or "").strip()
        if not motivo:
            raise ValidationError(_(
                "Para reabrir el acta hay que decir por qué: es lo que la otra "
                "parte va a leer y lo que queda en el expediente."))
        actor = actor or self.env.user.partner_id
        # Igual que al firmar: el controlador trabaja en sudo, asi que el
        # control de quien es quien tiene que estar aqui y no en el ACL.
        if actor not in (self.client_partner_id | self.copacker_partner_id):
            raise AccessError(_(
                "El acta la reabre una de las dos partes de la orden."))
        if self.state != "packed" or self.acceptance_state != "disputed":
            raise ValidationError(_(
                "Solo se reabre un acta en disputa. Esta orden está en "
                "«%(estado)s» con el acta en «%(acta)s».")
                % {"estado": dict(self._fields["state"].selection).get(self.state, self.state),
                   "acta": dict(self._fields["acceptance_state"].selection).get(
                       self.acceptance_state, self.acceptance_state)})

        # Las firmas anteriores se archivan, NUNCA se borran: son la prueba de
        # que hubo una disputa y de lo que cada parte declaro entonces, con las
        # cifras congeladas que tenia delante al firmar.
        self.acceptance_ids.filtered("active")._archivar(motivo)
        self.write({
            "state": "received",
            "acceptance_state": "na",
            # La fecha de entrega se limpia porque la que valdra es la del
            # empaque corregido; `packed_lb` se conserva para que el maquilador
            # rectifique sobre lo declarado en vez de escribirlo de cero.
            "packed_date": False,
        })
        self.message_post(body=_(
            "Acta reabierta por %(quien)s. Motivo: %(motivo)s")
            % {"quien": actor.name or "", "motivo": motivo})
        return True

    def action_close(self):
        for rec in self:
            if rec.state != "signed":
                raise ValidationError(_("Se cierra cuando las dos partes firmaron el acta."))
            rec.state = "closed"
            # El estado `done` de la solicitud no lo escribia nadie: las
            # solicitudes adjudicadas se quedaban en `assigned` de por vida
            # aunque el trabajo estuviera cobrado y cerrado. El ciclo de la
            # solicitud lo termina la orden, que es quien sabe que acabo.
            if rec.request_id and rec.request_id.state == "assigned":
                rec.request_id.state = "done"

    def action_cancel(self):
        for rec in self:
            if rec.state in ("signed", "closed"):
                raise ValidationError(_("Un trabajo ya firmado no se cancela."))
            rec.state = "cancelled"
            # Cerrar el acta tambien. Sin esto las firmas pendientes seguian
            # vivas: las dos partes firmaban una orden CANCELADA, _evaluar_acta
            # la pasaba a "signed", de ahi a "closed", y acababa cobrandose en
            # liquidaciones y saliendo en el certificado de trazabilidad.
            if rec.acceptance_state == "open":
                rec.acceptance_ids.filtered(
                    lambda f: f.active and f.decision == "pending").unlink()
                rec.acceptance_state = "na"
