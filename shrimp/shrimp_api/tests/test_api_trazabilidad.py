"""Trazabilidad pública y salidas / exportaciones por API (hallazgos 3, 7, 8,
10 y 11 del escenario de trazabilidad)."""
from datetime import date, timedelta

from odoo.tests import tagged

from .common import FORBIDDEN_KEYS, ApiCase, int_ids, walk_keys


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiTrazabilidad(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.libra = env.ref("shrimp_marketplace.uom_libra")
        cls.adult = env["shrimp.product"].create({
            "name": "Camarón API", "seller_partner_id": cls.farm_a.id, "seller_role": "camaronera",
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": cls.libra.id, "presentation": "entero",
            "size_grade_id": env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1).id,
            "initial_qty": 10000.0, "price": 2.2, "state": "published",
            "expected_delivery_date": date.today()})
        cls.tx = cls.adult.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            cls.packer, 4000.0)["transaction"]
        cls.tx.action_receive()
        cls.lot = env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", cls.tx.stock_move_ids.ids), ("owner_id", "=", cls.packer.id)])
        Sub = env["shrimp.webhook.subscription"]
        cls.sub_packer = Sub.create({
            "name": "Emp", "partner_id": cls.packer.id, "user_id": cls.u_packer.id,
            "url": "https://hooks.example.com/emp", "event_types": "export.registered",
            "secret": "whsec_test_emp"})

    def test_publica_destino_no_es_planta_de_empaque(self):
        data = self._get_public()
        self.assertFalse(data["packing"], "La empacadora compradora no empacó nada")
        self.assertEqual(data["destination"]["label"], "Destino / planta compradora")
        self.assertEqual(data["destination"]["plant"], "Planta Durán")
        empresas = [c["company"] for c in data["chain"]]
        self.assertEqual(len(empresas), len(set(empresas)), "Sin eslabones repetidos")
        # Entrega = recepción real; fechas con zona local.
        self.assertTrue(data["dates"]["delivery_is_actual"])
        self.assertEqual(data["timezone"], "America/Guayaquil")
        self.assertFalse(int_ids(data))
        self.assertFalse(FORBIDDEN_KEYS & walk_keys(data))

    def _get_public(self):
        resp = self.url_open("/api/v1/public/traceability/%s" % self.tx.trace_token)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_exportacion_por_api_y_webhook(self):
        body = {"date": date.today().isoformat(), "destination_buyer": "Importador Miami",
                "destination_country": "US", "dae_number": "028-2026-40-0009",
                "container": "MSKU7712345", "boxes": 190, "unit_price": 4.1,
                "lines": [{"lot": self.lot.uuid_ref, "qty": 3800.0}]}
        resp = self.api("POST", "/exports", raw=self.raw_packer, body=body)
        self.assertEqual(resp.status_code, 201, resp.text)
        exp = resp.json()
        self.assertEqual(exp["state"], "registered")
        self.assertEqual(exp["destination_country"], "US")
        self.assertTrue(exp["confidential"])
        self.assertAlmostEqual(self.lot.available_qty, 200.0)
        # Listado y detalle propios; ajenos no.
        listado = self.api("GET", "/exports", raw=self.raw_packer).json()["data"]
        self.assertIn(exp["id"], [e["id"] for e in listado])
        self.assertProblem(self.api("GET", "/exports/%s" % exp["id"], raw=self.raw_a), 404)
        # Un lote ajeno no se exporta.
        self.assertProblem(self.api("POST", "/exports", raw=self.raw_a, body=body), 422)
        # Webhook encolado para quien despacha.
        self.assertTrue(self.env["shrimp.webhook.delivery"].search([
            ("subscription_id", "=", self.sub_packer.id), ("event_type", "=", "export.registered")]))
        # Trazabilidad pública: la salida es el último eslabón y, siendo
        # confidencial, sin el comprador de destino ni el precio.
        data = self._get_public()
        self.assertEqual(data["chain"][-1]["role_code"], "export")
        self.assertTrue(data["exports"][0]["country"])
        self.assertIsNone(data["exports"][0]["buyer"])
        self.assertNotIn("Importador Miami", str(data))
        self.assertNotIn("4.1", str(data["exports"]))
        # Trazabilidad privada (comprador): ve su salida completa.
        priv = self.api("GET", "/transactions/%s/traceability" % self.tx.uuid_ref,
                        raw=self.raw_packer).json()
        self.assertEqual(priv["exports"][0]["destination_buyer"], "Importador Miami")
        self.assertTrue(any(m["type"] == "export" for m in priv["moves"]))
        # Anular devuelve la cantidad.
        resp = self.api("POST", "/exports/%s:cancel" % exp["id"], raw=self.raw_packer,
                        body={"reason": "Error de captura"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertAlmostEqual(self.lot.available_qty, 4000.0)

    def test_scope_exports(self):
        Key = self.env["shrimp.api.key"]
        _k, raw = Key.api_create_key(self.u_packer, "solo-lectura", ["exports:read"])
        resp = self.api("POST", "/exports", raw=raw, body={
            "date": date.today().isoformat(), "lines": [{"lot": self.lot.uuid_ref, "qty": 1.0}]})
        self.assertProblem(resp, 403)
        self.assertEqual(self.api("GET", "/exports", raw=raw).status_code, 200)

    def test_certificados_sin_repetir(self):
        """El mismo certificado (tipo + número) en dos productos de la cadena
        —la larva y la cosecha que sale de ella— se publica una vez."""
        cert = self.env["shrimp.certificate"].search([], limit=1)
        if not cert:
            return
        compra = self.product.execute_purchase_flow(self.farm_a, 10)["transaction"]
        compra.write({"desired_date": date.today()})
        compra.action_receive()
        lote = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", compra.stock_move_ids.ids), ("owner_id", "=", self.farm_a.id)])
        pond = self.env["shrimp.partner.pond"].create({"partner_id": self.farm_a.id, "name": "PZ"})
        self.env["shrimp.lot.allocation"].create({
            "stock_lot_id": lote.id, "pond_id": pond.id, "allocated_qty": 10,
            "allocation_date": date.today() - timedelta(days=90)})
        cosecha = self.adult.copy({"name": "Cosecha PZ", "origin_pond_id": pond.id,
                                   "production_date": date.today(), "initial_qty": 500.0,
                                   "state": "published"})
        self.assertTrue(cosecha.origin_allocation_ids)
        att = self.env["ir.attachment"].create({"name": "c.pdf", "raw": b"%PDF-1.4"})
        Line = self.env["shrimp.product.certificate.line"].with_context(shrimp_keep_cert_status=True)
        for prod in (self.product, cosecha):
            Line.create({"product_id": prod.id, "certificate_id": cert.id, "number": "DUP-1",
                         "status": "approved", "attachment_id": att.id,
                         "issue_date": date.today() - timedelta(days=1),
                         "expiry_date": date.today() + timedelta(days=100)})
        venta = cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.packer, 100.0)["transaction"]
        data = venta._public_traceability_data()
        self.assertEqual(len([c for c in data["certificates"] if c["number"] == "DUP-1"]), 1)
        # La cadena llega hasta el laboratorio a través de la siembra.
        self.assertIn(self.lab.name, [c["company"] for c in data["chain"]])
        self.assertTrue(data["sowings"])
