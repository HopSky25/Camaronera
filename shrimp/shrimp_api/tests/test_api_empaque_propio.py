"""Empaque propio por la API: POST /copack/orders (scope copack:write) y la
bandera self_packing en la orden y en la trazabilidad pública."""
from datetime import date

from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiEmpaquePropio(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.farm_a.write({"shrimp_razon_social": "Camaronera A API S.A.", "shrimp_ubicacion": "Guayas",
                          "pack_codigo_establecimiento": "EST-API-SELF"})
        env["shrimp.partner.role"].sudo().create({
            "partner_id": cls.farm_a.id, "role": "maquilador", "state": "approved"})
        grade = env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)
        def cosecha(socio, nombre):
            return env["shrimp.product"].create({
                "name": nombre, "seller_partner_id": socio.id, "seller_role": "camaronera",
                "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
                "uom_id": env.ref("shrimp_marketplace.uom_libra").id, "presentation": "entero",
                "size_grade_id": grade.id, "initial_qty": 30000.0, "price": 2.3,
                "state": "published", "expected_delivery_date": date.today()})
        cls.cosecha_a = cosecha(cls.farm_a, "Cosecha API A")
        cls.cosecha_b = cosecha(cls.farm_b, "Cosecha API B")

    def test_empaque_propio_por_api(self):
        r = self.api("POST", "/copack/orders", raw=self.raw_a, body={
            "quantity_lb": 10000, "product": self.cosecha_a.uuid_ref,
            "supplies_notes": "Insumos propios"})
        self.assertEqual(r.status_code, 201, r.text)
        o = r.json()
        self.assertTrue(o["self_packing"])
        self.assertEqual(o["client"]["id"], o["copacker"]["id"])
        oid = o["id"]
        r = self.api("POST", "/copack/orders/%s:reception" % oid, raw=self.raw_a,
                     body={"received_lb": 10000, "supplies_received": True})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.api("POST", "/copack/orders/%s:packing" % oid, raw=self.raw_a,
                     body={"packed_lb": 9950, "boxes": 377, "packed_presentation": "entero"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["acceptance_state"], "na")
        self.assertEqual(r.json()["signatures"], [])
        r = self.api("POST", "/copack/orders/%s:close" % oid, raw=self.raw_a)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["state"], "closed")
        orden = self.env["shrimp.copack.order"].search([("uuid_ref", "=", oid)])
        self.assertFalse(orden.charge_ids)
        self.assertTrue(orden.packed_lot_id)
        r = self.api("GET", "/copack/orders?state=closed", raw=self.raw_a)
        self.assertTrue(any(x["self_packing"] for x in r.json()["data"]))

        # La venta posterior: la trazabilidad pública lo rotula.
        tx = self.cosecha_a.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.packer, 29950.0)["transaction"]
        tx._ensure_trace_token()
        r = self.api("GET", "/public/traceability/%s" % tx.trace_token)
        self.assertEqual(r.status_code, 200, r.text)
        propio = [p for p in r.json()["packing"] if p.get("self_packing")]
        self.assertEqual(len(propio), 1)
        self.assertIn("Empaque propio — empacado por Camaronera A API", propio[0]["label"])
        self.assertEqual(propio[0]["establishment_code"], "EST-API-SELF")
        page = self.url_open("/t/%s" % tx.trace_token)
        self.assertIn("Empaque propio — empacado por", page.text)
        # Pantalla privada de trazabilidad del comprador.
        self.authenticate("emp.api", "Clave-api-123")
        page = self.url_open("/marketplace/purchases/%s/traceability" % tx.uuid_ref)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Empaque propio — empacado por", page.text)
        self.assertIn("EST-API-SELF", page.text)

    def test_sin_perfil_o_ajeno(self):
        # Sin perfil Maquilador: 403.
        r = self.api("POST", "/copack/orders", raw=self.raw_b, body={
            "quantity_lb": 1000, "product": self.cosecha_b.uuid_ref})
        self.assertProblem(r, 403)
        self.assertIn("perfil Maquilador", r.json()["detail"])
        # Con perfil, pero un lote ajeno: 422.
        r = self.api("POST", "/copack/orders", raw=self.raw_a, body={
            "quantity_lb": 1000, "product": self.cosecha_b.uuid_ref})
        self.assertIn(r.status_code, (400, 422), r.text)
        # Ni lote ni compra: 422.
        r = self.api("POST", "/copack/orders", raw=self.raw_a, body={"quantity_lb": 1000})
        self.assertIn(r.status_code, (400, 422), r.text)
