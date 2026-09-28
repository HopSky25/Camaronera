from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpCopackOffer(models.Model):
    """Lo que un maquilador responde a una solicitud.

    Tres datos y nada mas: a cuanto la libra, cuando puede, y cuantas libras
    aguanta. El cliente compara y elige.
    """

    _name = "shrimp.copack.offer"
    _description = "Oferta de servicio de empaque"
    _inherit = ["mail.thread", "shrimp.uuid.mixin"]
    _order = "rate_per_lb, id"

    request_id = fields.Many2one(
        "shrimp.copack.request", string="Solicitud", required=True,
        ondelete="cascade", index=True)
    copacker_partner_id = fields.Many2one(
        "res.partner", string="Maquilador", required=True, ondelete="restrict",
        index=True, tracking=True)
    client_partner_id = fields.Many2one(
        related="request_id.client_partner_id", string="Cliente", store=True, index=True)
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)

    rate_per_lb = fields.Monetary(
        string="Tarifa por libra", required=True, currency_field="currency_id",
        tracking=True)
    capacity_lb = fields.Float(
        string="Libras que puede tomar", required=True, digits=(16, 2),
        help="Puede ser menos de lo pedido: el cliente decide si le sirve.")
    available_from = fields.Date(string="Disponible desde", required=True)
    available_to = fields.Date(string="Disponible hasta", required=True)

    estimated_total = fields.Monetary(
        string="Total estimado", compute="_compute_estimated_total", store=True,
        currency_field="currency_id")

    notes = fields.Text(string="Condiciones")

    state = fields.Selection(
        [
            ("sent", "Enviada"),
            ("accepted", "Aceptada"),
            ("rejected", "Descartada"),
            ("withdrawn", "Retirada"),
        ],
        string="Estado", default="sent", required=True, index=True, tracking=True)

    _uniq_oferta = models.Constraint(
        "UNIQUE(request_id, copacker_partner_id)",
        "Cada maquilador hace una sola oferta por solicitud; para cambiarla, se edita.")

    @api.depends("rate_per_lb", "capacity_lb")
    def _compute_estimated_total(self):
        for rec in self:
            rec.estimated_total = rec.rate_per_lb * rec.capacity_lb

    @api.constrains("copacker_partner_id")
    def _check_maquilador(self):
        for rec in self:
            if rec.copacker_partner_id.shrimp_user_type != "maquilador":
                raise ValidationError(_(
                    "Solo un maquilador oferta servicio de empaque. «%s» no lo es.")
                    % (rec.copacker_partner_id.name or ""))

    @api.constrains("rate_per_lb", "capacity_lb")
    def _check_numeros(self):
        for rec in self:
            if rec.rate_per_lb <= 0:
                raise ValidationError(_("La tarifa por libra debe ser mayor que cero."))
            if rec.capacity_lb <= 0:
                raise ValidationError(_("Las libras ofertadas deben ser mayores que cero."))

    @api.constrains("available_from", "available_to")
    def _check_ventana(self):
        for rec in self:
            if rec.available_to < rec.available_from:
                raise ValidationError(_(
                    "La disponibilidad no puede terminar antes de empezar."))

    def action_accept(self):
        """El cliente acepta: nace la orden y las demas ofertas se descartan."""
        self.ensure_one()
        if self.state != "sent":
            raise ValidationError(_("Solo se acepta una oferta enviada."))
        solicitud = self.request_id
        if solicitud.state != "published":
            raise ValidationError(_(
                "La solicitud tiene que estar publicada para adjudicarla."))

        orden = self.env["shrimp.copack.order"].create({
            "request_id": solicitud.id,
            "offer_id": self.id,
            "client_partner_id": solicitud.client_partner_id.id,
            "copacker_partner_id": self.copacker_partner_id.id,
            "product_id": solicitud.product_id.id or False,
            "agreed_qty_lb": min(self.capacity_lb, solicitud.quantity_lb),
            "rate_per_lb": self.rate_per_lb,
            "currency_id": self.currency_id.id,
            "supplies_notes": solicitud.supplies_notes,
        })
        self.state = "accepted"
        (solicitud.offer_ids - self).filtered(
            lambda o: o.state == "sent").write({"state": "rejected"})
        solicitud.write({"state": "assigned", "order_id": orden.id})
        return orden
