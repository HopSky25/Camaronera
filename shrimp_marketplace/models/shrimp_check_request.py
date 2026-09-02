import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ShrimpCheckRequest(models.Model):
    _name = "shrimp.check.request"
    _description = "Solicitud de chequeo"
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
    ], string="Estado", default="requested", required=True, tracking=True, index=True)

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
    sale_order_id = fields.Many2one(
        "sale.order", string="Pedido de venta", readonly=True, copy=False)
    invoice_id = fields.Many2one(
        "account.move", string="Factura", readonly=True, copy=False)
    invoice_state = fields.Selection(
        related="invoice_id.state", string="Estado de la factura", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code("shrimp.check.request") or _("Nuevo")
        return super().create(vals_list)

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

    def action_mark_under_review(self):
        self.write({"state": "under_review"})

    def action_approve(self):
        for rec in self:
            rec._execute_approved_flow()

    def _execute_approved_flow(self):
        self.ensure_one()

        result = self.product_id.execute_purchase_flow(
            buyer_partner=self.buyer_partner_id,
            qty=self.qty,
        )

        self.write({
            "state": "approved",
            "reviewed_by": self.env.user.id,
            "reviewed_date": fields.Datetime.now(),
            "transaction_id": result["transaction"].id if result.get("transaction") else False,
            "result_product_id": result["new_product"].id if result.get("new_product") else False,
            "source_lot_id": result["source_lot_ids"][0].id if result.get("source_lot_ids") else False,
        })

    def action_reject(self):
        self.write({
            "state": "rejected",
            "reviewed_by": self.env.user.id,
            "reviewed_date": fields.Datetime.now(),
        })

    def action_cancel(self):
        self.write({"state": "cancelled"})

    # ------------------------------------------------------------------
    # Cobro del chequeo -> Ventas / Contabilidad (cliente = comprador)
    # ------------------------------------------------------------------
    def _get_check_product(self):
        """Producto de servicio con el que se factura el chequeo. Se crea en
        tiempo de ejecución (evita el conflicto de orden de carga con
        website_sale, que impone publish_date en product.template)."""
        product = self.env.ref(
            "shrimp_marketplace.product_marketplace_check",
            raise_if_not_found=False)
        if product:
            return product
        tmpl = self.env["product.template"].sudo().create({
            "name": "Chequeo de producto",
            "type": "service",
            "invoice_policy": "order",
            "list_price": 0.0,
            "sale_ok": True,
            "purchase_ok": False,
            "default_code": "CHEQUEO-MKT",
        })
        variant = tmpl.product_variant_id
        self.env["ir.model.data"].sudo().create({
            "name": "product_marketplace_check",
            "module": "shrimp_marketplace",
            "model": "product.product",
            "res_id": variant.id,
            "noupdate": True,
        })
        return variant

    def _create_check_sale_documents(self):
        """Genera pedido de venta (confirmado) + factura (contabilizada) por el
        costo del chequeo. Cliente = comprador. Nunca rompe el flujo web."""
        Sale = self.env["sale.order"].sudo()
        for rec in self:
            if rec.sale_order_id or not rec.buyer_partner_id or rec.check_fee <= 0:
                continue
            product = rec._get_check_product()
            if not product:
                _logger.warning(
                    "Producto de chequeo no encontrado; se omite el documento "
                    "de venta para la solicitud %s", rec.name)
                continue
            try:
                so = Sale.create({
                    "partner_id": rec.buyer_partner_id.id,
                    "client_order_ref": rec.name,
                    "order_line": [(0, 0, {
                        "product_id": product.id,
                        "name": "Chequeo de producto – %s" % (rec.product_id.display_name or ""),
                        "product_uom_qty": 1.0,
                        "price_unit": rec.check_fee,
                        "tax_id": [(6, 0, [])],
                    })],
                })
                so.action_confirm()
                rec.sale_order_id = so.id
                invoice = so._create_invoices()
                if invoice:
                    invoice.action_post()
                    rec.invoice_id = invoice.id
            except Exception as e:  # noqa: BLE001
                _logger.exception(
                    "No se pudo generar el documento de venta del chequeo %s: %s",
                    rec.name, e)

    def action_generate_check_documents(self):
        self._create_check_sale_documents()
        return True

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