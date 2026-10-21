"""Facturación de los cobros de la plataforma (shrimp.charge).

- Importe exacto: la factura suma EXACTAMENTE el importe del cobro, aunque la
  tarifa por unidad tenga más decimales que el precio unitario de Odoo (0,5
  ctv/lb salía al doble; 0,2 ctv por unidad salía en cero). También la nota
  de crédito.
- Compañía: el cobro, el pedido, la factura, la nota de crédito y la factura
  de proveedor van en la compañía de facturación de la plataforma, no en la
  compañía activa.
- IVA: una línea de servicio nunca sale sin IVA; sin IVA posible, el cobro
  queda bloqueado con el motivo.
"""
from odoo.tests import TransactionCase, tagged

from .common import crear_socios, facturar_en_compania_activa, producto


@tagged("post_install", "-at_install")
class TestFacturacionCobros(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Charge = cls.env["shrimp.charge"].sudo()
        cls.s = crear_socios(cls.env, sufijo="fac")
        cls.libra = cls.env.ref("shrimp_marketplace.uom_libra")
        cls.unidad = cls.env.ref("shrimp_marketplace.uom_unidad")
        cls.company_a = cls.env.company
        # Sin compañía de facturación configurada en la base: manda la activa
        # (cada prueba configura la suya si la necesita).
        facturar_en_compania_activa(cls.env)

    def _comprar(self, uom, tarifa, cantidad, nombre):
        uom.sudo().commission_cents = tarifa
        lote = producto(self.env, self.s["sem"], nombre=nombre, uom_id=uom.id,
                        initial_qty=10000.0)
        tx = lote.execute_purchase_flow(self.s["lab"], cantidad)["transaction"]
        cobro = tx.charge_ids.filtered(lambda c: c.charge_type == "commission")
        self.assertEqual(len(cobro), 1)
        return tx, cobro

    def _assert_factura_exacta(self, cobro, importe):
        self.assertEqual(cobro.amount, importe)
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        factura = cobro.invoice_id
        self.assertEqual(factura.state, "posted")
        self.assertAlmostEqual(factura.amount_untaxed, cobro.amount, places=2)
        linea = factura.invoice_line_ids.filtered(lambda l: l.display_type == "product")
        self.assertEqual(len(linea), 1)
        self.assertEqual(linea.quantity, 1.0)
        self.assertAlmostEqual(linea.price_subtotal, importe, places=2)
        self.assertTrue(linea.tax_ids, "la línea de servicio no puede salir sin IVA")
        return factura, linea

    # ------------------------------------------------------------------
    # Importe exacto
    # ------------------------------------------------------------------
    def test_comision_medio_centavo_cantidad_no_redonda(self):
        tx, cobro = self._comprar(self.libra, 0.5, 3920.05, "Lote 0,5 ctv")
        # 3.920,05 lb × 0,005 = 19,60025 -> 19,60 (antes la factura decía 39,20).
        factura, linea = self._assert_factura_exacta(cobro, 19.60)
        self.assertEqual((cobro.invoice_qty, cobro.unit_amount), (1.0, 19.60))
        self.assertIn("ctv", linea.name)
        self.assertIn(tx.shrimp_fmt_num(3920.05, 2), linea.name)
        self.assertIn(tx.shrimp_fmt_num(0.5, 1), linea.name)
        # La nota de crédito también por el importe exacto.
        cobro.action_cancel_charge("Prueba de anulación")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        self.assertAlmostEqual(cobro.refund_id.amount_untaxed, cobro.amount, places=2)
        self.assertEqual(cobro.refund_id.company_id, cobro.company_id)

    def test_comision_dos_decimas_por_unidad(self):
        _tx, cobro = self._comprar(self.unidad, 0.2, 1234.0, "Lote 0,2 ctv")
        # 1.234 × 0,002 = 2,468 -> 2,47 (antes 0,002 se redondeaba a 0,00).
        self._assert_factura_exacta(cobro, 2.47)

    def test_cobro_con_forma_antigua_se_normaliza(self):
        """Quien todavía mande cantidad × precio unitario (demo, scripts)
        también factura el importe exacto."""
        cobro = self.Charge.with_context(shrimp_charge_no_invoice=True)._register_charge({
            "charge_type": "commission", "payer_partner_id": self.s["sem"].id,
            "seller_partner_id": self.s["sem"].id, "qty": 392.006,
            "uom_id": self.libra.id, "rate_cents": 0.5,
            "invoice_qty": 392.006, "unit_amount": 0.005, "amount": 1.96,
            "description": "Comisión marketplace – histórico"})
        self.assertEqual((cobro.invoice_qty, cobro.unit_amount), (1.0, 1.96))
        cobro._try_invoice()
        self._assert_factura_exacta(cobro, 1.96)

    def test_entrega_parcial_recalcula_exacto(self):
        """La comisión recalculada sobre lo entregado también cuadra."""
        tx, cobro = self._comprar(self.libra, 0.5, 3920.05, "Lote parcial")
        nueva = self.Charge._register_commission(tx, qty=1234.57)
        if nueva == cobro:
            # Sigue la vigente: se anula y se registra la de lo entregado.
            cobro.action_cancel_charge("Entrega parcial")
            nueva = self.Charge._register_commission(tx, qty=1234.57)
        self._assert_factura_exacta(nueva, 6.17)

    # ------------------------------------------------------------------
    # Compañía e IVA
    # ------------------------------------------------------------------
    def _compania_b(self):
        if "shrimp_service_journal_id" not in self.env["res.company"]._fields:
            self.skipTest("Sin shrimp_l10n_ec no hay compañía de facturación configurable.")
        usd = self.env.ref("base.USD")
        b = self.env["res.company"].create({"name": "Plataforma B (facturación)",
                                            "currency_id": usd.id})
        self.env.user.company_ids |= b
        self.env["account.chart.template"].try_loading("generic_coa", company=b,
                                                       install_demo=False)
        diario = self.env["account.journal"].search(
            [("company_id", "=", b.id), ("type", "=", "sale")], limit=1)
        self.assertTrue(diario)
        b.shrimp_service_journal_id = diario
        return b

    def test_compania_de_facturacion_en_todos_los_documentos(self):
        b = self._compania_b()
        self.assertNotEqual(self.env.company, b)
        self.assertEqual(self.Charge._shrimp_billing_company(), b)
        cobro = self.Charge.with_context(shrimp_charge_no_invoice=True)._register_charge({
            "charge_type": "verification_fee", "payer_partner_id": self.s["lab"].id,
            "amount": 30.0, "description": "Verificación en campo – prueba"}) \
            if "verification_fee" in self.Charge._service_product_specs() else \
            self.Charge.with_context(shrimp_charge_no_invoice=True)._register_charge({
                "charge_type": "commission", "payer_partner_id": self.s["lab"].id,
                "amount": 30.0, "description": "Comisión – prueba"})
        self.assertEqual(cobro.company_id, b)
        self.assertEqual(cobro.currency_id, b.currency_id)
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.sale_order_id.company_id, b)
        self.assertEqual(cobro.invoice_id.company_id, b)
        self.assertEqual(cobro.invoice_id.journal_id.company_id, b)
        cobro.action_cancel_charge("Prueba")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        self.assertEqual(cobro.refund_id.company_id, b)
        # Factura de proveedor (verificador): en la compañía del cobro, y por
        # defecto en la de facturación (no en la activa).
        servicio = self.Charge._get_service_product(cobro.charge_type, b)
        for compania in (cobro.company_id, None):
            gasto = self.Charge._create_vendor_bill(
                self.s["lab2"], servicio, "Honorario", 12.0, "Prueba", company=compania)
            self.assertEqual(gasto.company_id, b)
            self.assertEqual(gasto.journal_id.company_id, b)

    def test_servicio_sin_iva_en_la_compania(self):
        b = self._compania_b()
        cobro = self.Charge.with_context(shrimp_charge_no_invoice=True)._register_charge({
            "charge_type": "commission", "payer_partner_id": self.s["sem"].id,
            "amount": 8.0, "description": "Comisión – sin IVA en el producto"})
        servicio = self.Charge._get_service_product("commission", b)
        # El producto no tiene IVA en B (nació en otra compañía).
        servicio.sudo().taxes_id = servicio.sudo().taxes_id.filtered(
            lambda t: t.company_id != b)
        self.assertTrue(b.account_sale_tax_id)
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        linea = cobro.invoice_id.invoice_line_ids.filtered(lambda l: l.display_type == "product")
        self.assertEqual(linea.tax_ids, b.account_sale_tax_id)
        self.assertGreater(cobro.invoice_id.amount_tax, 0.0)
        # Sin IVA posible (ni en el producto ni en la compañía): bloqueado, sin
        # gastar intentos y con el motivo a la vista.
        b.account_sale_tax_id = False
        otro = self.Charge._register_charge({
            "charge_type": "commission", "payer_partner_id": self.s["sem"].id,
            "amount": 8.0, "description": "Comisión – bloqueada"})
        self.assertEqual(otro.state, "pending")
        self.assertFalse(otro.invoice_id)
        self.assertEqual(otro.invoice_attempts, 0)
        self.assertIn("no tiene IVA", otro.invoice_error)
