from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from . import copack_plazos

from odoo.addons.shrimp_marketplace.models.shrimp_selection import (
    PRESENTATIONS_WITH_VALUE_ADDED)


def check_origen_del_cliente(records):
    """El camarón de una solicitud u orden de empaque es del propio cliente.

    Vale de dos formas: un lote que el cliente publica como vendedor (la
    camaronera), o una compra en la que el cliente es el COMPRADOR (la
    empacadora con el camarón que adquirió), confirmada o recibida. Lo usan
    la solicitud y la orden: una sola regla.
    """
    for rec in records:
        cliente = rec.client_partner_id
        compra = rec.transaction_id.sudo()
        if compra:
            if compra.buyer_partner_id != cliente:
                raise ValidationError(_(
                    "La compra «%(compra)s» no es de «%(cliente)s»: solo se "
                    "manda a empacar camarón propio.")
                    % {"compra": compra.name or "", "cliente": cliente.name or ""})
            if compra.state not in ("confirmed", "done"):
                raise ValidationError(_(
                    "La compra «%s» todavía no está confirmada.") % (compra.name or ""))
            if rec.product_id and rec.product_id != compra.product_id:
                raise ValidationError(_(
                    "El lote no corresponde a la compra elegida."))
        elif rec.product_id:
            dueno = rec.product_id.sudo().seller_partner_id
            if dueno != cliente:
                raise ValidationError(_(
                    "El lote «%(lote)s» es de «%(dueno)s», no de «%(cliente)s». "
                    "Solo se puede mandar a empacar camarón propio.")
                    % {"lote": rec.product_id.display_name or "",
                       "dueno": dueno.name or _("otro titular"),
                       "cliente": cliente.name or ""})
        if rec.stock_lot_id and rec.stock_lot_id.sudo().owner_id != cliente:
            raise ValidationError(_(
                "El lote de inventario elegido no es de «%s».") % (cliente.name or ""))


class ShrimpCopackRequest(models.Model):
    """Lo que un cliente necesita empacar y no puede empacar el mismo.

    El cliente es una camaronera o una empacadora que se quedo sin capacidad.
    Publica cuanto tiene, de que talla y para cuando, y los maquiladores le
    responden con su tarifa. Los insumos los pone siempre el cliente: aqui solo
    se deja constancia de cuales, porque "falto empaque y se paro la linea" es
    la discusion mas cara de este negocio.
    """

    _name = "shrimp.copack.request"
    _description = "Solicitud de servicio de empaque"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin",
                "shrimp.copack.notify.mixin"]
    _order = "needed_from, id desc"

    name = fields.Char(
        string="Referencia", required=True, copy=False, readonly=True,
        default=lambda self: _("Nueva"))

    client_partner_id = fields.Many2one(
        "res.partner", string="Cliente", required=True, ondelete="restrict",
        index=True, tracking=True,
        help="Quien es dueño del camarón y necesita empacarlo.")

    # El lote es opcional: se puede pedir empaque de algo que todavia no esta
    # publicado. Pero si viene, es lo que permite que el paso de empaque
    # aparezca despues en el certificado de trazabilidad de ese lote.
    product_id = fields.Many2one(
        "shrimp.product", string="Lote", ondelete="set null", index=True,
        help="Si se indica, el empaque queda enlazado a la trazabilidad del lote.")
    # Lo que la empacadora manda a empacar casi nunca es un lote SUYO: es el
    # camarón que COMPRÓ. El vínculo con esa compra (y con el lote de stock
    # que recibió) es lo que permite que el paso de empaque salga en la
    # trazabilidad de ESA compra y no en la de todas las del mismo producto.
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Compra de origen", ondelete="set null", index=True,
        help="La compra con la que el cliente adquirió el camarón que manda a empacar.")
    stock_lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Lote de inventario", ondelete="set null", index=True,
        help="El lote de inventario del cliente (si ya recibió la compra).")

    # Dirigida o abierta. El cliente normalmente entra al directorio, ve quien
    # empaca y desde cuanto, y le pide a uno en concreto: asi es como se
    # contrata un servicio. Dejarla abierta sigue siendo util cuando quiere
    # varios presupuestos, pero no puede ser el unico camino, porque obligaria
    # a publicar a ciegas y esperar para enterarse de los precios.
    copacker_partner_id = fields.Many2one(
        "res.partner", string="Dirigida a", ondelete="set null", index=True,
        help="Si se deja vacío, la solicitud va a la bandeja de todos los maquiladores.")
    is_open = fields.Boolean(
        string="Abierta a todos", compute="_compute_is_open", store=True)

    quantity_lb = fields.Float(
        string="Libras a empacar", required=True, digits=(16, 2), tracking=True)
    size_grade_id = fields.Many2one(
        "shrimp.size.grade", string="Talla", ondelete="restrict")
    presentation = fields.Selection(
        PRESENTATIONS_WITH_VALUE_ADDED, string="Presentación", required=True,
        default="entero")

    needed_from = fields.Date(string="Necesita desde", required=True, tracking=True)
    needed_to = fields.Date(string="Necesita hasta", required=True, tracking=True)

    supplies_notes = fields.Text(
        string="Insumos que lleva el cliente",
        help="Empaque, etiquetas, aditivos. Todo lo pone el cliente; esto es "
             "la constancia de qué se comprometió a llevar.")
    notes = fields.Text(string="Observaciones")

    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("published", "Publicada"),
            ("assigned", "Adjudicada"),
            ("done", "Cerrada"),
            # D-47: una solicitud publicada que nadie adjudica vence sola
            # (Ajustes › Empaque: días de vigencia, o al pasar «Necesita hasta»).
            ("expired", "Vencida"),
            ("cancelled", "Cancelada"),
        ],
        string="Estado", default="draft", required=True, index=True, tracking=True)

    # --- vigencia (D-47) ---
    published_date = fields.Datetime(
        string="Publicada el", readonly=True, copy=False,
        help="Última publicación (o reapertura) de la solicitud.")
    expires_on = fields.Date(
        string="Vence el", readonly=True, copy=False, index=True, tracking=True,
        help="Si nadie la adjudica, la solicitud vence este día (al terminar el día). "
             "Es lo primero entre «Necesita hasta» y los días de vigencia de Ajustes.")
    expiry_reminder_sent = fields.Boolean(
        string="Aviso de vencimiento enviado", readonly=True, copy=False)
    # --- adjudicación parcial (D-47) ---
    parent_request_id = fields.Many2one(
        "shrimp.copack.request", string="Remanente de", readonly=True, copy=False,
        ondelete="set null", index=True,
        help="La solicitud original cuya adjudicación fue parcial: esta publica las "
             "libras que quedaron sin planta.")
    child_request_ids = fields.One2many(
        "shrimp.copack.request", "parent_request_id", string="Remanentes publicados")

    offer_ids = fields.One2many(
        "shrimp.copack.offer", "request_id", string="Ofertas recibidas")
    offer_count = fields.Integer(compute="_compute_offer_count", string="Ofertas")
    order_id = fields.Many2one(
        "shrimp.copack.order", string="Orden generada", readonly=True, copy=False)

    @api.depends("offer_ids")
    def _compute_offer_count(self):
        for rec in self:
            rec.offer_count = len(rec.offer_ids)

    @api.depends("copacker_partner_id")
    def _compute_is_open(self):
        for rec in self:
            rec.is_open = not rec.copacker_partner_id

    @api.constrains("copacker_partner_id")
    def _check_dirigida(self):
        for rec in self:
            if (rec.copacker_partner_id
                    and not rec.copacker_partner_id._shrimp_can_any("provide_copack")):
                raise ValidationError(_(
                    "Una solicitud de empaque se dirige a un maquilador. «%s» no lo es.")
                    % (rec.copacker_partner_id.name or ""))
            # Varios perfiles: una empacadora puede ser también maquiladora,
            # pero no dirigirse una solicitud a sí misma.
            if rec.copacker_partner_id and rec.client_partner_id \
                    and rec.copacker_partner_id.commercial_partner_id \
                    == rec.client_partner_id.commercial_partner_id:
                raise ValidationError(_(
                    "Una empresa no se contrata a sí misma: el empaque de tu propio "
                    "camarón en tu planta es una operación interna, no un servicio de "
                    "la plataforma (no hay contraparte que firme el acta ni comisión "
                    "que cobrar)."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nueva")) == _("Nueva"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.copack.request") or _("Nueva")
        return super().create(vals_list)

    @api.constrains("client_partner_id")
    def _check_cliente(self):
        for rec in self:
            if not rec.client_partner_id._shrimp_can_any("request_copack"):
                raise ValidationError(_(
                    "El servicio de empaque lo pide quien es dueño del camarón: "
                    "una camaronera o una empacadora. «%s» no lo es.")
                    % (rec.client_partner_id.name or ""))

    @api.constrains("needed_from", "needed_to")
    def _check_ventana(self):
        for rec in self:
            if rec.needed_from and rec.needed_to and rec.needed_to < rec.needed_from:
                raise ValidationError(_(
                    "La ventana no puede terminar antes de empezar."))

    @api.constrains("quantity_lb")
    def _check_cantidad(self):
        for rec in self:
            if rec.quantity_lb <= 0:
                raise ValidationError(_("Las libras a empacar deben ser mayores que cero."))

    @api.constrains("product_id", "client_partner_id", "transaction_id", "stock_lot_id")
    def _check_lote_del_cliente(self):
        """El camarón que se manda a empacar tiene que ser del propio cliente.

        Si no se comprueba, se puede levantar una solicitud (y de ahi una
        orden) apuntando al lote de otro, y el paso de empaque aparece en el
        certificado de trazabilidad de un tercero que nunca piso esa planta.
        Ensuciar el dato de otro es peor que equivocarse en el propio.

        Vale de dos formas: un lote que el cliente publica como vendedor (la
        camaronera), o una compra en la que el cliente es el COMPRADOR (la
        empacadora con el camarón que adquirió), confirmada o recibida.
        """
        self._shrimp_check_origen_del_cliente()

    def _shrimp_check_origen_del_cliente(self):
        check_origen_del_cliente(self)

    @api.onchange("transaction_id")
    def _onchange_transaction_id(self):
        if self.transaction_id:
            self.product_id = self.transaction_id.product_id

    @api.model
    def _shrimp_origenes_del_cliente(self, cliente):
        """Lo que `cliente` puede mandar a empacar: sus compras confirmadas o
        recibidas y, si vende, sus propios lotes publicados. Lista de dicts
        {ref, label, kind, record} para el formulario del portal."""
        cliente = cliente.sudo()
        salida = []
        compras = self.env["shrimp.transaction"].sudo().search([
            ("buyer_partner_id", "=", cliente.id),
            ("state", "in", ("confirmed", "done")),
        ], order="create_date desc", limit=100)
        for tx in compras:
            salida.append({
                "ref": "t:%s" % tx.uuid_ref, "kind": "transaction", "record": tx,
                "label": "%s · %s · %s %s" % (
                    tx.name or "", tx.product_id.display_name or "",
                    "{:,.2f}".format(tx.transaction_qty or 0.0),
                    tx.product_id.uom_id.name or ""),
            })
        if cliente._shrimp_can_any("sell_products"):
            for prod in self.env["shrimp.product"].sudo().search([
                    ("seller_partner_id", "=", cliente.id),
                    ("state", "in", ("published", "sold")),
                    ("active", "=", True)], order="create_date desc", limit=100):
                salida.append({"ref": "p:%s" % prod.uuid_ref, "kind": "product",
                               "record": prod, "label": prod.display_name or ""})
        return salida

    @api.model
    def _shrimp_resolver_origen(self, cliente, token):
        """Valores (product_id, transaction_id, stock_lot_id) del origen
        elegido en el formulario, validando que sea del cliente. Vacío si no
        viene nada; ValidationError si no es suyo."""
        token = (token or "").strip()
        if not token:
            return {}
        tipo, _sep, ref = token.partition(":")
        if tipo == "t":
            tx = self.env["shrimp.transaction"].sudo().resolve_ref(ref)
            if not tx or tx.buyer_partner_id != cliente:
                raise ValidationError(_("La compra elegida no es tuya."))
            lote = self.env["shrimp.stock.lot"].sudo().search([
                ("owner_id", "=", cliente.id),
                ("origin_move_id.transaction_id", "=", tx.id)], limit=1)
            return {"transaction_id": tx.id, "product_id": tx.product_id.id,
                    "stock_lot_id": lote.id or False}
        if tipo == "p":
            prod = self.env["shrimp.product"].sudo().resolve_ref(ref)
            if not prod or prod.seller_partner_id != cliente:
                raise ValidationError(_("El lote elegido no es tuyo."))
            return {"product_id": prod.id}
        raise ValidationError(_("El origen del camarón no es válido."))

    @api.constrains("copacker_partner_id", "quantity_lb")
    def _check_lote_minimo(self):
        """El lote minimo que anuncia el maquilador se exige, no se sugiere.

        El maquilador lo declara en su perfil y el directorio lo muestra, pero
        nada impedia pedirle la mitad: la solicitud entraba en su bandeja y el
        rechazo tenia que ponerlo a mano, uno por uno.

        Solo se comprueba en las solicitudes DIRIGIDAS. Una solicitud abierta
        va a la bandeja de todos y cada planta decide si le sirve; ahi el
        minimo de uno no puede vetar la oferta de los demas.

        Tampoco al dejar una solicitud en lo adjudicado por una adjudicación
        parcial: la planta ya aceptó esas libras con su oferta.
        """
        if self.env.context.get("shrimp_copack_partial_award"):
            return
        for rec in self:
            maquilador = rec.copacker_partner_id
            if not maquilador:
                continue
            minimo = maquilador.pack_lote_minimo_lb or 0.0
            if minimo and rec.quantity_lb and rec.quantity_lb < minimo:
                raise ValidationError(_(
                    "«%(planta)s» no toma trabajos de menos de %(minimo).2f lb y "
                    "usted pide %(pedido).2f lb. Suba la cantidad o publique la "
                    "solicitud abierta para que oferten otras plantas.")
                    % {"planta": maquilador.name or "",
                       "minimo": minimo, "pedido": rec.quantity_lb})

    def _cpk_portal_url(self, partner=None):
        self.ensure_one()
        if partner and partner != self.client_partner_id:
            return "/copacker/requests/%s" % self.uuid_ref
        return "/marketplace/copacking/requests/%s" % self.uuid_ref

    def _shrimp_check_dirigida_operativa(self):
        """La solicitud dirigida va a una planta que pueda operar (perfil
        Maquilador aprobado). Se comprueba al publicar y no como constraint:
        las solicitudes viejas de una planta luego suspendida no se rompen."""
        for rec in self:
            maq = rec.copacker_partner_id
            if maq and not maq.shrimp_is_operational(role="maquilador"):
                raise ValidationError(_(
                    "«%s» no está operativa en la plataforma (su perfil de maquilador no "
                    "está aprobado): no se le puede dirigir una solicitud.") % (maq.name or ""))

    def _shrimp_new_expiry(self):
        """Vence lo primero entre «Necesita hasta» y hoy + días de vigencia."""
        self.ensure_one()
        hoy = fields.Date.context_today(self)
        tope = hoy + timedelta(days=copack_plazos.request_days(self.env))
        return min(tope, self.needed_to) if self.needed_to else tope

    def action_publish(self):
        for rec in self:
            if rec.state != "draft":
                raise ValidationError(_("Solo se publica una solicitud en borrador."))
            rec._shrimp_check_dirigida_operativa()
            rec.write({"state": "published", "published_date": fields.Datetime.now(),
                       "expires_on": rec._shrimp_new_expiry(),
                       "expiry_reminder_sent": False})
            if rec.copacker_partner_id:
                rec._cpk_notify(
                    rec.copacker_partner_id,
                    _("Te pidieron servicio de empaque: %s") % rec.name,
                    _("%(cli)s te pide empacar %(lb)s lb (%(desde)s a %(hasta)s). Oferta antes "
                      "del %(vence)s: ese día la solicitud vence.") % rec._cpk_fmt(),
                    url=rec._cpk_portal_url(rec.copacker_partner_id))

    def _cpk_fmt(self):
        self.ensure_one()
        return {
            "cli": self.client_partner_id.name or "", "ref": self.name or "",
            "lb": "{:,.2f}".format(self.quantity_lb or 0.0),
            "desde": self.needed_from.strftime("%d/%m/%Y") if self.needed_from else "",
            "hasta": self.needed_to.strftime("%d/%m/%Y") if self.needed_to else "",
            "vence": self.expires_on.strftime("%d/%m/%Y") if self.expires_on else "",
        }

    def shrimp_cancel_problem(self, actor=None):
        """'' si `actor` (el cliente; None = la plataforma) puede cancelar la
        solicitud ahora; si no, por qué. UNA regla para el portal y la API
        (antes la API solo dejaba borrador/publicada y el portal también
        adjudicada: el mismo acto con dos reglas).

        * borrador, publicada o vencida: se cancela y las ofertas vivas se
          descartan;
        * adjudicada: se cancela con su orden, con las mismas reglas que
          cancelar la orden como cliente (antes de empacar; ver
          shrimp.copack.order.shrimp_cancel_problem);
        * cerrada o cancelada: ya no.
        """
        self.ensure_one()
        if actor is not None and actor != self.client_partner_id:
            return _("La solicitud la cancela el cliente que la publicó.")
        if self.state in ("done", "cancelled"):
            return _("Una solicitud cerrada o cancelada ya no se cancela.")
        orden = self.order_id
        if self.state == "assigned" and orden and orden.state != "cancelled":
            return orden.shrimp_cancel_problem(actor=actor if actor is not None else None,
                                               role="client" if actor is not None else None)
        return ""

    def action_cancel(self, actor=None, reason=None):
        """Cancela la solicitud y, si ya habia orden, la arrastra con ella.

        Una sola regla (shrimp_cancel_problem) para el portal, la API y el
        backoffice. La orden adjudicada se cancela como si la cancelara el
        cliente: antes de empacar sí; empacada o firmada no (hay trabajo
        hecho; si algo no cuadra, el camino es el acta). Con el camarón ya en
        planta la devolución física la acuerdan las partes.
        """
        for rec in self:
            problema = rec.shrimp_cancel_problem(actor=actor)
            if problema:
                if actor is not None and actor != rec.client_partner_id:
                    raise AccessError(problema)
                raise ValidationError(problema)
            orden = rec.order_id
            if orden and orden.state != "cancelled":
                orden.with_context(shrimp_copack_from_request=True)._shrimp_do_cancel(
                    "client" if actor is not None else "platform",
                    reason or _("Cancelada junto con la solicitud %s.") % rec.name,
                    reopen=False)
            rec.state = "cancelled"
            rec.offer_ids.filtered(lambda o: o.state == "sent").write(
                {"state": "rejected", "closed_reason": "request_cancelled"})
        return True

    # ------------------------------------------------------------------
    # Reapertura (D-14): el maquilador cancela y la solicitud vuelve a salir
    # ------------------------------------------------------------------
    def _shrimp_reopen(self, motivo, excluir=None):
        """La orden adjudicada se canceló por la planta: la solicitud vuelve a
        «publicada» para las demás. Las ofertas que se descartaron solo por la
        adjudicación (y siguen en su ventana de disponibilidad) vuelven a estar
        sobre la mesa; la de la planta que canceló, no. Si la ventana de la
        solicitud ya pasó, se da por vencida."""
        hoy = fields.Date.context_today(self)
        Offer = self.env["shrimp.copack.offer"]
        for rec in self:
            if rec.state != "assigned":
                continue
            vals = {"order_id": False, "expiry_reminder_sent": False,
                    "published_date": fields.Datetime.now()}
            dirigida_fuera = excluir and rec.copacker_partner_id == excluir
            if dirigida_fuera:
                # Estaba dirigida a la planta que cancela: se abre a todas.
                vals["copacker_partner_id"] = False
            rec.write(vals)
            rec.expires_on = rec._shrimp_new_expiry()
            if rec.needed_to and rec.needed_to < hoy:
                rec.state = "expired"
                rec._cpk_notify(
                    rec.client_partner_id, _("Solicitud %s vencida") % rec.name,
                    _("La planta canceló la orden de tu solicitud %(ref)s (%(m)s) y la ventana "
                      "de fechas ya pasó, así que la solicitud quedó vencida. Si todavía "
                      "necesitas empacar, publícala de nuevo.") % {"ref": rec.name, "m": motivo})
                continue
            rec.state = "published"
            revivir = rec.offer_ids.filtered(
                lambda o: o.state == "rejected" and o.closed_reason == "award"
                and o.copacker_partner_id != excluir
                and (not o.available_to or o.available_to >= hoy)
                and o.copacker_partner_id.shrimp_is_operational(role="maquilador"))
            for oferta in revivir:
                oferta.write({"state": "sent", "closed_reason": False,
                              "expires_on": oferta._shrimp_new_expiry()})
                oferta._cpk_notify(
                    oferta.copacker_partner_id,
                    _("La solicitud %s vuelve a estar abierta") % rec.name,
                    _("La planta adjudicada canceló. Tu oferta para %(ref)s vuelve a estar "
                      "sobre la mesa hasta el %(v)s; puedes actualizarla o retirarla.") % {
                        "ref": rec.name,
                        "v": oferta.expires_on.strftime("%d/%m/%Y") if oferta.expires_on else ""},
                    url=rec._cpk_portal_url(oferta.copacker_partner_id))
            rec.message_post(body=_("Solicitud reabierta a las demás plantas: %s") % motivo)
            rec._cpk_notify(
                rec.client_partner_id, _("Solicitud %s reabierta") % rec.name,
                _("La planta canceló la orden de tu solicitud %(ref)s. Motivo: %(m)s. La "
                  "solicitud vuelve a estar publicada para las demás plantas hasta el %(v)s"
                  "%(of)s.") % {
                    "ref": rec.name, "m": motivo,
                    "v": rec.expires_on.strftime("%d/%m/%Y") if rec.expires_on else "",
                    "of": (_("; recuperaste %s oferta(s) que ya tenías") % len(revivir))
                    if revivir else ""})
        return True

    # ------------------------------------------------------------------
    # Adjudicación parcial (D-47): el remanente se publica solo
    # ------------------------------------------------------------------
    def _shrimp_publish_remainder(self, adjudicadas):
        """La oferta aceptada cubre menos libras de las pedidas: la solicitud
        queda en lo adjudicado y lo que falta sale como solicitud NUEVA,
        abierta a todas las plantas y enlazada a la original."""
        self.ensure_one()
        resto = round((self.quantity_lb or 0.0) - (adjudicadas or 0.0), 2)
        if resto <= 0.005:
            return self.browse()
        hija = self.with_context(shrimp_copack_partial_award=True).copy({
            "quantity_lb": resto,
            "copacker_partner_id": False,
            "parent_request_id": self.id,
            "state": "draft",
        })
        hija.action_publish()
        self.with_context(shrimp_copack_partial_award=True).write({"quantity_lb": adjudicadas})
        self.message_post(body=_(
            "Adjudicación parcial: %(adj)s lb adjudicadas; las %(resto)s lb restantes se "
            "publicaron como la solicitud %(hija)s, abierta a todas las plantas.") % {
                "adj": "{:,.2f}".format(adjudicadas), "resto": "{:,.2f}".format(resto),
                "hija": hija.name})
        self._cpk_notify(
            self.client_partner_id, _("Remanente publicado: %s") % hija.name,
            _("La planta que elegiste para %(ref)s toma %(adj)s lb. Las %(resto)s lb que "
              "faltan se publicaron automáticamente como la solicitud %(hija)s, abierta a "
              "todas las plantas hasta el %(v)s.") % {
                "ref": self.name, "adj": "{:,.2f}".format(adjudicadas),
                "resto": "{:,.2f}".format(resto), "hija": hija.name,
                "v": hija.expires_on.strftime("%d/%m/%Y") if hija.expires_on else ""},
            url=hija._cpk_portal_url())
        return hija

    # ------------------------------------------------------------------
    # Vencimiento (D-47)
    # ------------------------------------------------------------------
    @api.model
    def _cron_copack_expiry(self, limit=500):
        """Cada hora: vence las solicitudes y ofertas fuera de plazo y avisa
        al cliente de las solicitudes que vencen mañana con ofertas por mirar."""
        hoy = fields.Date.context_today(self)
        vencidas = self.sudo().search([
            ("state", "=", "published"), ("expires_on", "!=", False),
            ("expires_on", "<", hoy)], limit=limit)
        for rec in vencidas:
            rec._shrimp_expire()
        avisar = self.sudo().search([
            ("state", "=", "published"), ("expiry_reminder_sent", "=", False),
            ("expires_on", "=", hoy + timedelta(days=1))], limit=limit)
        for rec in avisar:
            vivas = rec.offer_ids.filtered(lambda o: o.state == "sent")
            rec.expiry_reminder_sent = True
            if vivas:
                rec._cpk_notify(
                    rec.client_partner_id, _("Tu solicitud %s vence mañana") % rec.name,
                    _("Tu solicitud %(ref)s vence mañana y tiene %(n)s oferta(s) sin "
                      "responder. Acepta una o recházalas; si no, la solicitud vence y las "
                      "ofertas se descartan.") % {"ref": rec.name, "n": len(vivas)})
        self.env["shrimp.copack.offer"]._cron_offer_expiry(limit=limit)
        return True

    def _shrimp_expire(self):
        for rec in self:
            if rec.state != "published":
                continue
            vivas = rec.offer_ids.filtered(lambda o: o.state == "sent")
            vivas.write({"state": "expired", "closed_reason": "request_expired"})
            rec.state = "expired"
            rec._cpk_notify(
                rec.client_partner_id, _("Solicitud %s vencida") % rec.name,
                _("Tu solicitud %(ref)s venció sin adjudicarse%(of)s. Si todavía necesitas "
                  "empacar, publícala de nuevo desde la solicitud.") % {
                    "ref": rec.name,
                    "of": (_(" (se descartaron %s oferta(s))") % len(vivas)) if vivas else ""})
            for oferta in vivas:
                oferta._cpk_notify(
                    oferta.copacker_partner_id, _("Solicitud %s vencida") % rec.name,
                    _("La solicitud %s venció sin que el cliente la adjudicara: tu oferta "
                      "queda descartada.") % rec.name,
                    url=rec._cpk_portal_url(oferta.copacker_partner_id))
        return True
