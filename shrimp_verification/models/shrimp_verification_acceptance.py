from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ShrimpVerificationAcceptance(models.Model):
    """La postura de una de las partes frente al informe del verificador.

    Hay exactamente dos por verificación: la del comprador y la del vendedor.
    La compra se cierra cuando las dos están en "aceptada"; se cae en cuanto
    una queda en "rechazada".

    Se modela como registro y no como dos casillas en la compra porque hace
    falta guardar el porqué, la hora, si la decisión fue automática por
    vencimiento del plazo y, en el caso del comprador, el precio que propone.
    Eso es la prueba de a qué se comprometió cada uno.
    """

    _name = "shrimp.verification.acceptance"
    _description = "Aceptación del informe de verificación"
    _order = "verification_id, role"

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
        string="Decisión", default="pending", required=True, index=True, tracking=True)

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
    def _sellar(self, decision, reason=None, auto=False):
        self.ensure_one()
        vals = {
            "decision": decision,
            "decided_at": fields.Datetime.now(),
            "auto": auto,
        }
        if reason is not None:
            vals["reason"] = reason
        self.write(vals)

    def action_accept(self, reason=None, auto=False):
        for rec in self:
            rec._sellar("accepted", reason, auto)
        self.mapped("verification_id")._resolver_aceptacion()
        return True

    def action_reject(self, reason=None):
        for rec in self:
            if not reason and not rec.reason:
                raise UserError(_(
                    "Explica por qué rechazas el informe. La otra parte tiene "
                    "derecho a saberlo y queda registrado."))
            rec._sellar("rejected", reason)
        self.mapped("verification_id")._resolver_aceptacion()
        return True

    def action_counter(self, price, reason=None):
        """El comprador propone seguir con la compra a otro precio."""
        self.ensure_one()
        if self.role != "buyer":
            raise UserError(_("Solo el comprador puede proponer un precio ajustado."))

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

        self.write({
            "decision": "counter",
            "counter_price": precio,
            "reason": reason,
            "decided_at": fields.Datetime.now(),
        })
        # El vendedor había aceptado un trato distinto: su postura anterior ya
        # no vale y vuelve a quedar pendiente sobre el precio nuevo.
        verificacion.acceptance_ids.filtered(lambda a: a.role == "seller").write({
            "decision": "pending", "decided_at": False, "auto": False})
        verificacion._abrir_plazo()
        verificacion._notify_acceptance("counter")
        return True
