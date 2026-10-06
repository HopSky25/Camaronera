from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


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

    @api.constrains("copacker_partner_id", "request_id")
    def _check_maquilador(self):
        for rec in self:
            if not rec.copacker_partner_id._shrimp_can_any("provide_copack"):
                raise ValidationError(_(
                    "Solo un maquilador oferta servicio de empaque. «%s» no lo es.")
                    % (rec.copacker_partner_id.name or ""))
            cliente = rec.request_id.client_partner_id
            if cliente and cliente.commercial_partner_id \
                    == rec.copacker_partner_id.commercial_partner_id:
                raise ValidationError(_(
                    "Una empresa no se contrata a sí misma: el empaque de tu propio "
                    "camarón en tu planta es una operación interna, no un servicio de "
                    "la plataforma (no hay contraparte que firme el acta ni comisión "
                    "que cobrar)."))

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

    def write(self, vals):
        """Editar una oferta retirada la vuelve a poner sobre la mesa.

        Sin esto, retirar seria una puerta de un solo sentido: la clave unica
        (solicitud, maquilador) impide crear otra oferta, asi que un
        maquilador que se retiro por error —o que al dia siguiente libera
        camara y si puede tomar el trabajo— se quedaba fuera de esa solicitud
        para siempre. La via de rectificar en este modelo ya era editar, no
        duplicar; esto solo la mantiene abierta despues de un retiro.

        Se reactiva unicamente si cambian las condiciones comerciales (es lo
        que convierte el gesto en una oferta nueva) y si la solicitud sigue
        publicada: sobre una ya adjudicada o cancelada no hay nada que ofertar.
        """
        terminos = {"rate_per_lb", "capacity_lb", "available_from", "available_to"}
        revivir = self.browse()
        if "state" not in vals and terminos.intersection(vals):
            revivir = self.filtered(
                lambda o: o.state == "withdrawn" and o.request_id.state == "published")
        res = super().write(vals)
        if revivir:
            # Este write lleva "state", asi que no vuelve a entrar por aqui.
            revivir.write({"state": "sent"})
        return res

    def action_withdraw(self, actor=None):
        """El maquilador retira su oferta.

        `withdrawn` estaba en el selector y no lo escribia nadie: una planta
        que se quedaba sin camara, sin personal o sin habilitacion no tenia
        forma de bajar su oferta, y el cliente podia adjudicarle un trabajo
        que ya sabia que no iba a poder hacer.

        La retira quien la hizo y solo quien la hizo: `actor` se comprueba
        aqui porque el controlador trabaja en sudo, igual que al aceptar.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.copacker_partner_id:
            raise AccessError(_(
                "Una oferta la retira el maquilador que la presentó."))
        if self.state == "accepted":
            raise ValidationError(_(
                "Esta oferta ya fue adjudicada: el trabajo existe como orden y "
                "no se deshace retirando la oferta. Hay que cancelar la orden."))
        if self.state != "sent":
            raise ValidationError(_("Solo se retira una oferta que sigue viva."))
        self.state = "withdrawn"
        self.message_post(body=_("Oferta retirada por el maquilador."))
        return True

    def action_accept(self, actor=None):
        """El cliente acepta: nace la orden y las demas ofertas se descartan.

        `actor` se comprueba siempre. Hasta ahora lo unico que impedia que un
        maquilador se autoadjudicara el trabajo era un permiso de OTRO modelo
        (el portal no podia crear ordenes). Apoyar una regla de negocio en un
        permiso de otra tabla es apoyarla en nada.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.request_id.client_partner_id:
            raise AccessError(_(
                "La oferta la acepta quien pidio el servicio, no otra parte."))
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
            "transaction_id": solicitud.transaction_id.id or False,
            "stock_lot_id": solicitud.stock_lot_id.id or False,
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
