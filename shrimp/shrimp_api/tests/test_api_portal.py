import re

from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiPortal(ApiCase):

    def _csrf(self, html):
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertTrue(m, "No hay csrf_token en la página")
        return m.group(1)

    def test_portal_keys_lifecycle(self):
        self.authenticate("a.api", "Clave-api-123")
        home = self.url_open("/my")
        self.assertEqual(home.status_code, 200)
        self.assertIn("/my/api-keys", home.text)
        page = self.url_open("/my/api-keys")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers.get("Cache-Control"), "no-store")
        resp = self.url_open("/my/api-keys/new", data={
            "csrf_token": self._csrf(page.text), "name": "ERP desde portal",
            "scopes": ["facilities:read", "products:read"], "expiry_days": "30"})
        self.assertEqual(resp.status_code, 200)
        m = re.search(r"(trz_[0-9a-f]{8}_[A-Za-z0-9_\-]{20,})", resp.text)
        self.assertTrue(m, "La clave nueva tiene que mostrarse una vez")
        raw = m.group(1)
        key = self.env["shrimp.api.key"].search([("name", "=", "ERP desde portal")])
        self.assertEqual(key.user_id, self.u_a)
        self.assertEqual(sorted(key.scope_ids.mapped("code")), ["facilities:read", "products:read"])
        self.assertEqual(self.api("GET", "/facilities", raw=raw).status_code, 200)
        # Al volver a la lista ya no se ve el secreto.
        again = self.url_open("/my/api-keys")
        self.assertNotIn(raw, again.text)
        resp = self.url_open("/my/api-keys/%s/revoke" % key.uuid_ref,
                             data={"csrf_token": self._csrf(again.text)})
        self.assertIn(resp.status_code, (200, 303, 302))
        self.assertProblem(self.api("GET", "/facilities", raw=raw), 401, "revoked-api-key")

    def test_portal_cannot_revoke_foreign_key(self):
        self.authenticate("b.api", "Clave-api-123")
        page = self.url_open("/my/api-keys")
        resp = self.url_open("/my/api-keys/%s/revoke" % self.key_a.uuid_ref,
                             data={"csrf_token": self._csrf(page.text)})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(self.key_a.revoked_at)

    def test_portal_webhooks(self):
        self.authenticate("a.api", "Clave-api-123")
        page = self.url_open("/my/webhooks")
        self.assertEqual(page.status_code, 200)
        resp = self.url_open("/my/webhooks/new", data={
            "csrf_token": self._csrf(page.text), "name": "Hook ERP",
            "url": "https://erp.example.com/hook", "events": ["transaction.state_changed"]})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("whsec_", resp.text)
        sub = self.env["shrimp.webhook.subscription"].search([("name", "=", "Hook ERP")])
        self.assertEqual(sub.partner_id, self.farm_a)
        self.assertEqual(sub.event_types, "transaction.state_changed")
