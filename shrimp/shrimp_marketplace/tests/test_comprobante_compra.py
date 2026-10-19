"""«¡Compra realizada!» y comprobante de compra (PDF) — rediseño 2026-10.

* La pantalla se pinta para el comprador (y el vendedor) y responde 403 a un
  tercero; conserva el comprobante, la trazabilidad, «Mis compras», «Seguir
  comprando» y suma «Mi panel».
* El comprobante: application/pdf para el comprador, 404 para un tercero; sigue
  diciendo «SIN VALOR TRIBUTARIO» y no carga hojas del sitio (modo oscuro).
* Importes en dos pasos: solo los servicios que paga el COMPRADOR (la comisión
  la paga el vendedor y no sale), sin inventar impuestos.
"""
from odoo.tests import HttpCase, TransactionCase, tagged

from .common import CLAVE, crear_socios, producto


@tagged("post_install", "-at_install")
class TestComprobanteCompraHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "cpb")
        cls.lote = producto(cls.env, cls.s["sem"], nombre="Lote comprobante")
        cls.tx = cls.lote.execute_purchase_flow(cls.s["lab"], 5.0)["transaction"]

    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)

    def test_pantalla_gracias(self):
        self._login("lab")
        r = self.url_open("/marketplace/thanks/%s?paid=1" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        for texto in ("¡Compra realizada!", "Pago recibido", "Resumen del pago",
                      "Próximos pasos", "Factura del vendedor",
                      "/marketplace/purchases/%s/invoice/pdf?download=1" % self.tx.uuid_ref,
                      "/marketplace/purchases/%s/traceability" % self.tx.uuid_ref,
                      'href="/marketplace/purchases"', 'href="/my/dashboard"',
                      'href="/marketplace"', "tk-mbar"):
            self.assertIn(texto, r.text, texto)
        # Compra directa: el comprador no paga servicio (la comisión es del vendedor).
        self.assertIn("Sin cargo para ti", r.text)
        # El vendedor también la puede abrir; un tercero no.
        self._login("sem")
        self.assertEqual(self.url_open("/marketplace/thanks/%s" % self.tx.uuid_ref).status_code, 200)
        self._login("lab2")
        self.assertEqual(self.url_open("/marketplace/thanks/%s" % self.tx.uuid_ref).status_code, 403)
        self.assertEqual(self.url_open("/marketplace/thanks/no-existe").status_code, 404)

    def test_pdf_comprobante(self):
        self._login("lab")
        r = self.url_open("/marketplace/purchases/%s/invoice/pdf" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("Content-Type"), "application/pdf")
        # En modo prueba el informe sale como HTML: se revisa el contenido.
        self.assertIn("SIN VALOR TRIBUTARIO", r.text)
        self.assertIn("Comprobante de compra", r.text)
        self.assertIn("no es una factura", r.text)
        self.assertIn(self.tx.name, r.text)
        self.assertNotIn("shrimp_dark", r.text, "el PDF no carga el modo oscuro del sitio")
        self.assertNotIn("data-bs-theme", r.text)
        self._login("lab2")
        r = self.url_open("/marketplace/purchases/%s/invoice/pdf" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 404)


@tagged("post_install", "-at_install")
class TestComprobanteImportes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "cpi")
        cls.lote = producto(cls.env, cls.s["sem"], nombre="Lote importes")
        cls.tx = cls.lote.execute_purchase_flow(cls.s["lab"], 4.0)["transaction"]

    def test_importes_en_dos_pasos(self):
        tx = self.tx
        Charge = self.env["shrimp.charge"].sudo().with_context(shrimp_charge_no_invoice=True)
        # Un servicio que paga el comprador y otro que paga el vendedor.
        Charge._register_charge({
            "charge_type": "check_fee", "transaction_id": tx.id,
            "payer_partner_id": tx.buyer_partner_id.id, "amount": 12.5, "origin": tx.name})
        Charge._register_charge({
            "charge_type": "commission", "transaction_id": tx.id,
            "seller_partner_id": tx.seller_partner_id.id,
            "payer_partner_id": tx.seller_partner_id.id, "amount": 99.0, "origin": tx.name})
        a = tx.shrimp_receipt_amounts()
        self.assertEqual([l["untaxed"] for l in a["service_lines"]], [12.5])
        self.assertFalse(a["service_taxes_known"], "sin factura contabilizada no se calcula IVA")
        self.assertIsNone(a["service_tax"])
        self.assertAlmostEqual(a["product_total"], round(tx.amount_total, 2))
        self.assertAlmostEqual(a["grand_total"], round(tx.amount_total + 12.5, 2))
        self.assertFalse(a["verified"])

    def test_sin_token_no_hay_enlace_publico(self):
        tx = self.tx.sudo()
        if "trace_token" not in tx._fields:
            self.assertFalse(tx.shrimp_receipt_public_url())
            return
        tx.trace_token = False
        self.assertFalse(tx.shrimp_receipt_public_url())
        self.assertFalse(tx.trace_token, "imprimir no crea tokens")
        self.assertTrue(tx.shrimp_receipt_logo_uri().startswith("data:image/png;base64,"))
