from odoo.tests import tagged

from .common import ApiCase, FORBIDDEN_KEYS, int_ids, walk_keys

PERSONAL_KEYS = {"email", "phone", "mobile", "street", "vat", "vat_or_id", "farm_telefono",
                 "carrier_phone", "technician", "price", "unit_price", "total", "amount"}


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiPublic(ApiCase):

    def _get(self, path):
        resp = self.url_open("/api/v1" + path)
        self.assertEqual(resp.status_code, 200, "%s -> %s" % (path, resp.text))
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
        return resp.json()

    def test_catalogs_no_leaks(self):
        for path in ("/public/catalogs/species", "/public/catalogs/stages",
                     "/public/catalogs/genetics-lines", "/public/catalogs/size-grades",
                     "/public/catalogs/uoms", "/public/catalogs/taste-criteria",
                     "/public/catalogs/certificate-types", "/public/catalogs/aguajes?year=2026"):
            body = self._get(path)
            self.assertIn("data", body)
            self.assertFalse(int_ids(body), path)
            self.assertFalse(FORBIDDEN_KEYS & walk_keys(body), path)
        uoms = self._get("/public/catalogs/uoms")["data"]
        self.assertTrue(uoms)
        self.assertNotIn("commission_cents", walk_keys(uoms))

    def test_public_products(self):
        # Filtrado por vendedor: con la demo masiva cargada hay cientos de
        # lotes publicados y el de la prueba no tiene por qué caer en la
        # primera página.
        body = self._get("/public/products?limit=200&seller=%s" % self.lab.uuid_ref)
        ids = [p["id"] for p in body["data"]]
        self.assertIn(self.product.uuid_ref, ids)
        detail = self._get("/public/products/%s" % self.product.uuid_ref)
        self.assertEqual(detail["seller"]["id"], self.lab.uuid_ref)
        keys = walk_keys(detail)
        self.assertFalse(FORBIDDEN_KEYS & keys)
        for k in ("email", "phone", "vat_or_id", "state", "initial_qty", "batch_code",
                  "health_status", "origin_pond"):
            self.assertNotIn(k, keys)
        self.assertFalse(int_ids(detail))
        # Un borrador no es público.
        draft = self.env["shrimp.product"].create({
            "name": "Borrador", "seller_partner_id": self.lab.id, "seller_role": "laboratorio",
            "initial_qty": 10, "price": 1, "uom_id": self.uom_millar.id})
        self.assertProblem(self.url_open("/api/v1/public/products/%s" % draft.uuid_ref), 404)
        # Filtro por catálogo con uuid inválido -> 400, no 500.
        self.assertProblem(self.url_open("/api/v1/public/products?species=nope"), 400)

    def test_directories_no_personal_data(self):
        for kind in ("packers", "copackers", "verifiers", "sellers"):
            body = self._get("/public/directory/%s?limit=200" % kind)
            keys = walk_keys(body)
            self.assertFalse(PERSONAL_KEYS & keys, kind)
            self.assertFalse(FORBIDDEN_KEYS & keys, kind)
            self.assertFalse(int_ids(body), kind)
        packers = self._get("/public/directory/packers?limit=200")["data"]
        self.assertIn(self.packer.uuid_ref, [p["id"] for p in packers])
        sellers = self._get("/public/directory/sellers?limit=200")["data"]
        self.assertIn(self.lab.uuid_ref, [p["id"] for p in sellers])
        # Una camaronera sin lotes publicados no aparece en el directorio de vendedores.
        self.assertNotIn(self.farm_b.uuid_ref, [p["id"] for p in sellers])
        profile = self._get("/public/partners/%s" % self.packer.uuid_ref)
        self.assertEqual(profile["packer"]["plant"], "Planta Durán")
        self.assertProblem(self.url_open("/api/v1/public/partners/%s" % self.farm_b.uuid_ref), 404)

    def test_fee_quote(self):
        body = self._get("/public/verification-fee/quote?lb=10000")
        self.assertIn("total", body)
        self.assertIn("currency", body["total"])
        self.assertProblem(self.url_open("/api/v1/public/verification-fee/quote?lb=abc"), 400)

    def test_public_traceability(self):
        tx = self.product.execute_purchase_flow(self.farm_a, 7)["transaction"]
        token = tx.trace_token
        self.assertTrue(token and len(token) >= 16)
        self.assertIn("/t/%s" % token, tx.traceability_url())
        self.assertNotIn("/%s/" % tx.id, tx.traceability_url())
        # Página HTML pública (destino del QR), sin sesión.
        page = self.url_open("/t/%s" % token)
        self.assertEqual(page.status_code, 200)
        self.assertIn("PL12 API", page.text)
        self.assertNotIn(self.lab.email, page.text)
        # JSON.
        data = self._get("/public/traceability/%s" % token)
        keys = walk_keys(data)
        self.assertFalse(PERSONAL_KEYS & keys, PERSONAL_KEYS & keys)
        self.assertFalse(FORBIDDEN_KEYS & keys)
        self.assertFalse(int_ids(data))
        companies = [c["company"] for c in data["chain"]]
        self.assertIn(self.lab.name, companies)
        self.assertIn(self.farm_a.name, companies)
        # Token desconocido o rotado: 404.
        self.assertProblem(self.url_open("/api/v1/public/traceability/" + "x" * 24), 404)
        tx.action_rotate_trace_token()
        self.assertEqual(self.url_open("/t/%s" % token).status_code, 404)
        self.assertProblem(self.url_open("/api/v1/public/traceability/%s" % token), 404)

    def test_openapi_and_docs(self):
        spec = self._get("/openapi.json")
        self.assertEqual(spec["openapi"], "3.1.0")
        for path in ("/api/v1/products", "/api/v1/public/products", "/api/v1/webhooks",
                     "/api/v1/products/{id}:publish", "/api/v1/copack/orders/{id}:sign"):
            self.assertIn(path, spec["paths"])
        op = spec["paths"]["/api/v1/products"]["post"]
        self.assertIn("products:write", op["x-required-scopes"])
        self.assertIn("requestBody", op)
        self.assertEqual(spec["paths"]["/api/v1/public/products"]["get"]["security"], [])
        docs = self.url_open("/api/v1/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("redoc", docs.text)

    def test_seller_certificate_requires_login(self):
        """El archivo del certificado personal del vendedor ya no se sirve sin sesión."""
        resp = self.url_open("/marketplace/product/%s/user-certificate/1" % self.product.uuid_ref,
                             allow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/web/login", resp.headers.get("Location", ""))

    def test_calendar_events_use_uuid(self):
        from odoo import fields
        self.product.expected_delivery_date = fields.Date.today()
        resp = self.url_open("/marketplace/calendar/events", json={
            "jsonrpc": "2.0", "method": "call", "params": {}, "id": 1})
        self.assertEqual(resp.status_code, 200)
        events = resp.json().get("result") or []
        self.assertTrue(events)
        for ev in events:
            self.assertIsInstance(ev["id"], str)
