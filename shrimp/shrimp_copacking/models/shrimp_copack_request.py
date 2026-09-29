from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


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
        [("entero", "Entero"), ("cola", "Cola"), ("valor_agregado", "Valor agregado")],
        string="Presentación", required=True, default="entero")

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
                    and rec.copacker_partner_id.shrimp_user_type != "maquilador"):
                raise ValidationError(_(
                    "Una solicitud de empaque se dirige a un maquilador. «%s» no lo es.")
                    % (rec.copacker_partner_id.name or ""))

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
            if rec.client_partner_id.shrimp_user_type not in ("camaronera", "empacadora"):
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

    @api.constrains("product_id", "client_partner_id")
    def _check_lote_del_cliente(self):
        """El lote que se manda a empacar tiene que ser del propio cliente.

        Si no se comprueba, se puede levantar una solicitud (y de ahi una
        orden) apuntando al lote de otro, y el paso de empaque aparece en el
        certificado de trazabilidad de un tercero que nunca piso esa planta.
        Ensuciar el dato de otro es peor que equivocarse en el propio.

        Nota: una empacadora cliente no puede figurar como vendedora de un
        `shrimp.product` (ese modelo solo admite semillero, laboratorio o
        camaronera), asi que en su caso el lote se deja vacio y el empaque no
        se enlaza a ninguna trazabilidad. Es lo correcto: no hay lote suyo que
        enlazar.

        La lectura va en sudo para poder dar este mensaje; sin ella, apuntar a
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
