from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ShrimpVerificationAcceptance(models.Model):
    """La postura de una de las partes frente al informe del verificador.

    Hay exactamente dos por verificación: la del comprador y la del vendedor.
    La compra se cierra cuando las dos están en "aceptada"; se cae si, con las
    dos decididas (o vencido el plazo), una quedó en "rechazada".

    Un rechazo NO tumba la compra en el acto: surte efecto cuando la ronda se
    cierra, es decir, cuando la otra parte también decide o cuando vence el
    plazo. Hasta entonces quien decidió puede deshacerlo («deshacer mi
    decisión», shrimp.signoff.mixin). Mientras tanto el stock sigue reservado,
    igual que durante toda la ronda.

    Se modela como registro y no como dos casillas en la compra porque hace
    falta guardar el porqué, la hora, si la decisión fue automática por
    vencimiento del plazo y, en el caso del comprador, el precio que propone.
    Eso es la prueba de a qué se comprometió cada uno.
    """

    _name = "shrimp.verification.acceptance"
    _inherit = ["shrimp.uuid.mixin", "shrimp.signoff.mixin"]
    _description = "Aceptación del informe de verificación"
    _order = "verification_id, role"
    _SIGNOFF_PARENT_FIELD = "verification_id"
    _SIGNOFF_SNAPSHOT_FIELDS = ("decision", "reason", "decided_at", "auto", "counter_price")

    verification_id = fields.Many2one(
        "shrimp.verification", string="Verificación", required=True,
        ondelete="cascade", index=True)
    transaction_id = fields.Many2one(
        related="verification_id.transaction_id", string="Compra", store=True, index=True)
    currency_id = fields.Many2one(related="verification_id.currency_id", readonly=True)

    role = fields.Selection(
        [("buyer", "Comprador"), ("seller", "Vendedor")],
        string="Parte", required=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Quien decide", required=True, ondelete="restrict", index=True)

    decision = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("accepted", "Aceptada"),
            ("counter", "Contraoferta"),
            ("rejected", "Rechazada"),
        ],
        string="Decisión", default="pending", required=True, index=True)
    # Sin tracking: este modelo no hereda mail.thread, así que Odoo
    # descartaba el parámetro con un WARNING y la decisión NO quedaba en
    # ningún chatter. Se creía auditada y no lo estaba. La huella real
    # de quién decidió y cuándo son decided_at y el mensaje que se
    # publica en la verificación, que sí es un mail.thread.

    reason = fields.Text(string="Motivo")
    decided_at = fields.Datetime(string="Fecha de la decisión", readonly=True)
    auto = fields.Boolean(
        string="Automática por vencimiento", readonly=True,
        help="Se dio por aceptada porque venció el plazo sin respuesta.")

    # ---- Contraoferta (solo la puede hacer el comprador) ----
    counter_price = fields.Monetary(
        string="Precio unitario propuesto", currency_field="currency_id")
    counter_total = fields.Monetary(
        string="Total propuesto", currency_field="currency_id",
        compute="_compute_counter_total", store=True)

    _uniq_role = models.Constraint(
        "UNIQUE(verification_id, role)",
        "Cada parte solo puede tener una postura por verificación.",
    )

    @api.depends("counter_price", "verification_id.transaction_id.transaction_qty")
    def _compute_counter_total(self):
        for rec in self:
            qty = rec.verification_id.transaction_id.transaction_qty or 0.0
            rec.counter_total = round((rec.counter_price or 0.0) * qty, 2)

    def name_get(self):
        etiquetas = dict(self._fields["role"]._description_selection(self.env))
        return [(r.id, "%s – %s" % (etiquetas.get(r.role, r.role), r.partner_id.name or ""))
                for r in self]

    # ==================================================================
    # Decisiones
    # ==================================================================
    # Campos que constituyen la firma: solo los escriben los métodos de abajo
    # (que comprueban quién firma). Un write() directo —por RPC o desde otro
    # código— no puede fijarlos: así nadie firma por la otra parte ni se
    # salta las validaciones de la contraoferta.
    _FIRMA_FIELDS = frozenset({"decision", "decided_at", "auto", "counter_price"})

    def write(self, vals):
        if self._FIRMA_FIELDS.intersection(vals) and not self.env.context.get("_shrimp_firma_ok"):
            raise AccessError(_(
                "La decisión sobre el informe solo se registra con las acciones "
                "Aceptar, Rechazar o Contraoferta de su titular."))
        return super().write(vals)

    def _check_actor(self, actor=None, auto=False):
        """`actor` es quien dice firmar, y se comprueba SIEMPRE.

        Mismo criterio que shrimp.harvest.confirmation._firmar y
        shrimp.copack.acceptance._firmar: el controlador resuelve la postura en
        sudo, así que sin esta comprobación una parte (o el verificador)
        podría firmar por la otra. Solo el sistema (vencimiento del plazo, en
        sudo) firma sin titular, y queda marcado como automático.
        """
        self.ensure_one()
        if auto:
            if not self.env.su:
                raise AccessError(_("Solo el sistema registra aceptaciones automáticas."))
            return
        # El titular de la fila, con la misma regla que las otras firmas de
        # dos partes (shrimp.signoff.mixin). Aquí la decisión sí se puede
        # rehacer mientras la ronda siga abierta (la contraoferta devuelve al
        # vendedor a «pendiente»), así que no se exige que esté sin decidir.
        self._signoff_check_actor(actor)
        if self.verification_id.acceptance_state != "waiting":
            raise UserError(_("La ronda de aceptación de este informe ya está cerrada."))

    def _sellar(self, decision, reason=None, auto=False, actor=None):
        self.ensure_one()
        self._check_actor(actor=actor, auto=auto)
        antes = self._signoff_snapshot()
        vals = {
            "decision": decision,
            "decided_at": fields.Datetime.now(),
            "auto": auto,
        }
        if reason is not None:
            vals["reason"] = reason
        self.with_context(_shrimp_firma_ok=True).write(vals)
        self._signoff_log("decision", antes, reason=self.reason, auto=auto)

    def action_accept(self, reason=None, auto=False, actor=None):
        for rec in self:
            rec._sellar("accepted", reason, auto, actor=actor)
        self.mapped("verification_id")._resolver_aceptacion()
        return True

    def action_reject(self, reason=None, actor=None):
        for rec in self:
            if not reason and not rec.reason:
                raise UserError(_(
                    "Explica por qué rechazas el informe. La otra parte tiene "
                    "derecho a saberlo y queda registrado."))
            rec._sellar("rejected", reason, actor=actor)
        self.mapped("verification_id")._resolver_aceptacion()
        return True

    def action_counter(self, price, reason=None, actor=None):
        """El comprador propone seguir con la compra a otro precio."""
        self.ensure_one()
        if self.role != "buyer":
            raise UserError(_("Solo el comprador puede proponer un precio ajustado."))
        self._check_actor(actor=actor)

        verificacion = self.verification_id
        cumple, _motivos = verificacion.cumple_lo_publicado()
        if cumple:
            # Sin esto, cualquier comprador regatearía después de una inspección
            # favorable y el vendedor quedaría rehén de su propia oferta.
            raise UserError(_(
                "El informe confirma lo que el anuncio ofrecía, así que no hay "
                "base para ajustar el precio. Puedes aceptar o rechazar."))

        precio = float(price or 0.0)
        actual = verificacion.transaction_id.price_unit or 0.0
        if precio <= 0:
            raise UserError(_("El precio propuesto debe ser mayor que cero."))
        if actual and precio >= actual:
            raise UserError(_(
                "La contraoferta sirve para ajustar el precio hacia abajo cuando "
                "el producto no cumplió lo publicado. Para pagar lo pactado, "
                "acepta el informe."))

        antes = self._signoff_snapshot()
        vendedor = verificacion.acceptance_ids.filtered(lambda a: a.role == "seller")
        # Foto del vendedor: si el comprador deshace la contraoferta, el
        # vendedor recupera la postura que tenía sobre el precio original.
        otras_antes = {str(v.id): v._signoff_snapshot() for v in vendedor}
        self.with_context(_shrimp_firma_ok=True).write({
            "decision": "counter",
            "counter_price": precio,
            "reason": reason,
            "decided_at": fields.Datetime.now(),
        })
        # El vendedor había aceptado un trato distinto: su postura anterior ya
        # no vale y vuelve a quedar pendiente sobre el precio nuevo.
        vendedor.with_context(_shrimp_firma_ok=True).write({
            "decision": "pending", "decided_at": False, "auto": False})
        self._signoff_log("decision", antes, reason=reason, others_before=otras_antes)
        verificacion._abrir_plazo()
        verificacion._notify_acceptance("counter")
        return True

    # ==================================================================
    # Deshacer la decisión (shrimp.signoff.mixin)
    # ==================================================================
    # Hasta cuándo: mientras la ronda siga abierta («waiting»). La ronda se
    # cierra cuando las DOS partes decidieron o al vencer el plazo; en ese
    # momento _resolver_aceptacion cierra el trato (factura el honorario,
    # cobra la comisión, confirma la compra) o lo da por caído (cancela la
    # compra y libera el stock). Nada de eso se deshace.
    def _signoff_undo_blocker(self):
        bloqueo = super()._signoff_undo_blocker()
        if bloqueo:
            return bloqueo
        verificacion = self.verification_id
        if verificacion.acceptance_state != "waiting":
            return _("La ronda de aceptación ya se cerró (compra cerrada o trato "
                     "caído): la decisión ya surtió efecto y no se puede deshacer.")
        if self.decision == "counter":
            vendedor = verificacion.acceptance_ids.filtered(lambda a: a.role == "seller")[:1]
            if vendedor and vendedor.decision != "pending":
                return _("El vendedor ya respondió a tu contraoferta: no se puede deshacer.")
        return False

    def _signoff_undo_until(self):
        verificacion = self.verification_id
        plazo = verificacion._texto_plazo()
        if (verificacion.verification_mode == "declared"
                and self.role == verificacion.declarant_role):
            return _("mientras la otra parte no responda: al deshacer, el informe "
                     "declarado vuelve a borrador para que lo corrijas")
        if plazo:
            return _("mientras la otra parte no responda y antes de que venza el "
                     "plazo (%s)") % plazo
        return _("mientras la otra parte no responda")

    def _signoff_after_undo(self, evento):
        """La regla del plazo tras deshacer.

        El plazo original se mantiene: deshacer no reinicia la ronda (si no,
        cualquiera podría estirar una compra indefinidamente). Pero una
        postura que se acaba de deshacer vuelve a «pendiente», y el cron da
        por aceptadas las pendientes al vencer el plazo: si faltaran dos
        minutos, deshacer un rechazo equivaldría a aceptar sin querer. Por eso
        se garantiza un margen mínimo (shrimp.signoff_undo_margin_minutes, 60
        por defecto): si al plazo le queda menos, se corre hasta ahora + margen.
        """
        verificacion = self.verification_id.sudo()
        # Verificación DECLARADA: si quien deshace es el declarante y la otra
        # parte todavía no respondió, deshacer es RETIRAR la presentación: el
        # informe vuelve a borrador para corregirlo (y se puede volver a
        # presentar, por esta parte o por la otra).
        if (verificacion.verification_mode == "declared"
                and verificacion.state == "declared"
                and self.role == verificacion.declarant_role
                and self.decision == "pending"
                and all(o.decision == "pending" for o in (verificacion.acceptance_ids - self))):
            verificacion._reabrir_declaracion()
            return True
        minimo = fields.Datetime.now() + timedelta(minutes=self._signoff_margin_minutes())
        if not verificacion.acceptance_deadline or verificacion.acceptance_deadline < minimo:
            verificacion.acceptance_deadline = minimo
        return True

    def _signoff_portal_url(self, partner):
        return "/marketplace/verifications/%s/acceptance" % self.verification_id.uuid_ref
