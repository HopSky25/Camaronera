import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.shrimp_api.models import webhook as webhook_module

from .common import ApiCase


class FakeResponse:
    def __init__(self, status):
        self.status_code = status


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiWebhooks(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Sub = cls.env["shrimp.webhook.subscription"]
        cls.sub_a = Sub.create({"name": "A", "partner_id": cls.farm_a.id, "user_id": cls.u_a.id,
                                "url": "https://hooks.example.com/a", "event_types": "*",
                                "secret": "whsec_test_a"})
        cls.sub_b = Sub.create({"name": "B", "partner_id": cls.farm_b.id, "user_id": cls.u_b.id,
                                "url": "https://hooks.example.com/b", "event_types": "*",
                                "secret": "whsec_test_b"})
        cls.sub_lab = Sub.create({"name": "Lab", "partner_id": cls.lab.id, "user_id": cls.u_lab.id,
                                  "url": "https://hooks.example.com/lab",
                                  "event_types": "transaction.state_changed",
                                  "secret": "whsec_test_lab"})

    def _deliveries(self, sub, event=None):
        domain = [("subscription_id", "=", sub.id)]
        if event:
            domain.append(("event_type", "=", event))
        return self.env["shrimp.webhook.delivery"].search(domain)

    def test_transaction_events_only_to_parties(self):
        tx = self.product.execute_purchase_flow(self.farm_a, 4)["transaction"]
        mine = self._deliveries(self.sub_a, "transaction.state_changed")
        self.assertTrue(mine)
        payload = json.loads(mine[0].payload)
        self.assertEqual(payload["resource"], {"type": "transaction", "uuid": tx.uuid_ref})
        self.assertEqual(payload["type"], "transaction.state_changed")
        self.assertEqual(set(payload), {"id", "type", "api_version", "occurred_at", "resource"})
        # El vendedor (suscrito solo a ese evento) también; la ajena, no.
        self.assertTrue(self._deliveries(self.sub_lab, "transaction.state_changed"))
        self.assertFalse(self._deliveries(self.sub_b, "transaction.state_changed"))

    def test_lot_published_goes_to_eligible_buyers(self):
        draft = self.env["shrimp.product"].create({
            "name": "PL nuevo", "seller_partner_id": self.lab.id, "seller_role": "laboratorio",
            "initial_qty": 50, "price": 1, "uom_id": self.uom_millar.id})
        self.assertFalse(self._deliveries(self.sub_a, "lot.published"))
        draft.write({"state": "published"})
        self.assertTrue(self._deliveries(self.sub_a, "lot.published"))
        self.assertTrue(self._deliveries(self.sub_b, "lot.published"))
        # El laboratorio está suscrito solo a transacciones.
        self.assertFalse(self._deliveries(self.sub_lab, "lot.published"))

    def test_no_event_without_real_change(self):
        tx = self.product.execute_purchase_flow(self.farm_a, 2)["transaction"]
        before = len(self._deliveries(self.sub_a))
        tx.write({"state": tx.state})
        self.assertEqual(len(self._deliveries(self.sub_a)), before)

    def test_inactive_user_gets_nothing(self):
        self.u_b.sudo().active = False
        draft = self.env["shrimp.product"].create({
            "name": "PL", "seller_partner_id": self.lab.id, "seller_role": "laboratorio",
            "initial_qty": 50, "price": 1, "uom_id": self.uom_millar.id})
        draft.write({"state": "published"})
        self.assertFalse(self._deliveries(self.sub_b, "lot.published"))

    def test_signature_and_send(self):
        self.sub_a.action_ping()
        delivery = self._deliveries(self.sub_a, "ping")
        self.assertEqual(delivery.state, "pending")
        calls = []

        def fake_post(url, data=None, headers=None, timeout=None, allow_redirects=None):
            calls.append((url, data, headers))
            return FakeResponse(204)

        Delivery = self.env["shrimp.webhook.delivery"]
        with patch.object(webhook_module.requests, "post", side_effect=fake_post), \
                patch.object(type(Delivery), "_host_is_public", return_value=True):
            Delivery._cron_send()
        self.assertEqual(len([c for c in calls if c[0] == self.sub_a.url]), 1)
        url, body, headers = [c for c in calls if c[0] == self.sub_a.url][0]
        sig = dict(part.split("=", 1) for part in headers["X-Trazul-Signature"].split(","))
        expected = hmac.new(b"whsec_test_a", ("%s." % sig["t"]).encode() + body, hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(sig["v1"], expected))
        self.assertEqual(headers["X-Trazul-Event"], "ping")
        self.assertEqual(headers["X-Trazul-Delivery"], delivery.uuid_ref)
        delivery.invalidate_recordset()
        self.assertEqual(delivery.state, "success")
        self.assertEqual(delivery.attempts, 1)

    def test_retry_backoff_and_dead_letter(self):
        self.sub_b.action_ping()
        delivery = self._deliveries(self.sub_b, "ping")
        Delivery = self.env["shrimp.webhook.delivery"]
        with patch.object(webhook_module.requests, "post", return_value=FakeResponse(500)), \
                patch.object(type(Delivery), "_host_is_public", return_value=True):
            delivery._send_one()
            self.assertEqual(delivery.state, "retrying")
            self.assertEqual(delivery.attempts, 1)
            self.assertTrue(delivery.next_attempt_at)
            for _i in range(webhook_module.MAX_ATTEMPTS - 1):
                delivery._send_one()
        self.assertEqual(delivery.state, "dead")
        self.assertEqual(delivery.attempts, webhook_module.MAX_ATTEMPTS)
        self.assertFalse(delivery.next_attempt_at)
        self.assertEqual(self.sub_b.failure_count, 1)

    def test_ssrf_guard(self):
        sub = self.env["shrimp.webhook.subscription"].create({
            "name": "local", "partner_id": self.farm_a.id, "user_id": self.u_a.id,
            "url": "https://127.0.0.1/hook", "event_types": "*", "secret": "s"})
        sub.action_ping()
        delivery = self._deliveries(sub, "ping")
        with patch.object(webhook_module.requests, "post") as post:
            delivery._send_one()
            post.assert_not_called()
        self.assertIn("no permitido", delivery.last_error)

    def test_https_required(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env["shrimp.webhook.subscription"].create({
                "name": "http", "partner_id": self.farm_a.id, "user_id": self.u_a.id,
                "url": "http://hooks.example.com/x", "event_types": "*"})

    def test_api_manage_subscriptions(self):
        resp = self.api("POST", "/webhooks", raw=self.raw_b, body={
            "name": "ERP", "url": "https://erp.example.com/hook",
            "events": ["transaction.state_changed", "lot.published"]})
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertTrue(data["secret"].startswith("whsec_"))
        wid = data["id"]
        detail = self.api("GET", "/webhooks/%s" % wid, raw=self.raw_b).json()
        self.assertNotIn("secret", detail)
        self.assertEqual(sorted(detail["events"]), ["lot.published", "transaction.state_changed"])
        # Otro socio no la ve.
        self.assertProblem(self.api("GET", "/webhooks/%s" % wid, raw=self.raw_a), 404)
        resp = self.api("POST", "/webhooks/%s:ping" % wid, raw=self.raw_b)
        self.assertEqual(resp.status_code, 202, resp.text)
        deliveries = self.api("GET", "/webhooks/%s/deliveries" % wid, raw=self.raw_b).json()["data"]
        self.assertEqual(deliveries[0]["event_type"], "ping")
        self.assertProblem(self.api("POST", "/webhooks", raw=self.raw_b, body={
            "name": "x", "url": "https://erp.example.com/x", "events": ["no.existe"]}), 422)
        self.assertProblem(self.api("POST", "/webhooks", raw=self.raw_b, body={
            "name": "x", "url": "http://erp.example.com/x", "events": ["*"]}), 422)
        self.assertEqual(self.api("DELETE", "/webhooks/%s" % wid, raw=self.raw_b).status_code, 204)
        events = self.api("GET", "/webhooks/events", raw=self.raw_b).json()["data"]
        self.assertIn("dispatch.eta_changed", [e["type"] for e in events])

    def test_business_survives_broken_webhook_engine(self):
        """Si el encolado falla, la venta sigue: el webhook nunca tumba el negocio."""
        Delivery = type(self.env["shrimp.webhook.delivery"])
        with patch.object(Delivery, "_enqueue", side_effect=RuntimeError("boom")):
            tx = self.product.execute_purchase_flow(self.farm_a, 1)["transaction"]
        self.assertEqual(tx.state, "confirmed")
