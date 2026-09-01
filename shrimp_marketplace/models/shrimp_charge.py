import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class ShrimpCharge(models.Model):
    _name = "shrimp.charge"
    _inherit = "shrimp.uuid.mixin"
    _description = "Cobro de comisión del marketplace"
    _order = "create_date desc"
    _rec_name = "name"

    name = fields.Char(string="Referencia", default=lambda self: _("Nuevo"), copy=False)
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Transacción", ondelete="cascade", index=True)
    seller_partner_id = fields.Many2one("res.partner", string="Vendedor", index=True)
    buyer_partner_id = fields.Many2one("res.partner", string="Comprador")
    product_id = fields.Many2one("shrimp.product", string="Producto")
    qty = fields.Float(string="Cantidad vendida")
    uom_id = fields.Many2one("shrimp.uom", string="Unidad")
    rate_cents = fields.Float(
        string="Tarifa (centavos por unidad)",
        help="Centavos cobrados por cada unidad vendida al momento del cobro.")
    amount = fields.Monetary(string="Comisión cobrada", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
    date = fields.Datetime(string="Fecha del cobro", default=fields.Datetime.now)

    # Integración con Ventas / Contabilidad de Odoo
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
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.charge") or _("Nuevo")
        return super().create(vals_list)

    @api.model
    def register_for_transaction(self, transaction, qty):
        """Registra el cobro de comisión de una transacción según la tarifa
        configurada en la unidad de medida del producto (centavos por unidad
        vendida). Devuelve el cobro o False."""
        if not transaction:
            return False
        product = transaction.product_id
        # La tarifa vive en la tabla de Unidades de medida; cada unidad tiene su
        # propio valor (p. ej. la libra puede cobrar distinto que el millar).
        rate = float(product.uom_id.sudo().commission_cents or 0.0)
        if rate <= 0:
            return False
        amount = round((qty or 0.0) * rate / 100.0, 2)
        charge = self.sudo().create({
            "transaction_id": transaction.id,
            "seller_partner_id": transaction.seller_partner_id.id,
            "buyer_partner_id": transaction.buyer_partner_id.id,
            "product_id": product.id,
            "qty": qty,
            "uom_id": product.uom_id.id,
            "rate_cents": rate,
            "amount": amount,
        })
        # Lleva el cobro al módulo de Ventas: pedido confirmado + factura.
        charge._create_sale_documents()
        return charge

    def _get_commission_product(self):
        """Devuelve (creando si no existe) el producto de servicio con el que se
        factura la comisión. Se crea en tiempo de ejecución para no depender del
        orden de carga de módulos (website_sale impone publish_date en el
        product.template)."""
        product = self.env.ref(
            "shrimp_marketplace.product_marketplace_commission",
            raise_if_not_found=False)
        if product:
            return product

        tmpl = self.env["product.template"].sudo().create({
            "name": "Comisión Marketplace",
            "type": "service",
            "invoice_policy": "order",
            "list_price": 0.0,
            "sale_ok": True,
            "purchase_ok": False,
            "default_code": "COMISION-MKT",
        })
        variant = tmpl.product_variant_id
        self.env["ir.model.data"].sudo().create({
            "name": "product_marketplace_commission",
            "module": "shrimp_marketplace",
            "model": "product.product",
            "res_id": variant.id,
            "noupdate": True,
        })
        return variant

    def _create_sale_documents(self):
        """Genera el pedido de venta (confirmado) y la factura (contabilizada)
        por el cobro de comisión. El cliente es el VENDEDOR (paga la comisión al
        marketplace, como en Amazon/Airbnb). Nunca interrumpe el flujo de compra:
        si algo falla, se registra en el log y el cobro queda igual."""
        Sale = self.env["sale.order"].sudo()
        for charge in self:
            if charge.sale_order_id or not charge.seller_partner_id or charge.amount <= 0:
                continue
            product = charge._get_commission_product()
            if not product:
                _logger.warning(
                    "Producto de comisión no encontrado; se omite el documento de "
                    "venta para el cobro %s", charge.name)
                continue
            try:
                so = Sale.create({
                    "partner_id": charge.seller_partner_id.id,
                    "client_order_ref": charge.name,
                    "order_line": [(0, 0, {
                        "product_id": product.id,
                        "name": "Comisión marketplace – %s" % (charge.product_id.display_name or ""),
                        "product_uom_qty": charge.qty or 1.0,
                        "price_unit": (charge.rate_cents or 0.0) / 100.0,
                        "tax_ids": [(6, 0, [])],
                    })],
                })
                so.action_confirm()
                charge.sale_order_id = so.id

                invoice = so._create_invoices()
                if invoice:
                    invoice.action_post()
                    charge.invoice_id = invoice.id
            except Exception as e:  # noqa: BLE001 - no debe romper la compra
                # Se registra el fallo Y se marca en el propio cobro: si solo va
                # al log, un error aquí pasa desapercibido y los cobros quedan
                # sin facturar sin que nadie lo note.
                _logger.exception(
                    "No se pudo generar el documento de venta para el cobro %s: %s",
                    charge.name, e)
                charge.sudo().message_post(body="No se pudo facturar: %s" % e) \
                    if hasattr(charge, "message_post") else None

    def action_generate_sale_documents(self):
        """Botón: genera manualmente el pedido de venta y la factura del cobro."""
        self._create_sale_documents()
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
