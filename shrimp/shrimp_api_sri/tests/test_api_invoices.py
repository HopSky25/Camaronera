import base64

from odoo.tests import tagged

from odoo.addons.shrimp_api.tests.common import ApiCase, int_ids


@tagged("post_install", "-at_install", "shrimp_api_sri")
class TestApiInvoices(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Doc = cls.env["ec.sri.document"]
        cls.key_ok = "0" * 24 + "001001000000123" + "1" * 10
        cls.doc_a = Doc.create({"partner_id": cls.farm_a.id, "document_type": "01"})
        cls.doc_a._set({"state": "authorized", "access_key": cls.key_ok,
                        "authorization_number": cls.key_ok,
                        "authorized_xml": base64.b64encode(b"<autorizacion>ok</autorizacion>")})
        cls.doc_draft = Doc.create({"partner_id": cls.farm_a.id, "document_type": "01"})

    def test_list_only_mine_and_authorized(self):
        body = self.api("GET", "/invoices", raw=self.raw_a).json()
        keys = [d["access_key"] for d in body["data"]]
        self.assertEqual(keys, [self.key_ok])
        self.assertEqual(body["data"][0]["number"], "001-001-000000123")
        self.assertFalse(int_ids(body))
        self.assertEqual(self.api("GET", "/invoices", raw=self.raw_b).json()["data"], [])

    def test_detail_and_xml(self):
        resp = self.api("GET", "/invoices/%s" % self.key_ok, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        resp = self.api("GET", "/invoices/%s/xml" % self.key_ok, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/xml", resp.headers["Content-Type"])
        self.assertEqual(resp.content, b"<autorizacion>ok</autorizacion>")
        # Otro socio: 404, como si no existiera.
        self.assertProblem(self.api("GET", "/invoices/%s/xml" % self.key_ok, raw=self.raw_b), 404)
        self.assertProblem(self.api("GET", "/invoices/123", raw=self.raw_a), 404)

    def test_scope(self):
        _key, raw = self.env["shrimp.api.key"].api_create_key(self.u_a, "sin facturas", ["products:read"])
        self.assertProblem(self.api("GET", "/invoices", raw=raw), 403, "insufficient-scope")

    def test_event_invoice_authorized(self):
        sub_a = self.env["shrimp.webhook.subscription"].create({
            "name": "A", "partner_id": self.farm_a.id, "user_id": self.u_a.id,
            "url": "https://hooks.example.com/a", "event_types": "invoice.authorized", "secret": "s"})
        sub_b = self.env["shrimp.webhook.subscription"].create({
            "name": "B", "partner_id": self.farm_b.id, "user_id": self.u_b.id,
            "url": "https://hooks.example.com/b", "event_types": "*", "secret": "s"})
        key = "0" * 24 + "001001000000999" + "2" * 10
        self.doc_draft._set({"state": "authorized", "access_key": key})
        Delivery = self.env["shrimp.webhook.delivery"]
        mine = Delivery.search([("subscription_id", "=", sub_a.id), ("event_type", "=", "invoice.authorized")])
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine.resource_uuid, key)
        self.assertFalse(Delivery.search([("subscription_id", "=", sub_b.id),
                                          ("event_type", "=", "invoice.authorized")]))

    def test_openapi_lists_invoices(self):
        spec = self.url_open("/api/v1/openapi.json").json()
        self.assertIn("/api/v1/invoices/{access_key}/xml", spec["paths"])
