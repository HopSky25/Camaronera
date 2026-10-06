from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpCopackAcceptance(models.Model):
    """La firma de una de las partes sobre el acta de empaque.

    Hay exactamente dos por orden y por ronda: la del cliente y la del
    maquilador. El acta queda cerrada cuando las dos aceptan, y en disputa en
    cuanto una rechaza.

    Se guarda como registro y no como dos casillas en la orden porque hace
    falta el porque, la hora y quien lo hizo: eso es la prueba de a que se
    comprometio cada uno con las libras que declaro.

    Dos cosas que este modelo tiene que sostener:

    * Un acta en disputa se puede reabrir para rectificar el empaque. Las
      firmas de la ronda anterior NO se borran: se archivan (`active = False`)
      con quien, cuando y por que se reabrio. Borrarlas dejaria la orden sin
      rastro de la discusion, que es justo el papel que en una maquila se
      guarda para el dia que alguien reclame.
    * Cada firma congela las cifras sobre las que se firmo. Sin eso, cambiar
      `packed_lb` despues rehacia los calculos y el acta seguia diciendo
      "conforme" sobre unos numeros que nadie acepto nunca.
    """

    _name = "shrimp.copack.acceptance"
    _inherit = ["shrimp.uuid.mixin", "shrimp.signoff.mixin"]
    _description = "Firma del acta de empaque"
    _SIGNOFF_ARCHIVED_MSG = ("Esta firma es de un acta anterior que ya se reabrió; "
                             "firme la del acta vigente.")
    _order = "order_id, ronda desc, role"
    _SIGNOFF_PARENT_FIELD = "order_id"
    _SIGNOFF_SNAPSHOT_FIELDS = (
        "decision", "reason", "decided_by_uid", "decided_at",
        "signed_received_lb", "signed_packed_lb", "signed_difference_lb",
        "signed_difference_pct", "signed_tolerance_pct")

    order_id = fields.Many2one(
        "shrimp.copack.order", string="Orden", required=True,
        ondelete="cascade", index=True)
    # La ronda distingue las firmas de un acta reabierta de las anteriores.
    # Va dentro de la clave unica porque, si no, archivar no bastaria: un
    # indice UNIQUE de Postgres no sabe nada del campo `active` y bloquearia
    # la segunda firma del mismo rol.
    ronda = fields.Integer(
        string="Ronda del acta", default=1, required=True, index=True,
        readonly=True,
        help="1 es el acta original. Cada reapertura por disputa abre una ronda nueva.")
    active = fields.Boolean(
        string="Vigente", default=True,
        help="Las firmas de rondas anteriores quedan archivadas, no borradas.")
    role = fields.Selection(
        [("client", "Cliente"), ("copacker", "Maquilador")],
        string="Parte", required=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Quien firma", required=True,
        ondelete="restrict", index=True)

    decision = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("accepted", "Conforme"),
            ("rejected", "No conforme"),
        ],
        string="Decisión", default="pending", required=True, index=True)
    reason = fields.Text(string="Motivo")
    decided_by_uid = fields.Many2one("res.users", string="Firmado por", readonly=True)
    decided_at = fields.Datetime(string="Fecha de firma", readonly=True)

    # --- foto de las cifras firmadas ---
    # Se copian de la orden en el momento de firmar. A partir de ahi son
    # historia: aunque la orden se reabra y el maquilador corrija las libras,
    # esta fila sigue diciendo exactamente sobre que se pronuncio esta parte.
    signed_received_lb = fields.Float(
        string="Libras recibidas firmadas", digits=(16, 2), readonly=True)
    signed_packed_lb = fields.Float(
        string="Libras empacadas firmadas", digits=(16, 2), readonly=True)
    signed_difference_lb = fields.Float(
        string="Diferencia firmada (lb)", digits=(16, 2), readonly=True)
    # Se congela tambien el porcentaje y la tolerancia porque el "cuadra/no
    # cuadra" sale de comparar los dos: guardar solo las libras obligaria a
    # recalcularlo con la tolerancia de hoy, que puede no ser la de entonces.
    signed_difference_pct = fields.Float(
        string="Diferencia firmada (%)", digits=(5, 2), readonly=True)
    signed_tolerance_pct = fields.Float(
        string="Tolerancia firmada (%)", digits=(5, 2), readonly=True)

    # --- traza del archivado ---
    archived_at = fields.Datetime(string="Archivada el", readonly=True)
    archived_by_uid = fields.Many2one(
        "res.users", string="Archivada por", readonly=True)
    archive_reason = fields.Text(
        string="Motivo de la reapertura", readonly=True,
        help="Por qué se reabrió el acta que contenía esta firma.")

    _uniq_firma = models.Constraint(
        "UNIQUE(order_id, role, ronda)",
        "Cada parte firma una sola vez en cada ronda del acta.")

    @api.constrains("ronda")
    def _check_ronda(self):
        for rec in self:
            if rec.ronda < 1:
                raise ValidationError(_("La ronda del acta empieza en 1."))

    def _firmar(self, decision, motivo=None, actor=None):
        """`actor` es quien dice firmar, y hay que comprobarlo SIEMPRE.

        El controlador resuelve el acta en sudo —lo necesita para leer las dos
        filas— y sin este control quedaba en manos del ACL que una parte no
        firmara por la otra. Un acta que el maquilador puede firmar en nombre
        del cliente no es un acta: es justo lo que este registro existe para
        impedir.
        """
        self.ensure_one()
        # Quién firma, que la ronda siga vigente y que el «no conforme» traiga
        # motivo: las mismas reglas que las otras firmas de dos partes
        # (shrimp.signoff.mixin).
        self._signoff_check_actor(actor)
        self._signoff_check_open()
        self._signoff_check_reason(decision, motivo)
        orden = self.order_id
        antes = self._signoff_snapshot()
        self.write({
            "decision": decision,
            "reason": motivo,
            "decided_by_uid": self.env.user.id,
            "decided_at": fields.Datetime.now(),
            # Foto de las cifras en el instante de la firma.
            "signed_received_lb": orden.received_lb,
            "signed_packed_lb": orden.packed_lb,
            "signed_difference_lb": orden.difference_lb,
            "signed_difference_pct": orden.difference_pct,
            "signed_tolerance_pct": orden.tolerance_pct,
        })
        self._signoff_log("decision", antes, reason=motivo)
        orden._evaluar_acta()

    def _archivar(self, motivo):
        """Retira estas firmas de la ronda vigente dejando constancia.

        Interno: lo llama `shrimp.copack.order.action_reabrir_acta`, que es
        quien ya comprobo quien pide la reapertura y por que.
        """
        return self._signoff_archive(motivo)

    def action_accept(self, actor=None):
        self._firmar("accepted", actor=actor)

    def action_reject(self, motivo=None, actor=None):
        self._firmar("rejected", motivo or self.reason, actor=actor)

    # ==================================================================
    # Deshacer la firma (shrimp.signoff.mixin)
    # ==================================================================
    # Hasta cuándo: mientras el acta de ESTA ronda siga abierta o en disputa.
    # La firma surte efecto en el acto (una «no conforme» pone el acta en
    # disputa), pero la disputa no tiene efectos irreversibles: no cobra, no
    # mueve stock, solo bloquea el cobro. Lo irreversible es el cierre: con
    # las dos conformes la orden pasa a «firmada» y se registra la comisión de
    # la plataforma; a partir de ahí (y de «cerrada»/liquidada) no se deshace.
    # Tampoco se deshace una firma de una ronda reabierta (queda archivada).
    def _signoff_undo_blocker(self):
        bloqueo = super()._signoff_undo_blocker()
        if bloqueo:
            return bloqueo
        orden = self.order_id
        if orden.state != "packed" or orden.acceptance_state not in ("open", "disputed"):
            return _("El acta ya está cerrada (o la orden ya no está a la firma): "
                     "la firma ya surtió efecto y no se puede deshacer.")
        return False

    def _signoff_undo_until(self):
        return _("mientras el acta no quede cerrada con las dos firmas conformes "
                 "ni se reabra")

    def _signoff_after_undo(self, evento):
        """Sin «no conforme» vigente, la disputa ya no tiene motivo: el acta
        vuelve a estar abierta a la firma."""
        orden = self.order_id.sudo()
        decisiones = orden.acceptance_ids.filtered("active").mapped("decision")
        if orden.acceptance_state == "disputed" and "rejected" not in decisiones:
            orden.acceptance_state = "open"
        return True

    def _signoff_portal_url(self, partner):
        return "/marketplace/copacking/orders/%s" % self.order_id.uuid_ref
