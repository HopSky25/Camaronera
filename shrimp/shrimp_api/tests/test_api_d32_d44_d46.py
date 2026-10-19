"""Auditoría de procesos: D-32 (API anterior retirada), D-44 (webhooks:
intentos, aviso al desactivarse, reenvío) y D-46 (caducidad de las claves)."""

import importlib
import importlib.util
import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_api.models import webhook as webhook_module

from .common import ApiCase

T0 = datetime(2026, 10, 16, 12, 0, 0)


class FakeResponse:
    def __init__(self, status):
        self.status_code = status


def _csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


@tagged("post_install", "-at_install", "shrimp_api")
class TestD46KeyExpiry(ApiCase):

    def _mails_to(self, email):
        return self.env["mail.mail"].sudo().search([("email_to", "ilike", email)])

    def test_default_lifetime_365_and_configurable(self):
        Key = self.env["shrimp.api.key"]
        with freeze_time(T0):
            key, _raw = Key.api_create_key(self.u_a, "por defecto", ["products:read"])
            self.assertEqual(key.expires_at, T0 + timedelta(days=365))
            with self.assertRaises(ValidationError):
                Key.api_create_key(self.u_a, "demasiado", ["products:read"],
                                   expires_at=T0 + timedelta(days=400))
            self.env["ir.config_parameter"].sudo().set_param("shrimp_api.key_lifetime_days", "30")
            key2, _raw = Key.api_create_key(self.u_a, "corta", ["products:read"])
            self.assertEqual(key2.expires_at, T0 + timedelta(days=30))
            with self.assertRaises(ValidationError):
                Key.api_create_key(self.u_a, "larga", ["products:read"],
                                   expires_at=T0 + timedelta(days=90))

    def test_expired_key_401_key_expired(self):
        self.key_a.sudo().expires_at = fields.Datetime.now() - timedelta(minutes=1)
        body = self.assertProblem(self.api("GET", "/me", raw=self.raw_a), 401, "expired-api-key")
        self.assertEqual(body["code"], "key-expired")
        self.assertEqual(body["renew_url"], "/my/api-keys")
        # Una clave inválida no lleva ese código.
        body = self.assertProblem(self.api("GET", "/me", raw="trz_deadbeef_" + "x" * 43), 401)
        self.assertNotIn("code", body)

    def test_notice_7_days_before_once(self):
        Key = self.env["shrimp.api.key"].sudo()
        self.env["ir.mail_server"].sudo().create({"name": "smtp test", "smtp_host": "localhost"})
        with freeze_time(T0):
            self.key_a.sudo().expires_at = T0 + timedelta(days=5)
            self.key_b.sudo().expires_at = T0 + timedelta(days=20)
            Key._cron_expiry_notice()
            self.assertTrue(self.key_a.expiry_notice_sent_at)
            self.assertFalse(self.key_b.expiry_notice_sent_at)
            mails = self._mails_to(self.farm_a.email)
            self.assertEqual(len(mails), 1)
            self.assertIn(self.key_a.name, mails.subject)
            # No se repite.
            Key._cron_expiry_notice()
            self.assertEqual(len(self._mails_to(self.farm_a.email)), 1)
            # Aviso en el portal.
            notices = Key._shrimp_portal_api_notices(self.u_a)
            self.assertTrue(any(n["url"] == "/my/api-keys" and n["level"] == "warning" for n in notices))
            self.assertFalse(Key._shrimp_portal_api_notices(self.u_b))
            # Renovar: misma clave, otro año, y el aviso se vuelve a armar.
            self.key_a.action_renew()
            self.assertEqual(self.key_a.expires_at, T0 + timedelta(days=365))
            self.assertEqual(self.key_a.renewed_at, T0)
            self.assertFalse(self.key_a.expiry_notice_sent_at)
        with freeze_time(T0 + timedelta(days=21)):
            self.env.invalidate_all()
            notices = Key._shrimp_portal_api_notices(self.u_b)
            self.assertTrue(any(n["level"] == "danger" for n in notices))

    def test_renew_rules(self):
        key = self.key_b.sudo()
        key.expires_at = fields.Datetime.now() - timedelta(days=3)
        self.assertEqual(key.status, "expired")
        key.action_renew()
        self.assertEqual(key.status, "active")
        self.assertEqual(self.api("GET", "/me", raw=self.raw_b).status_code, 200)
        key.action_revoke()
        with self.assertRaises(ValidationError):
            key.action_renew()

    def test_portal_renew(self):
        self.key_a.sudo().expires_at = fields.Datetime.now() + timedelta(days=2)
        self.authenticate("a.api", "Clave-api-123")
        home = self.url_open("/my")
        self.assertIn("shrimp_api_home_notices", home.text)
        page = self.url_open("/my/api-keys")
        self.assertIn("Caduca pronto", page.text)
        self.assertIn("/my/api-keys/%s/renew" % self.key_a.uuid_ref, page.text)
        resp = self.url_open("/my/api-keys/%s/renew" % self.key_a.uuid_ref,
                             data={"csrf_token": _csrf(page.text)})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("renovada", resp.text)
        self.assertGreater(self.key_a.expires_at, fields.Datetime.now() + timedelta(days=360))
        # Clave ajena: 404.
        resp = self.url_open("/my/api-keys/%s/renew" % self.key_b.uuid_ref,
                             data={"csrf_token": _csrf(page.text)})
        self.assertEqual(resp.status_code, 404)

    def test_migration_gives_one_year_to_keys_without_expiry(self):
        self.env.cr.execute("UPDATE shrimp_api_key SET expires_at = NULL WHERE id IN %s",
                            (tuple((self.key_a | self.key_b).ids),))
        self.env.invalidate_all()
        path = __file__.rsplit("/tests/", 1)[0] + "/migrations/19.0.1.10.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("shrimp_api_mig_1_10", path)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        before = self.key_packer.sudo().expires_at
        mig.migrate(self.env.cr, "19.0.1.9.0")
        self.env.invalidate_all()
        self.env.cr.execute("SELECT (now() at time zone 'utc')")
        db_now = self.env.cr.fetchone()[0]
        for key in (self.key_a, self.key_b):
            delta = key.sudo().expires_at - db_now
            self.assertTrue(timedelta(days=364) < delta <= timedelta(days=365), delta)
        self.assertEqual(self.key_packer.sudo().expires_at, before, "Las que ya tenían fecha no cambian")


@tagged("post_install", "-at_install", "shrimp_api")
class TestD44Webhooks(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Sub = cls.env["shrimp.webhook.subscription"]
        cls.sub = Sub.create({"name": "ERP A", "partner_id": cls.farm_a.id, "user_id": cls.u_a.id,
                              "url": "https://hooks.example.com/a", "event_types": "*",
                              "secret": "whsec_a"})
        cls.sub_b = Sub.create({"name": "ERP B", "partner_id": cls.farm_b.id, "user_id": cls.u_b.id,
                                "url": "https://hooks.example.com/b", "event_types": "*",
                                "secret": "whsec_b"})

    def _patches(self, status=200):
        Delivery = type(self.env["shrimp.webhook.delivery"])
        return (patch.object(webhook_module.requests, "post", return_value=FakeResponse(status)),
                patch.object(Delivery, "_host_is_public", return_value=True))

    def _due(self, delivery):
        delivery.sudo().next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)

    @mute_logger("odoo.addons.shrimp_api.models.webhook")
    def test_poisoned_event_counts_attempts_and_stops(self):
        delivery = self.sub.action_ping()
        Delivery = self.env["shrimp.webhook.delivery"]
        with freeze_time(T0), patch.object(type(Delivery), "_sign", side_effect=RuntimeError("boom")):
            for i in range(webhook_module.MAX_ATTEMPTS):
                self._due(delivery)
                Delivery._cron_send()
                delivery.invalidate_recordset()
                self.assertEqual(delivery.attempts, i + 1)
                self.assertIn("RuntimeError", delivery.last_error)
            self.assertEqual(delivery.state, "dead")
            self.assertFalse(delivery.next_attempt_at)
            # Ya no la toma el cron.
            self.assertEqual(Delivery._cron_send(), 0)

    def test_auto_disable_notifies_owner(self):
        self.env["ir.mail_server"].sudo().create({"name": "smtp test", "smtp_host": "localhost"})
        sub = self.sub_b.sudo()
        sub.failure_count = webhook_module.AUTO_DISABLE_AFTER - 1
        delivery = sub.action_ping()
        delivery.sudo().attempts = webhook_module.MAX_ATTEMPTS - 1
        p1, p2 = self._patches(500)
        with freeze_time(T0), p1, p2:
            delivery._send_one()
        self.assertEqual(delivery.state, "dead")
        self.assertFalse(sub.active)
        self.assertEqual(sub.auto_disabled_at, T0)
        mails = self.env["mail.mail"].sudo().search([("email_to", "ilike", self.farm_b.email)])
        self.assertTrue(mails)
        self.assertIn(sub.name, mails[0].subject)
        notices = self.env["shrimp.api.key"]._shrimp_portal_api_notices(self.u_b)
        self.assertTrue(any(n["url"] == "/my/webhooks" for n in notices))
        # Una pausa manual no es «desactivación automática».
        self.sub.active = False
        self.assertFalse(self.sub.auto_disabled_at)

    def test_events_held_while_inactive_and_resent_on_reactivation(self):
        Delivery = self.env["shrimp.webhook.delivery"].sudo()
        sub = self.sub.sudo()
        with freeze_time(T0 - timedelta(days=10)):
            sub.active = False
            very_old = sub.action_ping()
            self.assertEqual(very_old.state, "held")
        with freeze_time(T0 - timedelta(days=2)):
            recent = sub.action_ping()
        dead_recent = Delivery.create({
            "subscription_id": sub.id, "event_type": "ping", "occurred_at": T0 - timedelta(days=3),
            "payload": "{}", "state": "dead", "attempts": 8})
        dead_old = Delivery.create({
            "subscription_id": sub.id, "event_type": "ping", "occurred_at": T0 - timedelta(days=9),
            "payload": "{}", "state": "dead", "attempts": 8})
        # Un evento de negocio también queda retenido.
        tx = self.product.execute_purchase_flow(self.farm_a, 3)["transaction"]
        held_tx = Delivery.search([("subscription_id", "=", sub.id),
                                   ("event_type", "=", "transaction.state_changed")])
        self.assertTrue(held_tx)
        self.assertEqual(set(held_tx.mapped("state")), {"held"})
        self.assertEqual(json.loads(held_tx[0].payload)["resource"]["uuid"], tx.uuid_ref)
        with freeze_time(T0):
            resent = sub.action_reactivate()
        self.assertEqual(resent, 2 + len(held_tx))
        self.assertTrue(sub.active)
        for d in recent | dead_recent | held_tx:
            self.assertEqual(d.state, "pending")
            self.assertEqual(d.attempts, 0)
            self.assertEqual(d.resend_count, 1)
        self.assertEqual(very_old.state, "dead")
        self.assertEqual(dead_old.state, "dead")
        self.assertEqual(dead_old.resend_count, 0)
        # Se entregan.
        p1, p2 = self._patches(200)
        with p1, p2:
            Delivery._cron_send()
        self.assertEqual(recent.state, "success")

    def test_resend_window_configurable(self):
        self.env["ir.config_parameter"].sudo().set_param("shrimp_api.webhook_resend_days", "1")
        sub = self.sub.sudo()
        with freeze_time(T0 - timedelta(days=2)):
            sub.active = False
            d = sub.action_ping()
        with freeze_time(T0):
            self.assertEqual(sub.action_reactivate(), 0)
        self.assertEqual(d.state, "dead")

    def test_api_resend_delivery(self):
        sub = self.sub.sudo()
        delivery = sub.action_ping()
        delivery.write({"state": "dead", "attempts": 8, "next_attempt_at": False})
        url = "/webhooks/%s/deliveries/%s:resend" % (sub.uuid_ref, delivery.uuid_ref)
        resp = self.api("POST", url, raw=self.raw_a)
        self.assertEqual(resp.status_code, 202, resp.text)
        data = resp.json()
        self.assertEqual((data["state"], data["attempts"], data["resend_count"]), ("pending", 0, 1))
        # Ya está en cola.
        self.assertProblem(self.api("POST", url, raw=self.raw_a), 409, "invalid-state")
        # Alias con barra.
        delivery.sudo().write({"state": "success"})
        resp = self.api("POST", url.replace(":resend", "/resend"), raw=self.raw_a)
        self.assertEqual(resp.status_code, 202, resp.text)
        # Ajena: 404.
        self.assertProblem(self.api("POST", url, raw=self.raw_b), 404)
        # Suscripción inactiva: 409.
        delivery.sudo().write({"state": "dead"})
        sub.active = False
        self.assertProblem(self.api("POST", url, raw=self.raw_a), 409, "subscription-inactive")
        # PATCH active=true reactiva y re-encola.
        resp = self.api("PATCH", "/webhooks/%s" % sub.uuid_ref, raw=self.raw_a, body={"active": True})
        self.assertEqual(resp.status_code, 200, resp.text)
        delivery.invalidate_recordset()
        self.assertEqual(delivery.state, "pending")
        # La lista admite el estado «held» y el detalle trae los campos nuevos.
        resp = self.api("GET", "/webhooks/%s/deliveries?state=held" % sub.uuid_ref, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        resp = self.api("GET", "/webhooks/%s" % sub.uuid_ref, raw=self.raw_a)
        self.assertIn("held_count", resp.json())
        self.assertIn("auto_disabled_at", resp.json())

    def test_portal_deliveries_and_resend(self):
        sub = self.sub.sudo()
        delivery = sub.action_ping()
        delivery.write({"state": "dead", "attempts": 8, "next_attempt_at": False})
        self.authenticate("a.api", "Clave-api-123")
        page = self.url_open("/my/webhooks/%s" % sub.uuid_ref)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Reenviar", page.text)
        resp = self.url_open("/my/webhooks/%s/deliveries/%s/resend" % (sub.uuid_ref, delivery.uuid_ref),
                             data={"csrf_token": _csrf(page.text)})
        self.assertIn(resp.status_code, (200, 303, 302))
        delivery.invalidate_recordset()
        self.assertEqual(delivery.state, "pending")
        # Suscripción ajena: 404.
        self.assertEqual(self.url_open("/my/webhooks/%s" % self.sub_b.uuid_ref).status_code, 404)
        # Pausar y reactivar desde el portal informa de lo re-encolado.
        listing = self.url_open("/my/webhooks")
        self.url_open("/my/webhooks/%s/toggle" % sub.uuid_ref, data={"csrf_token": _csrf(listing.text)})
        self.assertFalse(sub.active)
        sub.action_ping()
        resp = self.url_open("/my/webhooks/%s/toggle" % sub.uuid_ref,
                             data={"csrf_token": _csrf(listing.text)})
        self.assertTrue(sub.active)
        self.assertIn("reactivada", resp.text)


@tagged("post_install", "-at_install", "shrimp_api")
class TestD32LegacyApiRetired(ApiCase):

    def test_legacy_controller_is_gone(self):
        from odoo.addons.shrimp_marketplace import controllers as mp_controllers
        self.assertFalse(hasattr(mp_controllers, "api"))
        with self.assertRaises(ImportError):
            importlib.import_module("odoo.addons.shrimp_marketplace.controllers.api")

    def test_same_urls_served_by_new_api_with_real_seller_role(self):
        # Mismo contrato de la API anterior: X-API-Key, *_id con el uuid y PUT.
        resp = self.api("POST", "/products", headers={"X-API-Key": self.raw_a, "Idempotency-Key": "d32-1"},
                        body={"name": "Camarón legado", "price": 2.1, "initial_qty": 100,
                              "uom_id": self.env.ref("shrimp_marketplace.uom_libra").uuid_ref},
)
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        product = self.env["shrimp.product"].sudo().search([("uuid_ref", "=", data["id"])])
        self.assertEqual(product.seller_partner_id, self.farm_a)
        self.assertEqual(product.seller_role, "camaronera",
                         "La API vieja ponía «laboratorio» a las camaroneras")
        resp = self.url_open("/api/v1/products/%s" % data["id"], method="PUT",
                             data=json.dumps({"price": 2.3}),
                             headers={"X-API-Key": self.raw_a, "Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 200, resp.text)
        for path in ("/products", "/facilities", "/ponds"):
            self.assertEqual(self.url_open("/api/v1" + path, headers={"X-API-Key": self.raw_a}).status_code,
                             200, path)
