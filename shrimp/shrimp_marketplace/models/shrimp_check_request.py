import logging
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Flujo RETIRADO (decisión D-28). Los parámetros del plazo de respuesta y del
# costo del chequeo se borran en la migración 19.0.1.38.0.
CHECK_OPEN_STATES = ("requested", "under_review")
# Única forma de crear una solicitud: cargar HISTÓRICO (demo, pruebas).
LEGACY_IMPORT_CTX = "shrimp_legacy_check_import"


class ShrimpCheckRequest(models.Model):
    """Solicitud de chequeo: flujo RETIRADO por completo (decisión D-28).

    La verificación en campo (shrimp_verification) cubre lo mismo con técnico,
    informe, aceptación de las partes y trazabilidad; se compra directo o con
    verificación. Aquí solo queda el HISTÓRICO, de solo lectura (Ajustes ›
    Técnico): no se crean solicitudes nuevas por ninguna vía (portal, API,
    back-office, RPC), no se aprueban ni se rechazan, y el costo del chequeo
    ya no existe. La migración 19.0.1.38.0 canceló las que estaban abiertas,
    liberó la cantidad que reservaban y avisó a las dos partes. La API
    responde 410 Gone en sus rutas.
    """

    _name = "shrimp.check.request"
    _description = "Solicitud de chequeo (histórico, flujo retirado)"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Referencia",
        required=True,
        copy=False,
        default=lambda self: _("Nuevo"),
        tracking=True,
    )

    product_id = fields.Many2one(
        "shrimp.product",
        string="Producto origen",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )

    seller_partner_id = fields.Many2one(
        "res.partner",
        string="Vendedor",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )

    buyer_partner_id = fields.Many2one(
        "res.partner",
        string="Comprador",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )

    qty = fields.Float(
        string="Cantidad solicitada",
        required=True,
        tracking=True,
    )

    uom_id = fields.Many2one(
        "shrimp.uom",
        string="Unidad",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )

    state = fields.Selection([
        ("requested", "Solicitado"),
        ("under_review", "En revisión"),
        ("approved", "Aprobado"),
        ("rejected", "Rechazado"),
        ("cancelled", "Cancelado"),
        ("expired", "Vencido"),
    ], string="Estado", default="requested", required=True, tracking=True, index=True)
    # Histórico: el plazo que tuvo el vendedor (flujo retirado, D-28).
    response_deadline = fields.Datetime(
        string="Responder antes de", index=True, copy=False, tracking=True,
        help="Plazo que tenía el vendedor para responder (flujo retirado).")

    transaction_id = fields.Many2one(
        "shrimp.transaction",
        string="Transacción",
        readonly=True,
        ondelete="set null",
    )

    source_lot_id = fields.Many2one(
        "shrimp.stock.lot",
        string="Lote origen",
        ondelete="set null",
    )

    result_product_id = fields.Many2one(
        "shrimp.product",
        string="Producto generado para comprador",
        readonly=True,
        ondelete="set null",
    )

    note = fields.Text(string="Observaciones")
    reviewed_by = fields.Many2one("res.users", string="Revisado por", readonly=True)
    reviewed_date = fields.Datetime(string="Fecha revisión", readonly=True)

    # Cobro del chequeo (se cobra al COMPRADOR: enviamos un equipo a verificar).
    check_fee = fields.Monetary(string="Costo del chequeo", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", default=lambda self: self.env.company.currency_id)
    charge_id = fields.Many2one(
        "shrimp.charge", string="Cobro del chequeo", readonly=True, copy=False)
    # Documentos del cobro (antes se guardaban aquí; ahora vienen del cobro).
    sale_order_id = fields.Many2one(
        "sale.order", string="Pedido de venta", readonly=True, copy=False)
    invoice_id = fields.Many2one(
        "account.move", string="Factura", readonly=True, copy=False)
    invoice_state = fields.Selection(
        related="invoice_id.state", string="Estado de la factura", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(LEGACY_IMPORT_CTX):
            raise UserError(self._shrimp_retired_text())
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code("shrimp.check.request") or _("Nuevo")
        return super().create(vals_list)

    @api.model
    def _shrimp_retired_text(self):
        return self.env._(
            "La solicitud de chequeo se retiró de la plataforma: compra directo o con "
            "verificación en campo. Las solicitudes anteriores se conservan solo para consulta.")

    def _shrimp_retired(self, *args, **kwargs):
        raise UserError(self._shrimp_retired_text())

    @api.constrains("qty")
    def _check_qty(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError(_("La cantidad debe ser mayor a 0."))

    @api.constrains("buyer_partner_id", "seller_partner_id")
    def _check_partners(self):
        for rec in self:
            if rec.buyer_partner_id == rec.seller_partner_id:
                raise ValidationError(_("El comprador y el vendedor no pueden ser el mismo."))

    # Flujo retirado: ninguna acción de resolución (por RPC tampoco).
    action_mark_under_review = _shrimp_retired
    action_approve = _shrimp_retired
    action_reject = _shrimp_retired
    action_cancel = _shrimp_retired
    action_generate_check_documents = _shrimp_retired

    # ------------------------------------------------------------------
    # Cierre automático: plazo vencido / producto retirado
    # ------------------------------------------------------------------
    def _shrimp_close_open(self, state, motivo):
        """Cierra solicitudes abiertas sin compra (vencida o producto retirado),
        libera la reserva y avisa a las dos partes."""
        abiertas = self.filtered(lambda r: r.state in CHECK_OPEN_STATES)
        if not abiertas:
            return abiertas
        abiertas.sudo().write({"state": state, "reviewed_date": fields.Datetime.now()})
        for rec in abiertas:
            rec._shrimp_notify_parties(motivo)
        return abiertas

    def _shrimp_notify_parties(self, texto):
        self.ensure_one()
        partners = (self.buyer_partner_id | self.seller_partner_id).filtered("email")
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("<p>%s</p>") % texto,
                subject=_("Solicitud de chequeo %s") % (self.name or ""),
                partner_ids=partners.ids, message_type="comment",
                subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 - un aviso nunca tumba el proceso
            _logger.warning("No se pudo avisar la solicitud de chequeo %s", self.id, exc_info=True)

    @api.model
    def _shrimp_retire_open(self):
        """Migración D-28: cancela las solicitudes abiertas, libera la cantidad
        reservada y avisa al comprador y al vendedor. Devuelve las cerradas."""
        abiertas = self.sudo().search([("state", "in", CHECK_OPEN_STATES)])
        cerradas = self.browse()
        for rec in abiertas:
            cerradas |= rec._shrimp_close_open("cancelled", _(
                "La solicitud de chequeo %(ref)s se canceló porque la plataforma retiró este "
                "flujo. No se ejecutó ninguna compra ni hay cobro, y la cantidad reservada "
                "(%(qty)s) vuelve a estar disponible. Si todavía te interesa el lote, cómpralo "
                "directo o con verificación en campo.") % {
                    "ref": rec.name or "", "qty": "{:,.2f}".format(rec.qty or 0.0)})
        if cerradas:
            productos = cerradas.mapped("product_id").sudo()
            productos._compute_available_qty()
        return cerradas

    @api.model
    def _cron_expire(self):
        """Sin uso: el cron se borró con el flujo (D-28). Se conserva por si
        una base sin actualizar lo llama."""
        return 0

    def action_open_sale_order(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
            "target": "current",
        }