from odoo import api, fields, models, _
from odoo.exceptions import UserError

# Estados en los que una compra verificada todavía NO consumió lotes pero ya
# reserva la cantidad: hasta que las partes firman, nadie más puede comprarla.
VERIFICATION_HOLD_STATES = ("pending_verification", "pending_acceptance")


class ShrimpTransaction(models.Model):
    _name = "shrimp.transaction"
    _inherit = "shrimp.transaction"

    # Estado intermedio: la compra ya está grabada, pero todavía no consumió
    # lotes. El stock queda RESERVADO hasta que el verificador emita veredicto.
    state = fields.Selection(
        selection_add=[
            ("pending_verification", "Pendiente de verificación"),
            # El veredicto no cierra la venta: la deja a la firma de las dos
            # partes. Solo cuando comprador y vendedor aceptan el informe se
            # consumen lotes y se cobra comisión.
            ("pending_acceptance", "Esperando aceptación de las partes"),
            ("confirmed",),
        ],
        ondelete={"pending_verification": "set default",
                  "pending_acceptance": "set default"},
    )

    # Una compra tiene a lo sumo UNA verificación (lo garantiza un unique en
    # shrimp.verification), pero la relación vive del lado de la verificación,
    # así que aquí es un One2many. Se llama en plural, como todo One2many.
    verification_ids = fields.One2many(
        "shrimp.verification", "transaction_id", string="Verificación")

    # Alias de compatibilidad (una versión): plantillas, la API y código de
    # terceros leían `verification_id` como si fuera la verificación. Ahora
    # es un Many2one calculado con la primera (y única) verificación.
    verification_id = fields.Many2one(
        "shrimp.verification", string="Verificación (obsoleto)",
        compute="_compute_verification_id", search="_search_verification_id",
        help="Obsoleto: usar verification_ids.")

    verification_state = fields.Selection(
        related="verification_ids.state", string="Estado de la verificación", readonly=True)

    verifier_partner_id = fields.Many2one(
        related="verification_ids.verifier_partner_id", string="Verificador",
        readonly=True, store=True)

    # Cómo se verifica esta compra (lo eligió el comprador al comprar):
    # verificadora de la plataforma o verificación declarada por las partes.
    verification_mode = fields.Selection(
        related="verification_ids.verification_mode", string="Modo de verificación",
        readonly=True, store=True)

    @api.depends("verification_ids")
    def _compute_verification_id(self):
        for rec in self:
            rec.verification_id = rec.verification_ids[:1]

    def _search_verification_id(self, operator, value):
        return [("verification_ids", operator, value)]

    # ------------------------------------------------------------------
    # Seguimiento del despacho
    # ------------------------------------------------------------------
    # Uno por compra (lo garantiza un unique en shrimp.dispatch), pero el
    # Many2one vive del lado del despacho, así que aquí entra como inverso.
    # El Many2one calculado y almacenado es lo que permite escribir dominios
    # y campos relacionados desde la verificación sin recorrer la colección.
    dispatch_ids = fields.One2many(
        "shrimp.dispatch", "transaction_id", string="Despachos")
    dispatch_id = fields.Many2one(
        "shrimp.dispatch", string="Despacho",
        compute="_compute_dispatch_id", store=True, readonly=True)

    @api.depends("dispatch_ids")
    def _compute_dispatch_id(self):
        for rec in self:
            rec.dispatch_id = rec.dispatch_ids[:1]

    def _ensure_dispatch(self):
        """Devuelve el seguimiento del despacho, creándolo si aún no existe.

        Se llama al crear la verificación y también desde el portal, porque las
        compras que ya estaban verificándose cuando se instaló esto no pasaron
        por aquel create y aun así necesitan su pantalla.
        """
        Dispatch = self.env["shrimp.dispatch"].sudo()
        salida = Dispatch.browse()
        for rec in self:
            if rec.dispatch_ids:
                salida |= rec.dispatch_ids[:1]
                continue
            salida |= Dispatch.create({"transaction_id": rec.id})
        return salida

    needs_verification = fields.Boolean(
        string="Requiere verificación", default=False, readonly=True, copy=False,
        help="Marcada cuando la compra quedó sujeta a verificación en campo.",
    )

    can_be_completed = fields.Boolean(
        string="Lista para concluir", compute="_compute_can_be_completed",
        help="La verificación fue aprobada y el comprador puede concluir la compra.",
    )

    awaiting_signatures = fields.Boolean(
        string="Pendiente de firmas", compute="_compute_awaiting_signatures",
        help="El veredicto fue favorable y faltan las firmas de comprador y "
             "vendedor para cerrar la compra.")

    @api.depends("state")
    def _compute_awaiting_signatures(self):
        for rec in self:
            rec.awaiting_signatures = rec.state == "pending_acceptance"

    @api.depends("state", "verification_ids.state", "verification_ids.acceptance_state")
    def _compute_can_be_completed(self):
        for rec in self:
            verificacion = rec.verification_ids[:1]
            rec.can_be_completed = bool(
                rec.state in VERIFICATION_HOLD_STATES
                and verificacion
                # «declared»: informe declarado presentado por una parte
                # (la otra lo confirma en la misma ronda de aceptación).
                and verificacion.state in ("approved", "approved_obs", "declared")
                # Un veredicto favorable ya no basta: hace falta que las dos
                # partes hayan firmado que aceptan el informe.
                and verificacion.acceptance_state == "closed"
            )

    def action_await_acceptance(self):
        """Pasa la compra a la firma de las partes tras un veredicto favorable."""
        for rec in self:
            if rec.state == "pending_verification":
                rec.write({"state": "pending_acceptance"})
        return True

    def action_confirm(self):
        """Permite confirmar también las compras que venían de verificación.

        El flujo base solo procesa las transacciones en borrador; aquí las
        aprobadas por el verificador se pasan a borrador para que el mismo
        código base consuma lotes, genere la trazabilidad y cobre la comisión
        (que se registra en ese único punto de cierre).
        """
        ready = self.filtered(
            lambda t: t.state in VERIFICATION_HOLD_STATES and t.can_be_completed)
        if ready:
            super(ShrimpTransaction, ready).write({"state": "draft"})
        return super().action_confirm()

    # ------------------------------------------------------------------
    # El lote del comprador refleja el peso REAL en planta
    # ------------------------------------------------------------------
    def action_receive(self):
        res = super().action_receive()
        for rec in self:
            rec._shrimp_apply_plant_weight()
        return res

    def _shrimp_apply_plant_weight(self):
        """Ajusta el lote recibido al peso verificado en planta.

        La compra se pacta por las libras que salen de la finca, pero a la
        planta llega lo que la báscula dice. Ese peso (y la basura que se
        descarta) es lo que de verdad entra al inventario de la empacadora:
        se documenta con un ajuste y un consumo, cada uno con su motivo, para
        que la cantidad que luego se empaca o exporta cuadre.
        """
        self.ensure_one()
        ver = self.verification_ids[:1]
        libra = self.env.ref("shrimp_marketplace.uom_libra", raise_if_not_found=False)
        if (not ver or ver.scope != "adult"
                or ver.state not in ("approved", "approved_obs", "declared")
                or not ver.weight_plant_lb or (libra and self.product_id.uom_id != libra)):
            return
        Lot = self.env["shrimp.stock.lot"].sudo()
        Move = self.env["shrimp.stock.move"].sudo()
        lots = Lot.search([("origin_move_id", "in", self.stock_move_ids.ids),
                           ("owner_id", "=", self.buyer_partner_id.id)], order="id asc")
        if not lots:
            return
        marca = "(%s)" % ver.name
        if Move.search_count([("lot_id", "in", lots.ids), ("reason", "like", marca)]):
            return
        recibido = sum(lots.mapped("initial_qty"))
        delta = ver.weight_plant_lb - recibido
        fmt = self.shrimp_fmt_qty
        motivo = _("Peso verificado en planta: %(planta)s frente a %(pactado)s pactadas %(ver)s") % {
            "planta": fmt(ver.weight_plant_lb, "lb"), "pactado": fmt(recibido, "lb"), "ver": marca}
        if abs(delta) >= 0.005:
            if delta > 0:
                lots[0]._shrimp_internal_move("adjustment", delta, motivo, direction="in")
            else:
                falta = -delta
                for lot in lots:
                    if falta < 0.005:
                        break
                    take = min(lot.available_qty, falta)
                    if take > 0:
                        lot._shrimp_internal_move("adjustment", take, motivo, direction="out")
                    falta -= take
        basura = ver.trash_lb or 0.0
        if basura >= 0.005:
            motivo = _("Basura descartada en planta %s") % marca
            for lot in lots:
                if basura < 0.005:
                    break
                take = min(lot.available_qty, basura)
                if take > 0:
                    lot._shrimp_internal_move("consumption", take, motivo, direction="out")
                basura -= take

    def action_complete_after_verification(self):
        """Concluir la compra ya verificada y aceptada por las dos partes.

        La comisión ya no se registra aquí: la registra action_confirm, igual
        que en cualquier otro camino de compra.
        """
        for rec in self:
            if rec.state not in VERIFICATION_HOLD_STATES:
                raise UserError(_("Esta compra no está pendiente de verificación."))
            if not rec.can_be_completed:
                raise UserError(_(
                    "La compra todavía no está lista: hace falta el veredicto "
                    "aprobado y que comprador y vendedor acepten el informe."))
            rec.action_confirm()
        return True

    def action_cancel_for_verification(self):
        """Cancela la compra pendiente y libera la reserva de stock.

        No hay lotes que devolver: en el flujo con verificación nunca llegaron a
        consumirse, solo estaban reservados.
        """
        for rec in self:
            if rec.state in VERIFICATION_HOLD_STATES:
                rec.write({"state": "cancel"})
                rec.product_id._compute_available_qty()
        return True

    def traceability_indicators(self, data=None):
        """Indicadores del certificado: añade lo medido por la verificación
        de esta compra (solo con veredicto emitido)."""
        rows = super().traceability_indicators(data)
        ver = self.sudo().verification_ids[:1]
        if not ver or ver.state not in ("approved", "approved_obs", "rejected", "declared"):
            return rows
        grupo = _("Verificación · %s") % (
            ver.verifier_partner_id.name or ver.declarant_partner_id.name or ver.name)
        num = self.shrimp_fmt_num
        if ver.scope == "adult" and ver.total_processed_lb:
            rows.append({
                "group": grupo, "label": _("Rendimiento en planta"),
                "value": "%s %%" % num(ver.yield_pct, 2),
                "pct": max(0.0, min(100.0, ver.yield_pct)),
                "detail": _("%(proc)s procesadas de %(neto)s netas") % {
                    "proc": self.shrimp_fmt_qty(ver.total_processed_lb, "lb"),
                    "neto": self.shrimp_fmt_qty(ver.net_weight_lb, "lb")},
            })
            rows.append({
                "group": grupo, "label": _("Clase A"),
                "value": "%s %%" % num(ver.yield_class_a_pct, 2),
                "pct": max(0.0, min(100.0, ver.yield_class_a_pct)),
                "detail": _("clase B %(b)s %% · clase C %(c)s %%") % {
                    "b": num(ver.yield_class_b_pct, 2), "c": num(ver.yield_class_c_pct, 2)},
            })
            if ver.grams_farm or ver.grams_plant_1:
                rows.append({
                    "group": grupo, "label": _("Gramaje"),
                    "value": "%s g" % num(ver.grams_plant_1 or ver.grams_farm, 2),
                    "pct": None,
                    "detail": _("camaronera %(f)s g · planta %(p)s g") % {
                        "f": num(ver.grams_farm, 2), "p": num(ver.grams_plant_1, 2)},
                })
        elif ver.scope == "larvae" and ver.larvae_qty_verified:
            rows.append({
                "group": grupo, "label": _("Supervivencia verificada"),
                "value": "%s %%" % num(ver.larvae_survival_rate, 2),
                "pct": max(0.0, min(100.0, ver.larvae_survival_rate)),
                "detail": _("tamaño %(mg)s mg · desvío %(d)s pp") % {
                    "mg": num(ver.larvae_avg_size_mg, 3), "d": num(ver.larvae_survival_diff, 2)},
            })
        return rows
