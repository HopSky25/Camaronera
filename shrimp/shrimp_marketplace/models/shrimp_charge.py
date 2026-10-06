"""Cobros de la plataforma: el único sitio donde se factura un servicio.

DECISIÓN DE NEGOCIO (facturación en dos pasos)
==============================================
1. La plataforma solo factura SUS servicios, con factura electrónica propia:
   - ``commission``: la comisión por unidad vendida (la paga el vendedor). Se
     registra en el cierre de la compra (``shrimp.transaction.action_confirm``),
     sea cual sea el camino por el que se llegó: compra directa, solicitud de
     chequeo aprobada, compra verificada o reserva de cosecha.
   - ``verification_fee``: el honorario de la verificación en campo (lo paga el
     comprador que la contrata). Se emite al INICIAR la compra verificada; si
     después el honorario se reasigna (trato caído por culpa medible del
     vendedor, veredicto rechazado) se emite nota de crédito y se factura al
     nuevo pagador.
   - ``copack_platform``: la comisión de la plataforma por libra empacada (la
     paga el maquilador). Se emite cuando las dos partes firman el acta.
   - ``check_fee``: el chequeo del flujo antiguo de solicitudes (legado).
2. La MERCADERÍA (el camarón, la larva) la factura el vendedor desde su propio
   sistema. La plataforma no emite esa factura: solo la REGISTRA (número, clave
   de acceso, archivo) en la compra; ver shrimp.transaction.seller_invoice_*.

Los vínculos con el pedido y la factura (sale_order_id / invoice_id) solo se
escriben cuando la factura quedó CONTABILIZADA. Si algo falla, el intento se
deshace entero (savepoint), el cobro queda en «Error» con el motivo a la vista
y el cron lo reintenta. Antes el pedido quedaba enlazado aunque la factura
fallara, y como el guardia era «ya tiene pedido», nunca se reintentaba.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Intentos automáticos antes de dejar el cobro para revisión manual.
MAX_AUTO_ATTEMPTS = 5


class ShrimpCharge(models.Model):
    _name = "shrimp.charge"
    _inherit = ["mail.thread", "shrimp.uuid.mixin"]
    _description = "Cobro de servicio de la plataforma"
    _order = "create_date desc, id desc"
    _rec_name = "name"

    name = fields.Char(string="Referencia", default=lambda self: _("Nuevo"), copy=False)
    charge_type = fields.Selection(
        [
            ("commission", "Comisión por venta"),
            ("verification_fee", "Honorario de verificación"),
            ("copack_platform", "Comisión de empaque"),
            ("check_fee", "Chequeo de producto (legado)"),
        ],
        string="Tipo de cobro", required=True, default="commission", index=True,
        tracking=True)
    payer_partner_id = fields.Many2one(
        "res.partner", string="Paga", index=True, tracking=True,
        help="A quién se le factura el servicio.")
    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True,
        default=lambda self: self.env.company)
    origin = fields.Char(
        string="Documento origen",
        help="Referencia legible de lo que originó el cobro (compra, "
             "verificación u orden de empaque).")
    description = fields.Char(string="Concepto")

    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Transacción", ondelete="cascade", index=True)
    seller_partner_id = fields.Many2one("res.partner", string="Vendedor", index=True)
    buyer_partner_id = fields.Many2one("res.partner", string="Comprador")
    product_id = fields.Many2one("shrimp.product", string="Producto")
    qty = fields.Float(string="Cantidad")
    uom_id = fields.Many2one("shrimp.uom", string="Unidad")
    rate_cents = fields.Float(
        string="Tarifa (centavos por unidad)",
        help="Centavos cobrados por cada unidad vendida al momento del cobro.")
    invoice_qty = fields.Float(
        string="Cantidad a facturar", default=1.0,
        help="Cantidad de la línea de factura (en la comisión, las unidades vendidas).")
    unit_amount = fields.Monetary(
        string="Precio unitario", currency_field="currency_id",
        help="Precio de la línea de factura antes de impuestos.")
    amount = fields.Monetary(string="Importe (sin impuestos)", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", default=lambda self: self.env.company.currency_id)
    date = fields.Datetime(string="Fecha del cobro", default=fields.Datetime.now)
    service_product_id = fields.Many2one(
        "product.product", string="Servicio facturado", readonly=True)

    state = fields.Selection(
        [
            ("pending", "Por facturar"),
            ("invoiced", "Facturado"),
            ("error", "Error al facturar"),
            ("to_credit", "Por acreditar"),
            ("credited", "Acreditado (nota de crédito)"),
            ("cancelled", "Anulado"),
        ],
        string="Estado", default="pending", required=True, index=True, tracking=True,
        copy=False)
    invoice_error = fields.Text(string="Último error", readonly=True, copy=False)
    invoice_attempts = fields.Integer(string="Intentos", readonly=True, copy=False)
    last_attempt_at = fields.Datetime(string="Último intento", readonly=True, copy=False)
    cancel_reason = fields.Char(string="Motivo de anulación", readonly=True, copy=False)

    # Integración con Ventas / Contabilidad de Odoo. Solo se escriben cuando
    # la factura quedó contabilizada.
    sale_order_id = fields.Many2one(
        "sale.order", string="Pedido de venta", readonly=True, copy=False)
    invoice_id = fields.Many2one(
        "account.move", string="Factura", readonly=True, copy=False)
    invoice_state = fields.Selection(
        related="invoice_id.state", string="Estado de la factura", readonly=True)
    refund_id = fields.Many2one(
        "account.move", string="Nota de crédito", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Creación
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.charge") or _("Nuevo")
            if not vals.get("payer_partner_id") and vals.get("seller_partner_id") \
                    and vals.get("charge_type", "commission") == "commission":
                vals["payer_partner_id"] = vals["seller_partner_id"]
            if "amount" in vals and "unit_amount" not in vals:
                cantidad = vals.get("invoice_qty") or 1.0
                vals["unit_amount"] = (vals["amount"] or 0.0) / cantidad
        return super().create(vals_list)

    @api.model
    def _register_charge(self, vals):
        """Crea un cobro y lo intenta facturar al momento.

        Punto único de entrada para todos los tipos de cobro. Con el contexto
        ``shrimp_charge_no_invoice`` solo se registra (lo factura el cron).
        """
        charge = self.sudo().create(vals)
        if not self.env.context.get("shrimp_charge_no_invoice"):
            charge._try_invoice()
        return charge

    @api.model
    def register_for_transaction(self, transaction, qty=None):
        """Compatibilidad: la comisión ya no se registra desde fuera.

        La registra la propia transacción al confirmarse; esto devuelve la que
        exista (o la crea si la compra ya está confirmada y no la tiene).
        """
        return self._register_commission(transaction, qty)

    @api.model
    def _register_commission(self, transaction, qty=None):
        """Comisión de una compra confirmada. Idempotente: una por compra."""
        if not transaction:
            return self.browse()
        transaction = transaction.sudo()
        existente = self.sudo().search([
            ("transaction_id", "=", transaction.id),
            ("charge_type", "=", "commission"),
            ("state", "not in", ("cancelled", "credited")),
        ], limit=1)
        if existente:
            return existente
        product = transaction.product_id
        # La tarifa vive en la tabla de Unidades de medida; cada unidad tiene su
        # propio valor (p. ej. la libra puede cobrar distinto que el millar).
        rate = float(product.uom_id.sudo().commission_cents or 0.0)
        if rate <= 0:
            return self.browse()
        qty = transaction.transaction_qty if qty is None else qty
        amount = round((qty or 0.0) * rate / 100.0, 2)
        return self._register_charge({
            "charge_type": "commission",
            "transaction_id": transaction.id,
            "seller_partner_id": transaction.seller_partner_id.id,
            "buyer_partner_id": transaction.buyer_partner_id.id,
            "payer_partner_id": transaction.seller_partner_id.id,
            "product_id": product.id,
            "qty": qty,
            "uom_id": product.uom_id.id,
            "rate_cents": rate,
            "invoice_qty": qty or 1.0,
            "unit_amount": rate / 100.0,
            "amount": amount,
            "origin": transaction.name,
            "description": _("Comisión marketplace – %s") % (product.display_name or ""),
        })

    # ------------------------------------------------------------------
    # Productos de servicio
    # ------------------------------------------------------------------
    @api.model
    def _service_product_specs(self):
        """Tipo de cobro -> (xmlid, nombre, código interno).

        Los módulos que agregan tipos de cobro amplían este diccionario. Los
        xmlids se conservan de versiones anteriores para no duplicar productos.
        """
        return {
            "commission": ("shrimp_marketplace.product_marketplace_commission",
                           "Comisión Marketplace", "COMISION-MKT"),
            "check_fee": ("shrimp_marketplace.product_marketplace_check",
                          "Chequeo de producto", "CHEQUEO-MKT"),
        }

    @api.model
    def _default_service_taxes(self, company):
        """Impuesto de venta por defecto de la compañía (IVA) para servicios.

        Configurable: es el «Impuesto de venta por defecto» de Contabilidad,
        y una vez creado el producto de servicio se puede cambiar en su ficha.
        """
        tax = company.account_sale_tax_id
        return tax if tax and tax.type_tax_use == "sale" else self.env["account.tax"]

    @api.model
    def _get_service_product(self, charge_type, company=None):
        """Producto de servicio del tipo de cobro (lo crea la primera vez).

        Se crea en tiempo de ejecución para no depender del orden de carga de
        módulos (website_sale impone publish_date en el product.template).
        """
        company = company or self.env.company
        xmlid, nombre, codigo = self._service_product_specs()[charge_type]
        product = self.env.ref(xmlid, raise_if_not_found=False)
        if product:
            return product
        module, name = xmlid.split(".", 1)
        tmpl = self.env["product.template"].sudo().create({
            "name": nombre,
            "type": "service",
            "invoice_policy": "order",
            "list_price": 0.0,
            "sale_ok": True,
            "purchase_ok": charge_type == "verification_fee",
            "default_code": codigo,
            "taxes_id": [(6, 0, self._default_service_taxes(company).ids)],
        })
        variant = tmpl.product_variant_id
        self.env["ir.model.data"].sudo().create({
            "name": name, "module": module, "model": "product.product",
            "res_id": variant.id, "noupdate": True,
        })
        return variant

    # ------------------------------------------------------------------
    # Facturación
    # ------------------------------------------------------------------
    def _prepare_sale_order_vals(self, product):
        self.ensure_one()
        return {
            "partner_id": self.payer_partner_id.id,
            "company_id": self.company_id.id,
            "client_order_ref": self.name,
            "origin": self.origin or self.name,
            # Sin tax_ids: la línea toma los impuestos del producto de servicio
            # (IVA por defecto de la compañía), mapeados por la posición fiscal.
            "order_line": [(0, 0, {
                "product_id": product.id,
                "name": self.description or product.display_name,
                "product_uom_qty": self.invoice_qty or 1.0,
                "price_unit": self.unit_amount or 0.0,
            })],
        }

    def _prepare_service_invoice(self, invoice):
        """Gancho: ajusta la factura en borrador antes de contabilizarla.

        Lo amplía el puente SRI (shrimp_l10n_ec) para elegir diario, punto de
        emisión, tipo de documento y forma de pago. Aquí no hace nada.
        """
        return invoice

    def _after_service_invoice_posted(self, invoice):
        """Gancho tras contabilizar (p. ej. encolar el comprobante en el SRI)."""
        return True

    def _create_invoice_documents(self):
        """Pedido confirmado + factura contabilizada. Lanza si algo falla."""
        self.ensure_one()
        if not self.payer_partner_id:
            raise UserError(_("El cobro %s no tiene a quién facturar.") % self.name)
        if (self.amount or 0.0) <= 0:
            raise UserError(_("El cobro %s no tiene importe.") % self.name)
        product = self._get_service_product(self.charge_type, self.company_id)
        order = self.env["sale.order"].sudo().with_company(self.company_id).create(
            self._prepare_sale_order_vals(product))
        order.action_confirm()
        invoice = order._create_invoices()
        if not invoice:
            raise UserError(_("Ventas no generó factura para el cobro %s.") % self.name)
        invoice = invoice[:1]
        self._prepare_service_invoice(invoice)
        invoice.action_post()
        if invoice.state != "posted":
            raise UserError(_("La factura del cobro %s no quedó contabilizada.") % self.name)
        return order, invoice, product

    def _try_invoice(self):
        """Factura los cobros pendientes o con error. Nunca lanza."""
        for charge in self.sudo():
            if charge.state not in ("pending", "error") or charge.invoice_id:
                continue
            if (charge.amount or 0.0) <= 0:
                charge.write({"state": "cancelled",
                              "cancel_reason": _("Importe cero: nada que facturar.")})
                continue
            ahora = fields.Datetime.now()
            intentos = charge.invoice_attempts + 1
            try:
                with self.env.cr.savepoint():
                    order, invoice, product = charge._create_invoice_documents()
            except Exception as e:  # noqa: BLE001 - nunca debe tumbar la compra
                _logger.warning("No se pudo facturar el cobro %s: %s", charge.name, e)
                charge.write({
                    "state": "error",
                    "invoice_error": str(e)[:2000],
                    "invoice_attempts": intentos,
                    "last_attempt_at": ahora,
                })
                continue
            charge.write({
                "state": "invoiced",
                "sale_order_id": order.id,
                "invoice_id": invoice.id,
                "service_product_id": product.id,
                "invoice_error": False,
                "invoice_attempts": intentos,
                "last_attempt_at": ahora,
            })
            try:
                with self.env.cr.savepoint():
                    charge._after_service_invoice_posted(invoice)
            except Exception as e:  # noqa: BLE001
                _logger.warning("Cobro %s: la factura %s se contabilizó pero el paso "
                                "posterior falló: %s", charge.name, invoice.name, e)
                charge.message_post(body=_(
                    "La factura %(f)s quedó contabilizada, pero no se pudo completar "
                    "el envío electrónico: %(e)s") % {"f": invoice.name, "e": e})
        return True

    @api.model
    def _shrimp_max_auto_attempts(self):
        """Intentos automáticos de facturación (Ajustes › CamaronMarket ›
        Facturación; shrimp_marketplace.charge_max_attempts, 5 por defecto)."""
        return self.env["shrimp.settings"].get_int(
            "shrimp_marketplace.charge_max_attempts", MAX_AUTO_ATTEMPTS, minimum=1)

    @api.model
    def _cron_retry_invoices(self):
        """Reintenta los cobros pendientes o con error (y las notas de crédito)."""
        pendientes = self.sudo().search([
            ("state", "in", ("pending", "error")),
            ("invoice_attempts", "<", self._shrimp_max_auto_attempts()),
        ], limit=200)
        pendientes._try_invoice()
        self.sudo().search([("state", "=", "to_credit")], limit=200)._try_credit()
        return True

    def action_retry_invoice(self):
        """Botón: reintentar ahora (también tras superar los intentos automáticos)."""
        self.filtered(lambda c: c.state in ("pending", "error"))._try_invoice()
        self.filtered(lambda c: c.state == "to_credit")._try_credit()
        return True

    # Compatibilidad con el botón y las llamadas antiguas.
    def action_generate_sale_documents(self):
        return self.action_retry_invoice()

    def _create_sale_documents(self):
        return self._try_invoice()

    # ------------------------------------------------------------------
    # Anulación y nota de crédito
    # ------------------------------------------------------------------
    def _prepare_refund_vals(self, reason):
        self.ensure_one()
        return {"ref": _("Anulación %(c)s: %(m)s") % {"c": self.name, "m": reason or ""},
                "date": fields.Date.context_today(self)}

    def _prepare_service_refund(self, refund, reason):
        """Gancho del puente SRI (motivo de la nota de crédito, punto...)."""
        return refund

    def _try_credit(self):
        """Emite la nota de crédito de los cobros ya facturados que se anulan."""
        for charge in self.sudo().filtered(lambda c: c.state == "to_credit"):
            invoice = charge.invoice_id
            if not invoice or invoice.state != "posted":
                charge.write({"state": "cancelled"})
                continue
            try:
                with self.env.cr.savepoint():
                    refund = invoice._reverse_moves(
                        [charge._prepare_refund_vals(charge.cancel_reason)], cancel=False)
                    charge._prepare_service_refund(refund, charge.cancel_reason)
                    refund.action_post()
            except Exception as e:  # noqa: BLE001
                _logger.warning("No se pudo acreditar el cobro %s: %s", charge.name, e)
                charge.write({"invoice_error": str(e)[:2000],
                              "last_attempt_at": fields.Datetime.now()})
                continue
            charge.write({"state": "credited", "refund_id": refund.id,
                          "invoice_error": False})
        return True

    def action_cancel_charge(self, reason=None):
        """Anula el cobro: si aún no se facturó, se descarta; si ya tenía
        factura contabilizada, queda «por acreditar» y se emite la nota de
        crédito (o la reintenta el cron)."""
        for charge in self.sudo():
            if charge.state in ("cancelled", "credited", "to_credit"):
                continue
            if charge.state == "invoiced" and charge.invoice_id:
                charge.write({"state": "to_credit", "cancel_reason": reason or False})
                charge._try_credit()
            else:
                charge.write({"state": "cancelled", "cancel_reason": reason or False})
        return True

    def _reassign_payer(self, new_payer, reason=None):
        """Cambia quién paga y devuelve el cobro vigente.

        Sin documento fiscal todavía, basta con cambiar el destinatario. Con
        factura contabilizada se emite la nota de crédito y se crea un cobro
        igual al nuevo pagador.
        """
        self.ensure_one()
        if not new_payer or new_payer == self.payer_partner_id \
                or self.state in ("cancelled", "credited", "to_credit"):
            return self
        if self.state in ("pending", "error") and not self.invoice_id:
            self.sudo().write({"payer_partner_id": new_payer.id, "state": "pending",
                               "invoice_attempts": 0})
            self._try_invoice()
            return self
        vals = self.copy_data({"payer_partner_id": new_payer.id})[0]
        vals["name"] = _("Nuevo")
        self.action_cancel_charge(reason or _("Cambio de pagador"))
        return self._register_charge(vals)

    # ------------------------------------------------------------------
    # Pagos al proveedor del servicio (verificador): un solo sitio
    # ------------------------------------------------------------------
    @api.model
    def _create_vendor_bill(self, partner, product, label, amount, ref, company=None):
        """Factura de PROVEEDOR por un servicio que la plataforma liquida a un
        tercero (hoy, la parte del honorario que cobra el verificador).

        POR CONFIRMAR SRI: según el caso, este gasto se sustenta con la factura
        del verificador (registrada con su número y autorización) o con una
        liquidación de compra (documento 03), y puede llevar retenciones. No se
        inventa esa lógica aquí: si el diario de compras usa documentos de la
        localización, la factura queda en BORRADOR para que la complete
        contabilidad; si no, se contabiliza como antes.
        """
        company = company or self.env.company
        move = self.env["account.move"].sudo().with_company(company).create({
            "move_type": "in_invoice",
            "partner_id": partner.id,
            "invoice_date": fields.Date.context_today(self),
            "ref": ref,
            "invoice_line_ids": [(0, 0, {
                "product_id": product.id,
                "name": label,
                "quantity": 1.0,
                "price_unit": amount,
            })],
        })
        if getattr(move.journal_id, "l10n_latam_use_documents", False):
            move.message_post(body=_(
                "Pendiente de contabilidad (por confirmar SRI: factura del "
                "proveedor o liquidación de compra, y retenciones). Registre el "
                "número y la autorización del comprobante del proveedor antes "
                "de contabilizar."))
        else:
            move.action_post()
        return move

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------
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
