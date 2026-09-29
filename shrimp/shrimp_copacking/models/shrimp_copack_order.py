from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpCopackOrder(models.Model):
    """El trabajo de empaque, desde que se adjudica hasta que se cuadra.

    Aqui no hay rendimiento que conciliar: el camaron llega listo para empacar,
    sin cabeza ni basura. Lo que hay que cuadrar es mas simple y mas exigente:
    entraron X libras y tienen que salir X libras empacadas. La diferencia, si
    la hay, se declara y la firman los dos.

    La propiedad del camaron NUNCA cambia de manos. Esto no es una compraventa
    y por eso no cuelga de shrimp.transaction: es un servicio que se cobra por
    libra empacada.
    """

    _name = "shrimp.copack.order"
    _description = "Orden de servicio de empaque"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Referencia", required=True, copy=False, readonly=True,
        default=lambda self: _("Nueva"))

    request_id = fields.Many2one(
        "shrimp.copack.request", string="Solicitud", ondelete="set null", index=True)
    offer_id = fields.Many2one(
        "shrimp.copack.offer", string="Oferta aceptada", ondelete="set null")

    client_partner_id = fields.Many2one(
        "res.partner", string="Cliente (dueño del camarón)", required=True,
        ondelete="restrict", index=True, tracking=True)
    copacker_partner_id = fields.Many2one(
        "res.partner", string="Maquilador", required=True,
        ondelete="restrict", index=True, tracking=True)
    product_id = fields.Many2one(
        "shrimp.product", string="Lote", ondelete="set null", index=True,
        help="Enlaza el empaque con la trazabilidad del lote.")

    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)
    agreed_qty_lb = fields.Float(
        string="Libras acordadas", required=True, digits=(16, 2), tracking=True)
    rate_per_lb = fields.Monetary(
        string="Tarifa por libra", required=True, currency_field="currency_id")

    supplies_notes = fields.Text(string="Insumos que lleva el cliente")
    supplies_received = fields.Boolean(
        string="Insumos recibidos completos", tracking=True,
        help="Si llegan incompletos, la línea se para y la culpa es del cliente. "
             "Dejarlo marcado aquí evita esa discusión después.")
    supplies_issue = fields.Text(string="Qué faltó de los insumos")

    # --- recepcion ---
    received_lb = fields.Float(string="Libras recibidas", digits=(16, 2), tracking=True)
    received_date = fields.Datetime(string="Fecha de recepción", readonly=True, copy=False)

    # --- entrega ---
    packed_lb = fields.Float(string="Libras empacadas", digits=(16, 2), tracking=True)
    packed_date = fields.Datetime(string="Fecha de entrega", readonly=True, copy=False)
    boxes = fields.Integer(string="Cajas / masters")
    packed_presentation = fields.Char(string="Presentación empacada")

    # --- el cuadre ---
    difference_lb = fields.Float(
        string="Diferencia (lb)", compute="_compute_cuadre", store=True, digits=(16, 2),
        help="Recibidas menos empacadas. Positivo significa que faltaron libras.")
    difference_pct = fields.Float(
        string="Diferencia (%)", compute="_compute_cuadre", store=True, digits=(5, 2))
    cuadra = fields.Boolean(
        string="Cuadra", compute="_compute_cuadre", store=True,
        help="Verdadero cuando la diferencia está dentro de la tolerancia pactada.")
    tolerance_pct = fields.Float(
        string="Tolerancia (%)", default=0.5, digits=(5, 2),
        help="Merma de manipulación que las partes dan por normal.")

    # --- dinero ---
    service_amount = fields.Monetary(
        string="A cobrar por el empaque", compute="_compute_importes", store=True,
        currency_field="currency_id",
        help="Tarifa por las libras efectivamente empacadas.")
    platform_rate_per_lb = fields.Monetary(
        string="Comisión Trazul por libra", currency_field="currency_id",
        default=0.01,
        help="Lo que la plataforma cobra al maquilador por libra empacada.")
    platform_amount = fields.Monetary(
        string="Comisión Trazul", compute="_compute_importes", store=True,
        currency_field="currency_id")

    state = fields.Selection(
        [
            ("confirmed", "Adjudicada"),
            ("received", "Recibida en planta"),
            ("packed", "Empacada"),
            ("signed", "Acta firmada"),
            ("closed", "Cerrada"),
            ("cancelled", "Cancelada"),
        ],
        string="Estado", default="confirmed", required=True, index=True, tracking=True)

    acceptance_ids = fields.One2many(
        "shrimp.copack.acceptance", "order_id", string="Firmas del acta")
    acceptance_state = fields.Selection(
        [
            ("na", "Sin abrir"),
            ("open", "A la firma"),
            ("closed", "Firmada por los dos"),
            ("disputed", "En disputa"),
        ],
        string="Acta", default="na", required=True, readonly=True, index=True)

    @api.depends("received_lb", "packed_lb", "tolerance_pct")
    def _compute_cuadre(self):
        for rec in self:
            dif = (rec.received_lb or 0.0) - (rec.packed_lb or 0.0)
            rec.difference_lb = dif
            rec.difference_pct = (100.0 * dif / rec.received_lb) if rec.received_lb else 0.0
            rec.cuadra = bool(rec.received_lb) and abs(rec.difference_pct) <= rec.tolerance_pct

    @api.depends("packed_lb", "rate_per_lb", "platform_rate_per_lb")
    def _compute_importes(self):
        for rec in self:
            rec.service_amount = (rec.packed_lb or 0.0) * rec.rate_per_lb
            rec.platform_amount = (rec.packed_lb or 0.0) * rec.platform_rate_per_lb

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nueva")) == _("Nueva"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.copack.order") or _("Nueva")
        return super().create(vals_list)

    @api.constrains("client_partner_id", "copacker_partner_id")
    def _check_partes(self):
        for rec in self:
            if rec.client_partner_id.shrimp_user_type not in ("camaronera", "empacadora"):
                raise ValidationError(_(
                    "El cliente del empaque es quien es dueño del camarón: una "
                    "camaronera o una empacadora."))
            if rec.copacker_partner_id.shrimp_user_type != "maquilador":
                raise ValidationError(_("Quien empaca tiene que ser un maquilador."))
            if rec.client_partner_id == rec.copacker_partner_id:
                raise ValidationError(_("Nadie se contrata a sí mismo."))

    @api.constrains("agreed_qty_lb", "rate_per_lb", "received_lb", "packed_lb",
                    "tolerance_pct", "platform_rate_per_lb")
    def _check_numeros(self):
        for rec in self:
            if rec.agreed_qty_lb <= 0:
                raise ValidationError(_("Las libras acordadas deben ser mayores que cero."))
            for campo, etiqueta in (
                    ("rate_per_lb", _("La tarifa por libra")),
                    ("received_lb", _("Las libras recibidas")),
                    ("packed_lb", _("Las libras empacadas")),
                    ("tolerance_pct", _("La tolerancia")),
                    ("platform_rate_per_lb", _("La comisión por libra"))):
                if (rec[campo] or 0.0) < 0:
                    raise ValidationError(_("%s no puede ser negativa.") % etiqueta)
            if rec.packed_lb and rec.received_lb and rec.packed_lb > rec.received_lb:
                raise ValidationError(_(
                    "No se pueden entregar más libras de las que entraron: "
                    "recibidas %(r).2f, empacadas %(e).2f.")
                    % {"r": rec.received_lb, "e": rec.packed_lb})

    # ------------------------------------------------------------------
    # Recorrido
    # ------------------------------------------------------------------
    def action_register_reception(self):
        for rec in self:
            if rec.state != "confirmed":
                raise ValidationError(_("La recepción se registra sobre una orden adjudicada."))
            if not rec.received_lb:
                raise ValidationError(_("Hay que indicar cuántas libras se recibieron."))
            rec.write({"state": "received", "received_date": fields.Datetime.now()})

    def action_register_packing(self):
        for rec in self:
            if rec.state != "received":
                raise ValidationError(_("Primero hay que registrar la recepción."))
            if not rec.packed_lb:
                raise ValidationError(_("Hay que indicar cuántas libras se empacaron."))
            rec.write({"state": "packed", "packed_date": fields.Datetime.now()})
            rec._abrir_acta()

    def _abrir_acta(self):
        """Dos firmas, una por parte. El acta la cierran ellos, no la plataforma."""
        self.ensure_one()
        self.acceptance_ids.unlink()
        Firma = self.env["shrimp.copack.acceptance"]
        for rol, socio in (("client", self.client_partner_id),
                           ("copacker", self.copacker_partner_id)):
            Firma.create({"order_id": self.id, "role": rol, "partner_id": socio.id})
        self.acceptance_state = "open"

    def _evaluar_acta(self):
        self.ensure_one()
        # Solo una orden empacada tiene acta que evaluar. Si llega aqui en otro
        # estado es que algo la movio por detras, y cerrar el acta entonces
        # equivaldria a dar por bueno un trabajo que nadie hizo.
        if self.state != "packed":
            return
        decisiones = self.acceptance_ids.mapped("decision")
        if "rejected" in decisiones:
            self.acceptance_state = "disputed"
        elif decisiones and all(d == "accepted" for d in decisiones):
            self.write({"acceptance_state": "closed", "state": "signed"})

    def action_close(self):
        for rec in self:
            if rec.state != "signed":
                raise ValidationError(_("Se cierra cuando las dos partes firmaron el acta."))
            rec.state = "closed"

    def action_cancel(self):
        for rec in self:
            if rec.state in ("signed", "closed"):
                raise ValidationError(_("Un trabajo ya firmado no se cancela."))
            rec.state = "cancelled"
            # Cerrar el acta tambien. Sin esto las firmas pendientes seguian
            # vivas: las dos partes firmaban una orden CANCELADA, _evaluar_acta
            # la pasaba a "signed", de ahi a "closed", y acababa cobrandose en
            # liquidaciones y saliendo en el certificado de trazabilidad.
            if rec.acceptance_state == "open":
                rec.acceptance_ids.filtered(
                    lambda f: f.decision == "pending").unlink()
                rec.acceptance_state = "na"
