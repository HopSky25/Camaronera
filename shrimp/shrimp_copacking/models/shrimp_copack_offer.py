from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from . import copack_plazos


class ShrimpCopackOffer(models.Model):
    """Lo que un maquilador responde a una solicitud.

    Tres datos y nada mas: a cuanto la libra, cuando puede, y cuantas libras
    aguanta. El cliente compara y elige.
    """

    _name = "shrimp.copack.offer"
    _description = "Oferta de servicio de empaque"
    _inherit = ["mail.thread", "shrimp.uuid.mixin", "shrimp.copack.notify.mixin"]
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
            # D-47: la oferta que nadie contesta vence sola.
            ("expired", "Vencida"),
            # D-14: la planta adjudicada canceló la orden.
            ("cancelled", "Cancelada"),
        ],
        string="Estado", default="sent", required=True, index=True, tracking=True)

    # Por qué dejó de estar viva: distingue la oferta descartada por la
    # adjudicación a otra planta (que vuelve si la orden se cancela y la
    # solicitud se reabre) de la que el cliente rechazó o la que venció.
    closed_reason = fields.Selection(
        [
            ("award", "Se adjudicó otra oferta"),
            ("client", "Rechazada por el cliente"),
            ("request_cancelled", "Solicitud cancelada"),
            ("request_expired", "Solicitud vencida"),
            ("expired", "Venció la oferta"),
            ("order_cancelled", "Orden cancelada"),
        ],
        string="Motivo del cierre", readonly=True, copy=False)
    reject_reason = fields.Text(
        string="Motivo del rechazo", readonly=True, copy=False,
        help="Lo que el cliente explicó al rechazar la oferta (opcional).")
    expires_on = fields.Date(
        string="Vence el", readonly=True, copy=False, index=True,
        help="Lo primero entre «Disponible hasta» y los días de vigencia de las ofertas "
             "(Ajustes › Empaque). Vencida, ya no se puede aceptar.")

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

    def _cpk_portal_url(self, partner=None):
        self.ensure_one()
        if partner and partner == self.copacker_partner_id:
            return "/copacker/requests/%s" % self.request_id.uuid_ref
        return "/marketplace/copacking/requests/%s#ofertas" % self.request_id.uuid_ref

    def _shrimp_new_expiry(self):
        self.ensure_one()
        hoy = fields.Date.context_today(self)
        tope = hoy + timedelta(days=copack_plazos.offer_days(self.env))
        return min(tope, self.available_to) if self.available_to else tope

    def _shrimp_check_operativa(self):
        """D-34: una planta pendiente o rechazada no oferta ni se le adjudica,
        venga por el portal, por la API o por el backoffice (la regla vive en
        el modelo y no en cada puerta)."""
        for rec in self:
            maq = rec.copacker_partner_id
            if maq and not maq.shrimp_is_operational(role="maquilador"):
                raise ValidationError(_(
                    "«%s» no está operativa: su perfil de maquilador está pendiente de "
                    "aprobación o fue rechazado, y no puede ofertar ni recibir trabajos.")
                    % (maq.name or ""))

    @api.model_create_multi
    def create(self, vals_list):
        ofertas = super().create(vals_list)
        ofertas._shrimp_check_operativa()
        for oferta in ofertas:
            if oferta.state == "sent":
                oferta.expires_on = oferta._shrimp_new_expiry()
                oferta._cpk_notify_received()
        return ofertas

    def _cpk_notify_received(self):
        self.ensure_one()
        self._cpk_notify(
            self.client_partner_id,
            _("Nueva oferta para tu solicitud %s") % self.request_id.name,
            _("%(maq)s oferta empacar %(lb)s lb a $ %(t)s / lb (disponible del %(d)s al "
              "%(h)s). La oferta vence el %(v)s.") % {
                "maq": self.copacker_partner_id.name or "",
                "lb": "{:,.2f}".format(self.capacity_lb or 0.0),
                "t": round(self.rate_per_lb or 0.0, 3),
                "d": self.available_from.strftime("%d/%m/%Y") if self.available_from else "",
                "h": self.available_to.strftime("%d/%m/%Y") if self.available_to else "",
                "v": self.expires_on.strftime("%d/%m/%Y") if self.expires_on else ""},
            url=self._cpk_portal_url(self.client_partner_id))

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
        if "state" not in vals and terminos.intersection(vals) and self.filtered(
                lambda o: o.state == "cancelled"):
            raise ValidationError(_(
                "Cancelaste la orden de esta solicitud: ya no puedes volver a ofertar sobre ella."))
        revivir = self.browse()
        if "state" not in vals and terminos.intersection(vals):
            # D-47: también la vencida y la que el cliente rechazó: cambiar
            # las condiciones es hacer una oferta nueva (la clave única impide
            # crear otra). La descartada por adjudicar otra no: esa solicitud
            # ya no está publicada.
            revivir = self.filtered(
                lambda o: (o.state in ("withdrawn", "expired")
                           or (o.state == "rejected" and o.closed_reason == "client"))
                and o.request_id.state == "published")
        res = super().write(vals)
        if terminos.intersection(vals):
            vivas = self.filtered(lambda o: o.state == "sent") - revivir
            for oferta in vivas:
                oferta.expires_on = oferta._shrimp_new_expiry()
        if revivir:
            revivir._shrimp_check_operativa()
            # Este write lleva "state", asi que no vuelve a entrar por aqui.
            revivir.write({"state": "sent", "closed_reason": False, "reject_reason": False})
            for oferta in revivir:
                oferta.expires_on = oferta._shrimp_new_expiry()
                oferta._cpk_notify_received()
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
        actor = actor or self.env.user.shrimp_actor_partner_id
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

    def action_reject(self, actor=None, reason=None):
        """D-47: el cliente rechaza una oferta concreta (antes solo salían de
        «enviada» al adjudicar otra o al cancelar la solicitud). La planta lo
        sabe y, si quiere, mejora su oferta (editarla la vuelve a enviar)."""
        actor = actor or self.env.user.shrimp_actor_partner_id
        for rec in self:
            if actor != rec.request_id.client_partner_id:
                raise AccessError(_("La oferta la rechaza quien pidió el servicio."))
            if rec.state != "sent":
                raise ValidationError(_("Solo se rechaza una oferta enviada."))
            motivo = (reason or "").strip()
            rec.write({"state": "rejected", "closed_reason": "client",
                       "reject_reason": motivo or False})
            rec._cpk_notify(
                rec.copacker_partner_id,
                _("Tu oferta para %s fue rechazada") % rec.request_id.name,
                _("El cliente rechazó tu oferta para la solicitud %(ref)s.%(m)s Mientras la "
                  "solicitud siga publicada puedes mejorarla: al cambiar la tarifa, las libras "
                  "o las fechas se vuelve a enviar.") % {
                    "ref": rec.request_id.name,
                    "m": (" " + _("Motivo: %s.") % motivo) if motivo else ""},
                url=rec._cpk_portal_url(rec.copacker_partner_id))
        return True

    @api.model
    def _cron_offer_expiry(self, limit=500):
        hoy = fields.Date.context_today(self)
        vencidas = self.sudo().search([
            ("state", "=", "sent"), ("expires_on", "!=", False),
            ("expires_on", "<", hoy)], limit=limit)
        for rec in vencidas:
            rec.write({"state": "expired", "closed_reason": "expired"})
            rec._cpk_notify(
                rec.copacker_partner_id, _("Tu oferta para %s venció") % rec.request_id.name,
                _("Tu oferta para la solicitud %s venció sin respuesta del cliente. Si la "
                  "solicitud sigue publicada, puedes renovarla actualizando sus condiciones.")
                % rec.request_id.name,
                url=rec._cpk_portal_url(rec.copacker_partner_id))
        return vencidas

    def action_accept(self, actor=None):
        """El cliente acepta: nace la orden y las demas ofertas se descartan.

        `actor` se comprueba siempre. Hasta ahora lo unico que impedia que un
        maquilador se autoadjudicara el trabajo era un permiso de OTRO modelo
        (el portal no podia crear ordenes). Apoyar una regla de negocio en un
        permiso de otra tabla es apoyarla en nada.

        D-47: una oferta vencida ya no se acepta, la planta tiene que estar
        operativa y, si la oferta cubre menos libras de las pedidas, el
        remanente se publica solo como una solicitud nueva enlazada.
        """
        self.ensure_one()
        actor = actor or self.env.user.shrimp_actor_partner_id
        if actor != self.request_id.client_partner_id:
            raise AccessError(_(
                "La oferta la acepta quien pidio el servicio, no otra parte."))
        if self.state != "sent":
            raise ValidationError(_("Solo se acepta una oferta enviada."))
        solicitud = self.request_id
        if solicitud.state != "published":
            raise ValidationError(_(
                "La solicitud tiene que estar publicada para adjudicarla."))
        hoy = fields.Date.context_today(self)
        if self.expires_on and self.expires_on < hoy:
            raise ValidationError(_(
                "Esta oferta venció el %s: la planta ya no se comprometió a esas fechas. "
                "Pídele que la renueve.") % self.expires_on.strftime("%d/%m/%Y"))
        self._shrimp_check_operativa()

        adjudicadas = min(self.capacity_lb, solicitud.quantity_lb)
        orden = self.env["shrimp.copack.order"].create({
            "request_id": solicitud.id,
            "offer_id": self.id,
            "client_partner_id": solicitud.client_partner_id.id,
            "copacker_partner_id": self.copacker_partner_id.id,
            "product_id": solicitud.product_id.id or False,
            "transaction_id": solicitud.transaction_id.id or False,
            "stock_lot_id": solicitud.stock_lot_id.id or False,
            "agreed_qty_lb": adjudicadas,
            "rate_per_lb": self.rate_per_lb,
            "currency_id": self.currency_id.id,
            "supplies_notes": solicitud.supplies_notes,
        })
        self.state = "accepted"
        (solicitud.offer_ids - self).filtered(
            lambda o: o.state == "sent").write({"state": "rejected", "closed_reason": "award"})
        solicitud.write({"state": "assigned", "order_id": orden.id})
        if adjudicadas < (solicitud.quantity_lb or 0.0) - 0.005:
            solicitud._shrimp_publish_remainder(adjudicadas)
        orden._cpk_notify(
            self.copacker_partner_id, _("Te adjudicaron el trabajo %s") % orden.name,
            _("%(cli)s aceptó tu oferta: %(lb)s lb a $ %(t)s / lb. Registra la recepción "
              "cuando llegue el camarón a tu planta.") % {
                "cli": solicitud.client_partner_id.name or "",
                "lb": "{:,.2f}".format(adjudicadas), "t": round(self.rate_per_lb or 0.0, 3)})
        return orden
