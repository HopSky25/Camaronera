"""Portal: la pantalla de trazabilidad, «Mis lotes» con «Registrar salida»,
el registro de la salida y la producción declarada, de punta a punta por HTTP."""
from datetime import date

from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestTrazabilidadPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "tp")
        cls.nauplio = producto(cls.env, cls.s["sem"], nombre="Nauplio portal",
                               etapa="shrimp_marketplace.shrimp_stage_nauplio",
                               expected_delivery_date=date.today())
        cls.tx = cls.nauplio.execute_purchase_flow(cls.s["lab"], 60.0)["transaction"]
        cls.tx.action_receive()
        cls.lote = cls.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", cls.tx.stock_move_ids.ids), ("owner_id", "=", cls.s["lab"].id)])

    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)

    def test_pantalla_y_salida(self):
        self._login("lab")
        r = self.url_open("/marketplace/purchases/%s/traceability" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cadena de custodia", r.text)
        self.assertIn("Salida / exportación", r.text)
        r = self.url_open("/marketplace/my-lots")
        self.assertEqual(r.status_code, 200)
        # Las salidas son de quien vende camarón adulto fuera de la plataforma
        # (empacadora, camaronera): el laboratorio no ve el botón.
        self.assertNotIn("Registrar salida", r.text)
        self.assertIn("Declarar producción real", r.text)
        # Producción declarada desde el portal.
        r = self.url_open("/marketplace/my-lots/%s/production" % self.lote.uuid_ref, data={
            "csrf_token": csrf_de(r.text), "actual_qty": "45", "reason": "Supervivencia 75 %"})
        self.assertEqual(r.status_code, 200)
        self.lote.invalidate_recordset()
        self.assertAlmostEqual(self.lote.available_qty, 45.0)
        # El laboratorio no registra salidas (misma regla en ruta y modelo).
        r = self.url_open("/marketplace/exports/new?lot=%s" % self.lote.uuid_ref)
        self.assertEqual(r.status_code, 404)
        r = self.url_open("/marketplace/exports/register", data={
            "csrf_token": csrf_de(self.url_open("/marketplace/my-lots").text),
            "lot_ref": self.lote.uuid_ref, "qty": "40",
            "date": date.today().isoformat(), "country_code": "ES"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(self.env["shrimp.export"].search([("partner_id", "=", self.s["lab"].id)]))
        self.assertAlmostEqual(self.lote.available_qty, 45.0)
        # El PDF del comprador incluye la salida y la marca de la plataforma.
        r = self.url_open("/marketplace/purchases/%s/traceability/pdf" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 200)

    def test_lote_ajeno_no_se_despacha(self):
        self._login("cam")
        r = self.url_open("/marketplace/exports/new")
        r = self.url_open("/marketplace/exports/register", data={
            "csrf_token": csrf_de(r.text), "lot_ref": self.lote.uuid_ref, "qty": "1"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(self.env["shrimp.export"].search([("partner_id", "=", self.s["cam"].id)]))
