from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from . import copack_plazos

from odoo.addons.shrimp_marketplace.models.shrimp_selection import (
    PRESENTATIONS_WITH_VALUE_ADDED)

from .shrimp_copack_request import check_origen_del_cliente

# Valores de siempre (Ajustes › CamaronMarket › Empaque los puede cambiar).
PLATFORM_RATE_PER_LB = 0.01
DEFAULT_TOLERANCE_PCT = 0.5


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
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin",
                "shrimp.copack.notify.mixin"]
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
    # La compra concreta cuyo camarón se empaca (la empacadora que compró a
    # una camaronera). Antes el certificado buscaba las órdenes por PRODUCTO,
    # y cada orden salía en todas las compras de ese producto, de cualquier
    # comprador. Ahora el paso de empaque cuelga de su compra.
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Compra de origen", ondelete="set null", index=True)
    stock_lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Lote de inventario", ondelete="set null", index=True)
    # Lo que sale del empaque es un lote nuevo del mismo dueño (el producto
    # empacado), que nace del movimiento de empaque. Así la venta posterior de
    # ese producto arrastra el paso de empaque en su cadena, y la merma
    # (recibidas - empacadas) queda consumida en vez de seguir a la venta.
    packed_lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Lote empacado", readonly=True, copy=False,
        ondelete="set null", index=True)
    packing_move_ids = fields.One2many(
        "shrimp.stock.move", "copack_order_id", string="Movimientos de empaque")

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
    packed_presentation = fields.Selection(
        PRESENTATIONS_WITH_VALUE_ADDED, string="Presentación empacada")
    packed_presentation_note = fields.Char(
        string="Detalle de la presentación",
        help="Formato o detalle libre (p. ej. «master 5 lb IQF»). También guarda "
             "el texto que se escribía antes, cuando la presentación era libre.")

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
        string="Tolerancia (%)", default=lambda self: self._shrimp_default_tolerance_pct(),
        digits=(5, 2),
        help="Merma de manipulación que las partes dan por normal. Por defecto, "
             "la de Ajustes › CamaronMarket › Empaque.")

    # --- dinero ---
    service_amount = fields.Monetary(
        string="A cobrar por el empaque", compute="_compute_importes", store=True,
        currency_field="currency_id",
        help="Tarifa por las libras efectivamente empacadas.")
    platform_rate_per_lb = fields.Monetary(
        string="Comisión CamaronMarket por libra", currency_field="currency_id",
        default=lambda self: self._shrimp_default_platform_rate(),
        help="Lo que la plataforma cobra al maquilador por libra empacada. Se "
             "fija al crear la orden con la cuota de Ajustes › CamaronMarket › "
             "Empaque; cambiar la cuota no altera las órdenes ya creadas.")
    platform_amount = fields.Monetary(
        string="Comisión CamaronMarket", compute="_compute_importes", store=True,
        currency_field="currency_id")
    # La comisión de la plataforma se cobra (factura electrónica al
    # maquilador) cuando las dos partes firman el acta: es entonces cuando
    # las libras empacadas quedan aceptadas por ambos.
    charge_ids = fields.One2many(
        "shrimp.charge", "copack_order_id", string="Cobros de la plataforma")

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

    # --- plazo del acta (D-14) ---
    # El cliente tiene un plazo para firmar, con recordatorios. Si calla, el
    # acta se da por aceptada (como el informe de verificación): sin plazo,
    # un cliente que no firmaba dejaba al maquilador sin cobrar para siempre.
    acceptance_deadline = fields.Datetime(
        string="Plazo de firma del acta", readonly=True, copy=False, index=True,
        help="Si a esta hora una parte no firmó, su firma se da por conforme "
             "(el silencio vale conformidad). Ajustes › Empaque: horas para firmar.")
    acceptance_reminder_marks = fields.Char(
        string="Recordatorios de firma enviados", readonly=True, copy=False)
    tacit_acceptance = fields.Boolean(
        string="Aceptada por silencio", readonly=True, copy=False,
        help="El acta se cerró porque venció el plazo sin la firma de una de las partes.")
    signed_date = fields.Datetime(
        string="Firmada el", readonly=True, copy=False, index=True,
        help="Cuándo quedó firmada el acta. La orden firmada se cierra sola pasado el "
             "plazo de Ajustes › Empaque.")
    # --- mediación de la plataforma (D-14) ---
    dispute_date = fields.Datetime(string="En disputa desde", readonly=True, copy=False)
    mediation_resolution = fields.Selection(
        [("upheld", "La plataforma dio por buena el acta"),
         ("reopened", "La plataforma reabrió el acta")],
        string="Resolución de la plataforma", readonly=True, copy=False, tracking=True)
    mediation_note = fields.Text(string="Nota de la mediación", readonly=True, copy=False)
    # --- cancelación (D-14, D-34) ---
    cancel_reason = fields.Text(string="Motivo de la cancelación", readonly=True, copy=False)
    cancelled_by = fields.Selection(
        [("client", "El cliente"), ("copacker", "La planta"),
         ("platform", "La plataforma"), ("system", "Automática")],
        string="Cancelada por", readonly=True, copy=False)
    cancellation_ids = fields.One2many(
        "shrimp.copack.cancellation", "order_id", string="Solicitudes de cancelación")
    pending_cancellation_id = fields.Many2one(
        "shrimp.copack.cancellation", string="Cancelación pendiente",
        compute="_compute_pending_cancellation")

    @api.depends("cancellation_ids.state")
    def _compute_pending_cancellation(self):
        for rec in self:
            rec.pending_cancellation_id = rec.cancellation_ids.filtered(
                lambda c: c.state == "pending")[:1]

    # --- empaque propio ---
    # Una cuenta con perfil Maquilador APROBADO puede empacar su propio
    # camarón (el que tiene como camaronera o el que compró como empacadora).
    # No es un servicio de la plataforma: no hay solicitud ni ofertas, no hay
    # tarifa ni comisión y no hay acta de dos partes (cliente y planta son la
    # misma empresa). Sí deja el mismo rastro que un empaque de terceros
    # (recepción, empaque, cajas, merma consumida y lote empacado) para que el
    # informe de trazabilidad diga quién empacó y en qué planta. El cierre es
    # una sola conformidad interna de la propia cuenta (self_signoff_*).
    self_packing = fields.Boolean(
        string="Empaque propio", readonly=True, copy=False, index=True, tracking=True,
        help="La empresa empacó su propio camarón en su planta (perfil Maquilador): "
             "sin solicitud, sin tarifa, sin comisión y sin acta de dos partes.")
    self_signoff_user_id = fields.Many2one(
        "res.users", string="Cierre interno por", readonly=True, copy=False,
        ondelete="set null")
    self_signoff_date = fields.Datetime(
        string="Fecha del cierre interno", readonly=True, copy=False)

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

    @api.depends("state", "acceptance_state", "packed_lb", "self_packing")
    def _compute_es_facturable(self):
        for rec in self:
            rec.es_facturable = (
                # El empaque propio no es un servicio: no se cobra ni se
                # liquida (su paso por la trazabilidad lo decide
                # _shrimp_consta_en_trazabilidad).
                not rec.self_packing
                # D-14: facturable (liquidación) y en el certificado SOLO
                # desde «firmada» —por las dos partes o por silencio del
                # cliente—. Antes contaba ya al empacar, con el acta abierta.
                and rec.state in ("signed", "closed")
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

    @api.constrains("client_partner_id", "copacker_partner_id", "self_packing",
                    "request_id", "offer_id", "rate_per_lb", "platform_rate_per_lb")
    def _check_partes(self):
        for rec in self:
            if not rec.client_partner_id._shrimp_can_any("request_copack"):
                raise ValidationError(_(
                    "El cliente del empaque es quien es dueño del camarón: una "
                    "camaronera o una empacadora."))
            if not rec.copacker_partner_id._shrimp_can_any("provide_copack"):
                raise ValidationError(_("Quien empaca tiene que ser un maquilador."))
            misma = rec.client_partner_id._shrimp_same_entity_as(rec.copacker_partner_id)
            if rec.self_packing:
                # Empaque propio: la planta ES la empresa dueña del camarón, y
                # el perfil Maquilador tiene que estar aprobado (un perfil
                # pendiente no da capacidad de empacar).
                if rec.client_partner_id != rec.copacker_partner_id:
                    raise ValidationError(_(
                        "En un empaque propio la planta es la misma cuenta dueña del camarón."))
                if not rec.copacker_partner_id._shrimp_self_pack_allowed():
                    raise ValidationError(rec._shrimp_self_pack_profile_msg())
                if rec.request_id or rec.offer_id:
                    raise ValidationError(_(
                        "Un empaque propio no viene de una solicitud ni de una oferta: "
                        "no hay contraparte a la que contratar."))
                if (rec.rate_per_lb or 0.0) or (rec.platform_rate_per_lb or 0.0):
                    raise ValidationError(_(
                        "Un empaque propio no tiene tarifa ni comisión de la plataforma."))
            elif misma:
                # En el mercado (solicitud → oferta → orden) sigue prohibido:
                # una empresa no se adjudica su propia solicitud.
                raise ValidationError(_(
                    "Nadie se contrata a sí mismo. Si empacas tu propio camarón en "
                    "tu planta, regístralo como «Empaque propio»."))

    @api.constrains("product_id", "client_partner_id", "transaction_id", "stock_lot_id")
    def _check_lote_del_cliente(self):
        """El lote empacado tiene que ser del cliente que contrata el empaque.

        Apuntar la orden al lote (o a la compra) de otro inyecta un paso de
        empaque falso en el certificado de trazabilidad de ese tercero: su
        comprador leeria que su camaron paso por una planta por la que nunca
        paso. Misma regla que la solicitud (check_origen_del_cliente).
        """
        check_origen_del_cliente(self)

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
    # Parámetros (Ajustes › CamaronMarket › Empaque)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_default_platform_rate(self):
        """Cuota de la plataforma por libra empacada (antes fija en 0,01)."""
        return self.env["shrimp.settings"].get_float(
            "shrimp_copacking.platform_rate_per_lb", PLATFORM_RATE_PER_LB, minimum=0)

    @api.model
    def _shrimp_default_tolerance_pct(self):
        """Tolerancia de merma por defecto del acta (antes fija en 0,5 %)."""
        return self.env["shrimp.settings"].get_float(
            "shrimp_copacking.default_tolerance_pct", DEFAULT_TOLERANCE_PCT, minimum=0, maximum=100)

    @api.model
    def _shrimp_requires_valid_license(self):
        return self.env["shrimp.settings"].get_bool("shrimp_copacking.require_valid_license", False)

    def _shrimp_check_plant_license(self):
        """Con «Exigir habilitación vigente» activado (apagado por defecto),
        la planta que empaca —el maquilador, o la propia cuenta en el empaque
        propio— necesita su habilitación vigente para recibir y empacar."""
        if not self._shrimp_requires_valid_license():
            return
        hoy = fields.Date.context_today(self)
        for rec in self:
            planta = rec.copacker_partner_id.sudo()
            if not planta:
                continue
            hasta = planta.pack_habilitacion_hasta
            if hasta and hasta >= hoy:
                continue
            if hasta:
                motivo = _("venció el %s") % hasta.strftime("%d/%m/%Y")
            else:
                motivo = _("no tiene registrada la fecha de vigencia")
            raise ValidationError(_(
                "La planta %(p)s no puede recibir ni empacar: su habilitación %(m)s. "
                "La plataforma exige habilitación vigente para empacar; actualízala en "
                "«Mi cuenta» (Habilitación desde / hasta) y vuelve a intentarlo.") % {
                    "p": planta.name or "", "m": motivo})

    # ------------------------------------------------------------------
    # Recorrido
    # ------------------------------------------------------------------
    def _shrimp_check_puede_operar(self):
        """D-34: la planta tiene que estar operativa (perfil Maquilador
        aprobado) para recibir y empacar, venga por donde venga; y con una
        cancelación pendiente el trabajo se detiene."""
        for rec in self:
            if not rec.self_packing and not rec.copacker_partner_id.shrimp_is_operational(
                    role="maquilador"):
                raise ValidationError(_(
                    "«%s» no está operativa: su perfil de maquilador está pendiente de "
                    "aprobación o fue rechazado.") % (rec.copacker_partner_id.name or ""))
            if rec.pending_cancellation_id:
                raise ValidationError(_(
                    "Hay una solicitud de cancelación pendiente sobre esta orden: el trabajo "
                    "queda detenido hasta que se resuelva."))

    def action_register_reception(self):
        self._shrimp_check_plant_license()
        self._shrimp_check_puede_operar()
        for rec in self:
            if rec.state != "confirmed":
                raise ValidationError(_("La recepción se registra sobre una orden adjudicada."))
            if not rec.received_lb:
                raise ValidationError(_("Hay que indicar cuántas libras se recibieron."))
            rec.write({"state": "received", "received_date": fields.Datetime.now()})
            if not rec.self_packing:
                rec._cpk_notify(
                    rec.client_partner_id, _("Tu camarón llegó a la planta (%s)") % rec.name,
                    _("%(maq)s registró la recepción de %(lb)s lb para la orden %(o)s.") % {
                        "maq": rec.copacker_partner_id.name or "", "o": rec.name,
                        "lb": "{:,.2f}".format(rec.received_lb or 0.0)})

    def action_register_packing(self):
        self._shrimp_check_plant_license()
        self._shrimp_check_puede_operar()
        for rec in self:
            if rec.state != "received":
                raise ValidationError(_("Primero hay que registrar la recepción."))
            if not rec.packed_lb:
                raise ValidationError(_("Hay que indicar cuántas libras se empacaron."))
            rec.write({"state": "packed", "packed_date": fields.Datetime.now()})
            if rec.self_packing:
                # Sin acta de dos partes: la propia cuenta revisa las cifras y
                # cierra (action_self_close).
                continue
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
        plazo = fields.Datetime.now() + timedelta(hours=copack_plazos.acceptance_hours(self.env))
        self.write({"acceptance_state": "open", "acceptance_deadline": plazo,
                    "acceptance_reminder_marks": False, "tacit_acceptance": False})
        self._cpk_notify(
            self.client_partner_id, _("Firma el acta de empaque %s") % self.name,
            _("%(maq)s registró el empaque: recibidas %(r)s lb, empacadas %(e)s lb. Revisa el "
              "cuadre y firma el acta antes del %(plazo)s. Si no firmas ni das tu "
              "disconformidad en ese plazo, el acta se da por aceptada.") % {
                "maq": self.copacker_partner_id.name or "",
                "r": "{:,.2f}".format(self.received_lb or 0.0),
                "e": "{:,.2f}".format(self.packed_lb or 0.0),
                "plazo": self._cpk_fmt_dt(plazo)})

    def _evaluar_acta(self):
        self.ensure_one()
        # Solo una orden empacada tiene acta que evaluar. Si llega aqui en otro
        # estado es que algo la movio por detras, y cerrar el acta entonces
        # equivaldria a dar por bueno un trabajo que nadie hizo.
        if self.state != "packed":
            return
        decisiones = self.acceptance_ids.filtered("active").mapped("decision")
        if "rejected" in decisiones:
            if self.acceptance_state != "disputed":
                self.write({"acceptance_state": "disputed",
                            "dispute_date": fields.Datetime.now(),
                            "mediation_resolution": False, "mediation_note": False})
                self._shrimp_open_mediation()
        elif decisiones and all(d == "accepted" for d in decisiones):
            self._shrimp_sign_acta()

    def _shrimp_sign_acta(self):
        """El acta queda firmada (por las dos partes, por silencio o por la
        plataforma): comisión, inventario, aviso y, si así está configurado,
        cierre inmediato."""
        self.ensure_one()
        self.write({"acceptance_state": "closed", "state": "signed",
                    "signed_date": fields.Datetime.now()})
        self._register_platform_charge()
        # Con el acta firmada por los dos, las libras empacadas quedan
        # aceptadas: es el momento de mover el inventario.
        self._shrimp_apply_packing_stock()
        self._cpk_close_platform_activities(_("Acta firmada."))
        cierre = copack_plazos.autoclose_hours(self.env)
        texto = _("El acta de la orden %(o)s quedó firmada%(como)s: %(e)s lb empacadas. El "
                  "empaque ya es facturable y consta en la trazabilidad.") % {
            "o": self.name, "e": "{:,.2f}".format(self.packed_lb or 0.0),
            "como": (" " + _("por silencio (venció el plazo de firma)"))
            if self.tacit_acceptance else (
                " " + _("por resolución de la plataforma")
                if self.mediation_resolution == "upheld" else "")}
        if cierre:
            texto += " " + _("La orden se cerrará sola en %s horas.") % cierre
        self._cpk_notify(self.client_partner_id | self.copacker_partner_id,
                         _("Acta firmada: %s") % self.name, texto)
        if not cierre:
            self._shrimp_autoclose()

    def _shrimp_autoclose(self):
        for rec in self.filtered(lambda o: o.state == "signed"):
            rec.action_close()
            rec.message_post(body=_("Orden cerrada automáticamente tras la firma del acta."))
        return True

    @api.model
    def _cpk_fmt_dt(self, valor):
        if not valor:
            return ""
        return fields.Datetime.context_timestamp(self, valor).strftime("%d/%m/%Y %H:%M")

    def _cpk_portal_url(self, partner=None):
        self.ensure_one()
        return "/marketplace/copacking/orders/%s" % self.uuid_ref

    # ------------------------------------------------------------------
    # Plazo del acta y mediación (D-14)
    # ------------------------------------------------------------------
    def _shrimp_open_mediation(self):
        """Disputa: aviso a la otra parte y actividad de mediación para la
        plataforma, que puede dar por buena el acta o reabrirla."""
        self.ensure_one()
        rechazo = self.acceptance_ids.filtered(lambda f: f.active and f.decision == "rejected")[:1]
        quien = rechazo.partner_id
        otra = (self.client_partner_id | self.copacker_partner_id) - quien
        motivo = rechazo.reason or ""
        self._cpk_notify(
            otra, _("Acta en disputa: %s") % self.name,
            _("%(q)s no dio conformidad al acta de la orden %(o)s. Motivo: %(m)s. Pueden "
              "aclararlo y reabrir el acta para rectificar; la plataforma media la disputa.")
            % {"q": quien.name or "", "o": self.name, "m": motivo})
        self._cpk_notify_platform(
            _("La orden de empaque %(o)s está en disputa: %(q)s no dio conformidad (%(m)s). "
              "Media entre las partes y resuélvela desde la orden: «Dar por buena el acta» "
              "o «Reabrir para rectificar».") % {"o": self.name, "q": quien.name or "",
                                                  "m": motivo},
            _("Disputa de empaque %s") % self.name,
            _("Mediar disputa del acta %s") % self.name,
            deadline=fields.Date.context_today(self) + timedelta(
                days=copack_plazos.mediation_days(self.env)))

    def _shrimp_tacit_acceptance(self):
        """Venció el plazo de firma: las firmas pendientes se dan por
        conformes (el silencio vale conformidad)."""
        for rec in self:
            if rec.state != "packed" or rec.acceptance_state != "open":
                continue
            pendientes = rec.acceptance_ids.filtered(
                lambda f: f.active and f.decision == "pending")
            if not pendientes:
                continue
            rec.tacit_acceptance = True
            for firma in pendientes:
                firma._firmar("accepted", _(
                    "Aceptada por silencio: venció el plazo de firma del acta (%s).")
                    % rec._cpk_fmt_dt(rec.acceptance_deadline), auto=True)
        return True

    @api.model
    def _cron_copack_acta(self, limit=500):
        """Cada hora: recordatorios de firma, aceptación por silencio al vencer
        el plazo y cierre automático de las órdenes firmadas."""
        ahora = fields.Datetime.now()
        abiertas = self.sudo().search([
            ("state", "=", "packed"), ("acceptance_state", "=", "open"),
            ("acceptance_deadline", "!=", False)], limit=limit)
        for rec in abiertas:
            if rec.acceptance_deadline <= ahora:
                rec._shrimp_tacit_acceptance()
                continue
            marcas = set((rec.acceptance_reminder_marks or "").split())
            for horas in copack_plazos.reminder_hours(self.env):
                clave = "r%s" % horas
                if clave in marcas or ahora < rec.acceptance_deadline - timedelta(hours=horas):
                    continue
                marcas.add(clave)
                pendientes = rec.acceptance_ids.filtered(
                    lambda f: f.active and f.decision == "pending").mapped("partner_id")
                if pendientes:
                    rec._cpk_notify(
                        pendientes, _("Recordatorio: firma el acta %s") % rec.name,
                        _("El acta de la orden %(o)s vence el %(p)s. Si no firmas ni das tu "
                          "disconformidad antes, se da por aceptada.") % {
                            "o": rec.name, "p": rec._cpk_fmt_dt(rec.acceptance_deadline)})
                # Un solo recordatorio por pasada: si se saltó uno (plazo
                # corto), los dos no salen juntos.
                break
            rec.acceptance_reminder_marks = " ".join(sorted(marcas)) or False
        horas = copack_plazos.autoclose_hours(self.env)
        firmadas = self.sudo().search([
            ("state", "=", "signed"), ("self_packing", "=", False),
            "|", ("signed_date", "=", False),
            ("signed_date", "<=", ahora - timedelta(hours=horas))], limit=limit)
        firmadas._shrimp_autoclose()
        return True

    def _shrimp_check_platform(self):
        if not (self.env.su or self.env.user._is_superuser()
                or self.env.user.has_group("shrimp_copacking.group_copacking_manager")
                or self.env.user.has_group("shrimp_marketplace.group_shrimp_manager")):
            raise AccessError(_("Esta acción la hace la plataforma."))

    def action_mediation_uphold(self, note):
        """Mediación: la plataforma da por buena el acta en disputa (las
        cifras declaradas quedan firmadas; la disconformidad queda archivada
        en el historial)."""
        self._shrimp_check_platform()
        note = (note or "").strip()
        if not note:
            raise ValidationError(_("Explica la resolución: es lo que leen las dos partes."))
        for rec in self:
            if rec.state != "packed" or rec.acceptance_state != "disputed":
                raise ValidationError(_("Solo se media un acta en disputa."))
            rec.write({"mediation_resolution": "upheld", "mediation_note": note})
            rec.message_post(body=_("Mediación de la plataforma: el acta se da por buena. %s")
                             % note)
            rec._shrimp_sign_acta()
        return True

    def action_mediation_reopen(self, note):
        """Mediación: la plataforma reabre el acta para que la planta rectifique."""
        self._shrimp_check_platform()
        for rec in self:
            rec.action_reabrir_acta(note, platform=True)
            rec.write({"mediation_resolution": "reopened", "mediation_note": note})
        return True

    # ------------------------------------------------------------------
    # Inventario: el empaque transforma el lote del cliente
    # ------------------------------------------------------------------
    def _shrimp_source_lots(self):
        """Lotes del cliente de los que sale el camarón que se empacó."""
        self.ensure_one()
        Lot = self.env["shrimp.stock.lot"].sudo()
        cliente = self.client_partner_id
        if self.stock_lot_id and self.stock_lot_id.owner_id == cliente:
            return self.stock_lot_id
        if self.transaction_id:
            lots = Lot.search([("origin_move_id", "in", self.transaction_id.stock_move_ids.ids),
                               ("owner_id", "=", cliente.id)], order="id asc")
            if lots:
                return lots
        if self.product_id:
            # Lotes propios del producto que todavía no se empacaron.
            return Lot.search([
                ("product_id", "=", self.product_id.id), ("owner_id", "=", cliente.id),
                ("state", "=", "available"), ("available_qty", ">", 0),
                "|", ("origin_move_id", "=", False),
                ("origin_move_id.move_type", "!=", "packing"),
            ], order="create_date asc, id asc")
        return Lot

    def _shrimp_apply_packing_stock(self, at_date=None):
        """Saca del lote del cliente lo que entró a planta y crea el lote
        empacado. Idempotente. Devuelve el lote empacado (o vacío si no hay
        lote de origen que mover: órdenes sin vínculo con el inventario)."""
        Lot = self.env["shrimp.stock.lot"].sudo()
        salida = Lot
        for rec in self:
            if rec.packed_lot_id or rec.packing_move_ids:
                salida |= rec.packed_lot_id
                continue
            if not rec.received_lb or not rec.packed_lb:
                continue
            fuentes = rec._shrimp_source_lots().filtered(lambda l: l.available_qty > 0)
            if not fuentes:
                continue
            fecha = at_date or rec.packed_date or fields.Datetime.now()
            empacar = rec.packed_lb
            merma = max(0.0, (rec.received_lb or 0.0) - rec.packed_lb)
            fmt = self.env["shrimp.transaction"].shrimp_fmt_qty
            motivo_emp = _("Empaque %(o)s en %(p)s") % {
                "o": rec.name, "p": rec.copacker_partner_id.name or ""}
            motivo_merma = _("Merma de empaque %(o)s: recibidas %(r)s, empacadas %(e)s") % {
                "o": rec.name, "r": fmt(rec.received_lb, "lb"), "e": fmt(rec.packed_lb, "lb")}
            # Primero la merma (lo que no llegó a la caja) y luego lo empacado:
            # si el lote ya no tiene todo (datos antiguos en los que se vendió
            # del lote sin empacar), lo que falta se da por vendido empacado.
            for lot in fuentes:
                if merma < 0.005:
                    break
                take = min(lot.available_qty, merma)
                if take <= 0:
                    continue
                lot._shrimp_internal_move(
                    "consumption", take, motivo_merma, copack_order_id=rec.id, date=fecha)
                merma -= take
            primero = self.env["shrimp.stock.move"]
            empacado = 0.0
            for lot in fuentes:
                if empacar < 0.005:
                    break
                take = min(lot.available_qty, empacar)
                if take <= 0:
                    continue
                move = lot._shrimp_internal_move(
                    "packing", take, motivo_emp, copack_order_id=rec.id, date=fecha)
                primero = primero or move
                empacado += take
                empacar -= take
            if not primero:
                continue
            base = fuentes[:1]
            packed = Lot.create({
                "product_id": base.product_id.id,
                "owner_id": rec.client_partner_id.id,
                "origin_move_id": primero.id,
                "initial_qty": empacado,
                "available_qty": empacado,
                "uom_id": base.uom_id.id,
                "state": "available",
            })
            rec.with_context(tracking_disable=True).write({"packed_lot_id": packed.id})
            base.product_id._compute_available_qty()
            if base.product_id.state in ("published", "sold"):
                base.product_id._update_state_from_stock()
            rec.message_post(body=_("Inventario: %(e)s empacadas pasan al lote empacado; merma de %(m)s consumida.") % {
                "e": fmt(empacado, "lb"), "m": fmt(max(0.0, rec.received_lb - rec.packed_lb), "lb")})
            salida |= packed
        return salida

    def _register_platform_charge(self):
        """Comisión de la plataforma por las libras empacadas (una por orden)."""
        Charge = self.env["shrimp.charge"].sudo()
        libra = self.env.ref("shrimp_marketplace.uom_libra", raise_if_not_found=False)
        for rec in self:
            if rec.self_packing:
                continue   # el empaque propio no paga comisión
            if (rec.platform_amount or 0.0) <= 0 or rec.charge_ids.filtered(
                    lambda c: c.charge_type == "copack_platform"
                    and c.state not in ("cancelled", "credited")):
                continue
            Charge._register_charge({
                "charge_type": "copack_platform",
                "copack_order_id": rec.id,
                "payer_partner_id": rec.copacker_partner_id.id,
                "transaction_id": rec.transaction_id.id or False,
                "product_id": rec.product_id.id or False,
                "qty": rec.packed_lb,
                "uom_id": libra.id if libra else False,
                # La tarifa queda a la vista y la factura lleva una línea de
                # 1 × importe ya redondeado: el concepto dice «… : 3.920,05
                # libras × 1 ctv» (shrimp.charge._invoice_line_name).
                "rate_cents": round((rec.platform_rate_per_lb or 0.0) * 100.0, 4),
                "invoice_qty": 1.0,
                "amount": rec.currency_id.round(rec.platform_amount or 0.0)
                if rec.currency_id else round(rec.platform_amount or 0.0, 2),
                "currency_id": rec.currency_id.id,
                "origin": rec.name,
                "description": _("Comisión de empaque – %s") % (rec.name or ""),
            })

    def action_reabrir_acta(self, motivo, actor=None, platform=False):
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
        if platform:
            # Mediación (D-14): la plataforma reabre; quién lo pide lo
            # comprueba action_mediation_reopen.
            self._shrimp_check_platform()
            actor = self.env["res.partner"]
        else:
            actor = actor or self.env.user.shrimp_actor_partner_id
        # Igual que al firmar: el controlador trabaja en sudo, asi que el
        # control de quien es quien tiene que estar aqui y no en el ACL.
        if not platform and actor not in (self.client_partner_id | self.copacker_partner_id):
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
        quien = actor.name if actor else _("la plataforma (mediación)")
        self.message_post(body=_(
            "Acta reabierta por %(quien)s. Motivo: %(motivo)s")
            % {"quien": quien or "", "motivo": motivo})
        self._cpk_close_platform_activities(_("Acta reabierta."))
        self._cpk_notify(
            (self.client_partner_id | self.copacker_partner_id) - actor,
            _("Acta reabierta: %s") % self.name,
            _("%(q)s reabrió el acta de la orden %(o)s. Motivo: %(m)s. La planta rectifica el "
              "empaque y las dos partes vuelven a firmar.") % {
                "q": quien or "", "o": self.name, "m": motivo})
        return True

    def action_close(self):
        for rec in self:
            if rec.self_packing:
                rec.action_self_close()
                continue
            if rec.state != "signed":
                raise ValidationError(_("Se cierra cuando las dos partes firmaron el acta."))
            rec.state = "closed"
            # El estado `done` de la solicitud no lo escribia nadie: las
            # solicitudes adjudicadas se quedaban en `assigned` de por vida
            # aunque el trabajo estuviera cobrado y cerrado. El ciclo de la
            # solicitud lo termina la orden, que es quien sabe que acabo.
            if rec.request_id and rec.request_id.state == "assigned":
                rec.request_id.state = "done"

    # ------------------------------------------------------------------
    # Cancelación (D-14, D-34)
    # ------------------------------------------------------------------
    def _shrimp_role_of(self, actor):
        self.ensure_one()
        if not actor:
            return None
        if actor == self.copacker_partner_id and not self.self_packing:
            return "copacker"
        if actor == self.client_partner_id:
            return "client"
        return False

    def shrimp_cancel_problem(self, actor=None, role=None):
        """'' si se puede cancelar ahora; si no, por qué. UNA regla para el
        portal, la API y el backoffice.

        * firmada o cerrada: nunca; empacada: tampoco (el camino es el acta);
        * el cliente (dueño del camarón): antes de empacar;
        * la planta: antes de recibir el camarón, con motivo (la solicitud se
          reabre a las demás plantas). Con el camarón del cliente en planta,
          NO cancela sola: acta de devolución firmada por el cliente o
          aprobación de la plataforma (D-34);
        * la plataforma (actor None): antes de empacar.
        """
        self.ensure_one()
        if role is None and actor is not None:
            role = self._shrimp_role_of(actor)
            if role is False:
                return _("Esta orden la cancela una de sus dos partes.")
        if self.state == "cancelled":
            return _("La orden ya está cancelada.")
        if self.state in ("signed", "closed"):
            return _("Un trabajo ya firmado no se cancela.")
        if self.self_packing:
            return ""
        if self.state == "packed":
            return _("Esta orden ya está empacada: no se cancela. Si no estás conforme, no "
                     "firmes el acta (queda en disputa) y reabre el acta para rectificar el "
                     "empaque.")
        if role == "copacker" and self.state == "received":
            return _("El camarón del cliente ya está en tu planta: no puedes cancelar sin su "
                     "consentimiento. Registra un acta de devolución para que el cliente la "
                     "firme, o pide la aprobación de la plataforma.")
        return ""

    def action_cancel(self, actor=None, reason=None):
        """Cancela la orden comprobando quién y cuándo (shrimp_cancel_problem).
        Sin `actor` la cancela la plataforma (backoffice, tests)."""
        for rec in self:
            role = rec._shrimp_role_of(actor) if actor is not None else "platform"
            if role is False:
                raise AccessError(_("Esta orden la cancela una de sus dos partes."))
            problema = rec.shrimp_cancel_problem(
                actor=actor, role=None if role == "platform" else role)
            if problema:
                raise ValidationError(problema)
            motivo = (reason or "").strip()
            if role == "copacker" and not motivo:
                raise ValidationError(_(
                    "Explica al cliente por qué cancelas: es lo que va a leer, y la "
                    "solicitud vuelve a salir a las demás plantas."))
            rec._shrimp_do_cancel(role, motivo, reopen=(role == "copacker"))
        return True

    def _shrimp_do_cancel(self, by, reason, reopen=False):
        """Aplica la cancelación. No comprueba quién: lo hace quien llama.

        * La oferta adjudicada pasa a «cancelada».
        * Si cancela la planta (por sí misma antes de recibir, con acta de
          devolución o con aprobación de la plataforma), la solicitud se
          REABRE a las demás plantas (D-14). Si cancela el cliente, la
          plataforma o el sistema, la solicitud se cancela con la orden.
        """
        for rec in self:
            if rec.state == "cancelled":
                continue
            rec.write({"state": "cancelled", "cancelled_by": by if by else "platform",
                       "cancel_reason": reason or False})
            if rec.self_packing:
                continue
            # Cerrar el acta tambien. Sin esto las firmas pendientes seguian
            # vivas: las dos partes firmaban una orden CANCELADA, _evaluar_acta
            # la pasaba a "signed", de ahi a "closed", y acababa cobrandose en
            # liquidaciones y saliendo en el certificado de trazabilidad.
            if rec.acceptance_state == "open":
                rec.acceptance_ids.filtered(
                    lambda f: f.active and f.decision == "pending").unlink()
                rec.acceptance_state = "na"
            rec.cancellation_ids.filtered(lambda c: c.state == "pending").write(
                {"state": "withdrawn"})
            if rec.offer_id and rec.offer_id.state == "accepted":
                rec.offer_id.write({"state": "cancelled", "closed_reason": "order_cancelled"})
            rec._cpk_close_platform_activities(_("Orden cancelada."))
            solicitud = rec.request_id
            if solicitud and solicitud.state == "assigned" and solicitud.order_id == rec:
                if reopen:
                    solicitud._shrimp_reopen(reason or _("sin motivo"),
                                             excluir=rec.copacker_partner_id)
                elif not self.env.context.get("shrimp_copack_from_request"):
                    solicitud.write({"state": "cancelled"})
                    solicitud.offer_ids.filtered(lambda o: o.state == "sent").write(
                        {"state": "rejected", "closed_reason": "request_cancelled"})
            quien = dict(self._fields["cancelled_by"].selection).get(rec.cancelled_by, "")
            texto = _("%(q)s canceló la orden de empaque %(o)s.%(m)s") % {
                "q": quien, "o": rec.name,
                "m": (" " + _("Motivo: %s") % reason) if reason else ""}
            if reopen:
                texto += " " + _("La solicitud vuelve a estar publicada para las demás plantas.")
            destinos = rec.client_partner_id | rec.copacker_partner_id
            if by == "client":
                destinos = rec.copacker_partner_id
            elif by == "copacker":
                destinos = rec.client_partner_id | rec.copacker_partner_id
            rec._cpk_notify(destinos, _("Orden %s cancelada") % rec.name, texto)
        return True

    def action_request_return(self, reason, returned_lb=0.0, notes=None, actor=None):
        """D-34: la planta, con el camarón del cliente en su planta, propone
        cancelar devolviéndolo. Queda un acta de devolución que el cliente firma
        (o rechaza). Firmada, la orden se cancela y la solicitud se reabre."""
        self.ensure_one()
        return self.env["shrimp.copack.cancellation"]._shrimp_request(
            self, "return", reason, actor=actor, returned_lb=returned_lb, notes=notes)

    def action_request_platform_cancel(self, reason, actor=None):
        """D-34: la planta pide a la plataforma que apruebe la cancelación."""
        self.ensure_one()
        return self.env["shrimp.copack.cancellation"]._shrimp_request(
            self, "platform", reason, actor=actor)

    # ------------------------------------------------------------------
    # Empaque propio (la empresa empaca su camarón en su propia planta)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_self_pack_profile_msg(self):
        return _("Para empacar tu propio producto necesitas el perfil Maquilador "
                 "(aprobado). Agrégalo desde «Mi cuenta» en la plataforma de empaque.")

    @api.model
    def shrimp_create_self_packing(self, partner, origen=None, qty_lb=0.0, **extra):
        """Crea una orden de EMPAQUE PROPIO para `partner` (su cuenta de
        perfiles), lista para registrar la recepción.

        `origen` es el mismo token que usa la solicitud ("t:<uuid compra>" o
        "p:<uuid lote>") y se valida contra la cuenta: solo se empaca camarón
        propio. `extra` admite supplies_notes, tolerance_pct, packed_presentation
        y packed_presentation_note. Lanza ValidationError si la cuenta no tiene
        el perfil Maquilador aprobado o no es dueña de camarón.
        """
        socio = partner.sudo()._shrimp_role_holder()
        if not socio._shrimp_self_pack_allowed():
            raise ValidationError(self._shrimp_self_pack_profile_msg())
        if not socio._shrimp_can_any("request_copack"):
            raise ValidationError(_(
                "El empaque propio es de quien es dueño del camarón: una camaronera "
                "o una empacadora."))
        try:
            qty = float(qty_lb or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:
            raise ValidationError(_("Las libras a empacar deben ser mayores que cero."))
        vals = self.env["shrimp.copack.request"].sudo()._shrimp_resolver_origen(socio, origen)
        if not vals:
            raise ValidationError(_(
                "Elige qué camarón vas a empacar: uno de tus lotes o una de tus compras."))
        permitidos = {"supplies_notes", "tolerance_pct", "packed_presentation",
                      "packed_presentation_note", "agreed_overrun_pct"}
        vals.update({k: v for k, v in extra.items() if k in permitidos and v not in (None, "")})
        vals.update({
            "self_packing": True,
            "client_partner_id": socio.id,
            "copacker_partner_id": socio.id,
            "agreed_qty_lb": qty,
            "rate_per_lb": 0.0,
            "platform_rate_per_lb": 0.0,
        })
        orden = self.sudo().create(vals)
        orden.message_post(body=_("Empaque propio registrado por %s.") % (socio.name or ""))
        return orden

    def action_self_correct(self):
        """Empaque propio: volver a «recibida» para corregir lo empacado antes
        del cierre interno (todavía no se movió el inventario)."""
        for rec in self:
            if not rec.self_packing or rec.state != "packed":
                raise ValidationError(_(
                    "Solo se corrige un empaque propio empacado y sin cerrar."))
            rec.write({"state": "received", "packed_date": False})

    def action_self_close(self, user=None):
        """Cierre interno del empaque propio: una sola conformidad, la de la
        propia cuenta (no hay otra parte que firme). Mueve el inventario igual
        que el acta firmada de un empaque de terceros: la merma se consume y lo
        empacado pasa al lote empacado."""
        user = user or self.env.user
        for rec in self:
            if not rec.self_packing:
                raise ValidationError(_("Esta orden no es un empaque propio."))
            if rec.state != "packed":
                raise ValidationError(_(
                    "El empaque propio se cierra después de registrar lo empacado."))
            rec.write({
                "state": "closed",
                "self_signoff_user_id": user.id,
                "self_signoff_date": fields.Datetime.now(),
            })
            rec._shrimp_apply_packing_stock()
            rec.message_post(body=_("Cierre interno del empaque propio por %s.")
                             % (user.partner_id.name or user.name or ""))
        return True

    def _shrimp_consta_en_trazabilidad(self):
        """True si la orden sale en el certificado: un empaque de terceros
        hecho y sin disputa (es_facturable) o un empaque propio cerrado."""
        self.ensure_one()
        if self.self_packing:
            return self.state == "closed" and (self.packed_lb or 0.0) > 0
        return bool(self.es_facturable)

    def shrimp_plant_label(self):
        """Texto de la planta para la trazabilidad. En el empaque propio:
        «Empaque propio — empacado por <empresa> (planta <código>)»."""
        self.ensure_one()
        planta = self.copacker_partner_id.sudo()
        codigo = planta.pack_codigo_establecimiento or _("sin código registrado")
        if self.self_packing:
            return _("Empaque propio — empacado por %(empresa)s (planta %(codigo)s)") % {
                "empresa": planta.name or "", "codigo": codigo}
        return planta.name or ""

    @api.model
    def _shrimp_self_pack_origin_for_lot(self, lot):
        """Token de origen («t:<compra>» o «p:<lote>») con el que un lote de
        inventario se puede mandar a empaque propio, o "" si no aplica (lote
        ya empacado, o sin compra ni producto propio del dueño)."""
        lot = lot.sudo()
        dueno = lot.owner_id
        if not dueno or lot.origin_move_id.move_type == "packing":
            return ""
        tx = lot.origin_move_id.transaction_id
        if tx and tx.buyer_partner_id == dueno and tx.state in ("confirmed", "done"):
            return "t:%s" % tx.uuid_ref
        if lot.product_id.seller_partner_id == dueno:
            return "p:%s" % lot.product_id.uuid_ref
        return ""
