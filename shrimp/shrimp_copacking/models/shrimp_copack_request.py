from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

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
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
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
            ("cancelled", "Cancelada"),
        ],
        string="Estado", default="draft", required=True, index=True, tracking=True)

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
        """
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

    def action_publish(self):
        for rec in self:
            if rec.state != "draft":
                raise ValidationError(_("Solo se publica una solicitud en borrador."))
            rec.state = "published"

    def action_cancel(self):
        """Cancela la solicitud y, si ya habia orden, la arrastra con ella.

        Antes cancelar la solicitud no tocaba la orden adjudicada: la orden
        seguia viva, el maquilador podia registrar recepcion y empaque, y
        acababa facturandose un trabajo cuyo encargo el cliente habia anulado.

        La linea se traza en el empaque, porque es donde deja de haber marcha
        atras:

        * Antes de empacar (`confirmed`, `received`) la cancelacion baja en
          cascada. Ojo con `received`: el camaron ya esta fisicamente en la
          planta, asi que cancelar aqui cierra el expediente pero la
          devolucion la acuerdan las partes fuera de la plataforma.
        * Empacada o firmada, no. Ahi hay trabajo hecho, insumos gastados y
          horas de linea: deshacerlo con un boton del cliente seria dejar al
          maquilador sin cobro por una decision unilateral. Si hay algo que
          discutir, ese es el camino del acta, no el de la cancelacion.
        """
        for rec in self:
            if rec.state == "done":
                raise ValidationError(_("Una solicitud cerrada ya no se cancela."))
            orden = rec.order_id
            if orden and orden.state != "cancelled":
                if orden.state in ("packed", "signed", "closed"):
                    raise ValidationError(_(
                        "La orden «%(orden)s» ya se empacó: el trabajo está "
                        "hecho y no se deshace cancelando la solicitud. Si hay "
                        "un problema con el empaque, se plantea en el acta.")
                        % {"orden": orden.name or ""})
                orden.action_cancel()
            rec.state = "cancelled"
            rec.offer_ids.filtered(lambda o: o.state == "sent").write({"state": "rejected"})
