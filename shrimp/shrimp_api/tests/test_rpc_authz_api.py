"""Métodos públicos de shrimp_api llamados por RPC (/web/dataset/call_kw).

En Odoo 19 un usuario de portal puede llamar por RPC a cualquier método
público de un modelo sobre los ids que quiera, sin ACL ni reglas. Aquí se
llama a cada método como lo haría ese RPC: ``with_user(portal)`` SIN sudo,
sobre registros ajenos (otra cuenta) y desde un subusuario sin «Gestionar
cuenta». Cada caso tiene su contraparte: el dueño (o el controlador, en
sudo) sigue pudiendo.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from odoo import fields
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase


def _public_key_b64():
    der = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


@tagged("post_install", "-at_install", "shrimp_api")
class TestRpcAuthzApi(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        # Subusuario de la camaronera A sin «Gestionar cuenta» (solo ver).
        contacto = env["res.partner"].sudo().create({
            "name": "Sub A rpc", "email": "sub.a.rpc@prueba.test", "parent_id": cls.farm_a.id,
            "type": "contact"})
        cls.u_sub_a = env["res.users"].sudo().create({
            "name": contacto.name, "login": "sub.a.rpc", "partner_id": contacto.id,
            "password": "Clave-api-123",
            "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
        env["shrimp.account.member"].sudo()._shrimp_ensure(
            cls.u_sub_a, cls.farm_a, permissions={"camaronera": {"view"}})

        Sub = env["shrimp.webhook.subscription"]
        cls.sub_a = Sub.create({"name": "A rpc", "partner_id": cls.farm_a.id, "user_id": cls.u_a.id,
                                "url": "https://hooks.example.com/a-rpc", "event_types": "*",
                                "secret": "whsec_rpc_a"})
        cls.delivery_a = env["shrimp.webhook.delivery"].create({
            "subscription_id": cls.sub_a.id, "event_type": "ping",
            "occurred_at": fields.Datetime.now(), "payload": "{}", "state": "dead"})

    # ------------------------------------------------------------------
    # Emisión de claves: no se llaman por RPC
    # ------------------------------------------------------------------
    def test_emision_de_claves_no_es_rpc(self):
        Key = self.env["shrimp.api.key"].with_user(self.u_b)
        for name in ("api_create_key", "api_create_supervisor_key", "api_create_act_as_key"):
            with self.assertRaises(AccessError, msg=name):
                get_public_method(Key, name)
        # Los botones del back-office siguen siendo públicos (con su guarda).
        # (con el entorno del servidor: al portal la puerta general de ir.http
        # le niega cualquier método de shrimp.*, ver shrimp_rpc_gate.py).
        get_public_method(self.env["shrimp.api.key"], "action_revoke")
        # Desde el servidor (los controladores) se siguen emitiendo.
        key, raw = self.env["shrimp.api.key"].sudo().api_create_key(self.u_b, "otra", SCOPE_CODES)
        self.assertTrue(raw.startswith("trz_%s_" % key.key_prefix))

    # ------------------------------------------------------------------
    # Claves y dispositivos
    # ------------------------------------------------------------------
    def test_revocar_clave_ajena(self):
        with self.assertRaises(AccessError):
            self.key_a.with_user(self.u_b).action_revoke()
        with self.assertRaises(AccessError):
            self.key_a.with_user(self.u_sub_a).action_revoke()
        self.assertFalse(self.key_a.revoked_at)
        self.assertEqual(self.key_a.status, "active")
        # El dueño revoca la suya (lo mismo que /my/api-keys).
        self.key_b.with_user(self.u_b).action_revoke()
        self.assertTrue(self.key_b.revoked_at)
        self.assertEqual(self.key_b.revoked_by_id, self.u_b)

    def _device(self, user, key):
        return self.env["shrimp.api.device"].sudo().create({
            "name": "Teléfono", "user_id": user.id, "api_key_id": key.id,
            "public_key": _public_key_b64()})

    def test_revocar_dispositivo_ajeno(self):
        dev = self._device(self.u_a, self.key_a)
        with self.assertRaises(AccessError):
            dev.with_user(self.u_b).action_revoke()
        self.assertTrue(dev.active)
        self.assertFalse(self.key_a.revoked_at)
        dev.with_user(self.u_a).action_revoke()
        self.assertFalse(dev.active)
        self.assertTrue(self.key_a.revoked_at, "con el dispositivo cae su clave")

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    def test_webhook_ajeno_o_sin_gestionar_cuenta(self):
        Delivery = self.env["shrimp.webhook.delivery"]
        antes = Delivery.search_count([("subscription_id", "=", self.sub_a.id)])
        for user in (self.u_b, self.u_sub_a):
            sub = self.sub_a.with_user(user)
            with self.assertRaises(AccessError, msg=user.login):
                sub.action_rotate_secret()
            with self.assertRaises(AccessError, msg=user.login):
                sub.action_ping()
            with self.assertRaises(AccessError, msg=user.login):
                self.delivery_a.with_user(user).action_resend()
            with self.assertRaises(AccessError, msg=user.login):
                self.delivery_a.with_user(user).action_retry()
        self.assertEqual(self.sub_a.sudo().secret, "whsec_rpc_a")
        self.assertEqual(Delivery.search_count([("subscription_id", "=", self.sub_a.id)]), antes)
        self.assertEqual(self.delivery_a.state, "dead")
        self.assertEqual(self.delivery_a.resend_count, 0)

        self.sub_a.active = False
        for user in (self.u_b, self.u_sub_a):
            with self.assertRaises(AccessError, msg=user.login):
                self.sub_a.with_user(user).action_reactivate()
        self.assertFalse(self.sub_a.active)

    def test_webhook_propio(self):
        sub = self.sub_a.with_user(self.u_a)
        secret = sub.action_rotate_secret()
        self.assertTrue(secret.startswith("whsec_"))
        self.assertEqual(self.sub_a.sudo().secret, secret)
        self.assertEqual(sub.action_ping().event_type, "ping")
        self.delivery_a.with_user(self.u_a).action_resend()
        self.assertEqual(self.delivery_a.state, "pending")
        self.assertEqual(self.delivery_a.resend_count, 1)
        self.sub_a.active = False
        self.sub_a.with_user(self.u_a).action_reactivate()
        self.assertTrue(self.sub_a.active)

    # ------------------------------------------------------------------
    # Avisos de la app
    # ------------------------------------------------------------------
    def test_avisos_ajenos(self):
        notif = self.env["shrimp.app.notification"].sudo().create({
            "partner_id": self.farm_a.id, "kind": "purchase_confirmed", "title": "Aviso"})
        with self.assertRaises(AccessError):
            notif.with_user(self.u_b).action_mark_read()
        self.assertFalse(notif.is_read)
        self.assertEqual(notif.with_user(self.u_a).action_mark_read(), 1)
        self.assertTrue(notif.is_read)

    # ------------------------------------------------------------------
    # Trazabilidad pública (token del QR)
    # ------------------------------------------------------------------
    def _tx(self):
        return self.product.execute_purchase_flow(self.farm_a, 3)["transaction"]

    def test_trazabilidad_publica_ajena(self):
        tx = self._tx()
        token = tx.trace_token
        ajena = tx.with_user(self.u_b)
        with self.assertRaises(AccessError):
            ajena.traceability_url()
        with self.assertRaises(AccessError):
            ajena.action_rotate_trace_token()
        # El `actor` que llega por RPC no vale: se usa el socio del usuario.
        with self.assertRaises(AccessError):
            ajena.action_withdraw_public_trace(actor=self.farm_a)
        with self.assertRaises(AccessError):
            ajena.action_renew_public_trace(actor=self.farm_a)
        # Un subusuario solo con «ver» tampoco rota ni retira.
        with self.assertRaises(AccessError):
            tx.with_user(self.u_sub_a).action_rotate_trace_token()
        with self.assertRaises(AccessError):
            tx.with_user(self.u_sub_a).action_withdraw_public_trace()
        self.assertEqual(tx.trace_token, token)
        self.assertEqual(tx.shrimp_trace_public_status(), "public")

    def test_trazabilidad_publica_partes(self):
        tx = self._tx()
        token = tx.trace_token
        self.assertIn("/t/%s" % token, tx.with_user(self.u_a).traceability_url())
        self.assertIn("/t/%s" % token, tx.with_user(self.u_lab).traceability_url())
        tx.with_user(self.u_a).action_withdraw_public_trace()
        self.assertEqual(tx.shrimp_trace_public_status(), "withdrawn")
        tx.with_user(self.u_lab).action_rotate_trace_token()
        self.assertNotEqual(tx.trace_token, token)
        self.assertEqual(tx.shrimp_trace_public_status(), "public")
        # El controlador (sudo) sí pasa el actor.
        tx.with_user(self.u_lab).sudo().action_withdraw_public_trace(actor=self.lab)
        retiro = self.env["shrimp.trace.withdrawal"].search([("transaction_id", "=", tx.id)])
        self.assertEqual(retiro.withdrawn_partner_id, self.lab)
