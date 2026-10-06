"""Historial de decisiones de las firmas de dos partes.

Cada decisión (aceptar, rechazar, contraofertar, firmar conforme / no
conforme) y cada vez que una parte la deshace queda aquí, con quién, en nombre
de qué parte, cuándo y por qué. Es lo que las dos partes ven en el portal como
«Historial de decisiones» y lo que permite deshacer: guarda cómo estaba la
postura ANTES de cada decisión, así que deshacer es devolverla a esa foto, no
inventar un estado.

Es un registro de auditoría: no se edita ni se borra desde fuera del servidor.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class ShrimpSignoffEvent(models.Model):
    _name = "shrimp.signoff.event"
    _description = "Historial de decisiones de una firma de dos partes"
    # El correo a la otra parte cuando alguien deshace su decisión sale de
    # aquí (plantilla sobre este modelo), con el mismo envío que el resto de
    # la plataforma.
    _inherit = ["shrimp.notify.mixin"]
    _order = "date desc, id desc"
    _rec_name = "summary"

    date = fields.Datetime(
        string="Fecha", required=True, default=fields.Datetime.now, index=True, readonly=True)
    kind = fields.Selection(
        [("decision", "Decisión"), ("undo", "Decisión deshecha")],
        string="Tipo", required=True, readonly=True, index=True)

    # La postura (fila de firma) sobre la que se decidió.
    res_model = fields.Char(string="Modelo de la firma", required=True, readonly=True, index=True)
    res_id = fields.Many2oneReference(
        string="Firma", model_field="res_model", required=True, readonly=True, index=True)
    # El expediente del que cuelga: verificación, orden de empaque, compromiso.
    parent_model = fields.Char(string="Modelo del expediente", required=True, readonly=True, index=True)
    parent_id = fields.Many2oneReference(
        string="Expediente", model_field="parent_model", required=True, readonly=True, index=True)
    parent_name = fields.Char(string="Referencia", readonly=True)
    ronda = fields.Integer(string="Ronda", default=1, readonly=True)

    role = fields.Char(string="Parte (código)", readonly=True)
    role_label = fields.Char(string="Parte", readonly=True)
    partner_id = fields.Many2one(
        "res.partner", string="Titular de la firma", readonly=True, index=True, ondelete="restrict")
    user_id = fields.Many2one(
        "res.users", string="Lo hizo", readonly=True, ondelete="restrict",
        default=lambda self: self.env.user)

    decision = fields.Char(string="Decisión (código)", readonly=True)
    decision_label = fields.Char(string="Decisión", readonly=True)
    previous_decision = fields.Char(string="Decisión anterior (código)", readonly=True)
    previous_label = fields.Char(string="Decisión anterior", readonly=True)
    counter_price = fields.Float(string="Precio propuesto", readonly=True)
    previous_counter_price = fields.Float(string="Precio propuesto antes", readonly=True)
    reason = fields.Text(string="Motivo", readonly=True)
    auto = fields.Boolean(string="Automática", readonly=True)
    on_behalf = fields.Boolean(
        string="Gestor en nombre de la parte", readonly=True,
        help="La deshizo un gestor de la plataforma, no la propia parte.")

    # Para poder deshacer: la foto de la postura antes de esta decisión, la de
    # las otras posturas que la decisión tocó (la contraoferta devuelve al
    # vendedor a «pendiente») y lo que el modelo necesite (p. ej. el estado de
    # la declaración de cosecha).
    snapshot_before = fields.Json(string="Postura antes", readonly=True)
    others_before = fields.Json(string="Otras posturas antes", readonly=True)
    extra = fields.Json(string="Datos del modelo", readonly=True)

    undone = fields.Boolean(string="Deshecha", readonly=True, index=True)
    undo_event_id = fields.Many2one(
        "shrimp.signoff.event", string="Deshecha por", readonly=True, ondelete="set null")

    summary = fields.Char(string="Resumen", compute="_compute_summary")

    _UPDATABLE = frozenset({"undone", "undo_event_id"})

    @api.depends("kind", "role_label", "partner_id", "decision_label", "previous_label")
    def _compute_summary(self):
        for rec in self:
            if rec.kind == "undo":
                rec.summary = _("%(parte)s deshizo «%(antes)s»") % {
                    "parte": rec.partner_id.name or rec.role_label or "",
                    "antes": rec.previous_label or ""}
            else:
                rec.summary = _("%(parte)s: %(dec)s") % {
                    "parte": rec.partner_id.name or rec.role_label or "",
                    "dec": rec.decision_label or ""}

    # ------------------------------------------------------------------
    # Inmutable
    # ------------------------------------------------------------------
    def write(self, vals):
        if not self.env.su or set(vals) - self._UPDATABLE:
            raise AccessError(_("El historial de decisiones no se modifica."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("El historial de decisiones no se borra."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    @api.model
    def history_for(self, parent):
        """Historial de un expediente (verificación, orden, compromiso), en sudo:
        lo usan los controladores tras comprobar que quien mira es parte."""
        if not parent:
            return self.sudo().browse()
        return self.sudo().search([
            ("parent_model", "=", parent._name), ("parent_id", "=", parent.id)])

    def date_local(self):
        self.ensure_one()
        return fields.Datetime.context_timestamp(self, self.date).strftime("%d/%m/%Y %H:%M")

    def actor_label(self):
        """Quién lo hizo, tal como lo lee la otra parte."""
        self.ensure_one()
        if self.auto:
            return _("Automática (venció el plazo)")
        if self.on_behalf:
            return _("Gestor de la plataforma (%s)") % (self.user_id.name or "")
        return self.user_id.name or ""

    def _posture(self):
        self.ensure_one()
        return self.env[self.res_model].sudo().with_context(active_test=False).browse(
            self.res_id).exists()

    def action_open_undo_wizard(self):
        """Desde el historial (backend): el gestor deshace esta decisión."""
        self.ensure_one()
        postura = self._posture()
        if not postura:
            raise UserError(_("La firma ya no existe."))
        return postura.action_open_signoff_undo_wizard()


class ShrimpSignoffUndoWizard(models.TransientModel):
    """Un gestor deshace la decisión de una parte, siempre con motivo.

    Existe para el caso en que la parte no puede hacerlo ella misma (no tiene
    acceso, se equivocó por teléfono...). Las reglas de hasta cuándo se puede
    deshacer son las mismas que para la parte: el gestor no puede deshacer lo
    que ya surtió efecto.
    """

    _name = "shrimp.signoff.undo.wizard"
    _description = "Deshacer la decisión de una parte (gestor)"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    posture_name = fields.Char(string="Firma", readonly=True)
    reason = fields.Text(string="Motivo", required=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        model = self.env.context.get("active_model")
        rid = self.env.context.get("active_id")
        if res.get("res_model") and res.get("res_id"):
            return res
        if model and rid and model in self.env and hasattr(self.env[model], "action_signoff_undo"):
            rec = self.env[model].browse(rid)
            res.update({"res_model": model, "res_id": rid,
                        "posture_name": rec.display_name})
        return res

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group("shrimp_marketplace.group_shrimp_manager"):
            raise AccessError(_("Solo un gestor de la plataforma deshace en nombre de una parte."))
        if self.res_model not in self.env or not hasattr(
                self.env[self.res_model], "action_signoff_undo"):
            raise UserError(_("Esto no es una firma de dos partes."))
        rec = self.env[self.res_model].browse(self.res_id).exists()
        if not rec:
            raise UserError(_("La firma ya no existe."))
        rec.action_signoff_undo(reason=self.reason, on_behalf=True)
        return {"type": "ir.actions.act_window_close"}
