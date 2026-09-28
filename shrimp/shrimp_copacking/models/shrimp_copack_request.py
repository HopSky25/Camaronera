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

    def action_publish(self):
        for rec in self:
            if rec.state != "draft":
                raise ValidationError(_("Solo se publica una solicitud en borrador."))
            rec.state = "published"

    def action_cancel(self):
        for rec in self:
            if rec.state == "done":
                raise ValidationError(_("Una solicitud cerrada ya no se cancela."))
            rec.state = "cancelled"
            rec.offer_ids.filtered(lambda o: o.state == "sent").write({"state": "rejected"})
