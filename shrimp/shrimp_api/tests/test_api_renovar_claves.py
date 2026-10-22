"""«Renovar» una clave de API (D-46): solo su dueño, y nunca una clave de
supervisor ni de «actuar como».

``action_renew`` es un método público del modelo: cualquier usuario con
sesión lo puede llamar por /web/dataset/call_kw con los ids que quiera. Antes
iteraba en sudo sin mirar de quién era la clave y alargaba un año la de otro
socio, o una clave de supervisor (tope de 8 h)."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import JsonRpcException

from .common import ApiCase

PASSWORD = "Clave-api-123"


@tagged("post_install", "-at_install", "shrimp_api")
class TestRenovarClaveAjena(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.u_sup = env["res.users"].create({
            "name": "Supervisora Renovar", "login": "sup.renovar.api", "password": PASSWORD,
            "email": "sup.renovar.api@prueba.test",
            "group_ids": [(6, 0, [env.ref("base.group_user").id,
                                  env.ref("shrimp_api.group_shrimp_app_supervisor").id])]})

    def _vence_pronto(self, key):
        key.sudo().write({"expires_at": fields.Datetime.now() + timedelta(days=2)})
        self.env.flush_all()
        return key.sudo().expires_at

    def test_orm_no_renueva_la_clave_de_otro(self):
        antes = self._vence_pronto(self.key_b)
        with self.assertRaises(AccessError):
            self.key_b.with_user(self.u_a).action_renew()
        # Mezclando una propia con una ajena tampoco.
        with self.assertRaises(AccessError):
            (self.key_a | self.key_b).with_user(self.u_a).action_renew()
        self.key_b.invalidate_recordset()
        self.assertEqual(self.key_b.sudo().expires_at, antes)
        self.assertFalse(self.key_b.sudo().renewed_at)

    def test_orm_el_dueno_renueva_la_suya(self):
        antes = self._vence_pronto(self.key_a)
        self.key_a.with_user(self.u_a).action_renew()
        self.assertGreater(self.key_a.sudo().expires_at, antes + timedelta(days=300))
        self.assertTrue(self.key_a.sudo().renewed_at)

    def test_call_kw_no_renueva_la_clave_de_otro(self):
        antes = self._vence_pronto(self.key_b)
        self.authenticate("a.api", PASSWORD)
        with self.assertRaises(JsonRpcException) as cm:
            self.make_jsonrpc_request("/web/dataset/call_kw/shrimp.api.key/action_renew", {
                "model": "shrimp.api.key", "method": "action_renew",
                "args": [[self.key_b.id]], "kwargs": {}})
        self.assertIn("AccessError", str(cm.exception))
        self.env.invalidate_all()
        self.assertEqual(self.key_b.sudo().expires_at, antes)
        self.assertFalse(self.key_b.sudo().renewed_at)
        # La suya sí.
        self._vence_pronto(self.key_a)
        self.make_jsonrpc_request("/web/dataset/call_kw/shrimp.api.key/action_renew", {
            "model": "shrimp.api.key", "method": "action_renew",
            "args": [[self.key_a.id]], "kwargs": {}})
        self.env.invalidate_all()
        self.assertTrue(self.key_a.sudo().renewed_at)

    def test_supervisor_y_actuar_como_no_se_renuevan(self):
        Key = self.env["shrimp.api.key"]
        sup_key, _raw = Key.api_create_supervisor_key(self.u_sup, "app")
        act_key, _raw = sup_key.api_create_act_as_key(self.u_a)
        for key in (sup_key, act_key):
            antes = key.sudo().expires_at
            self.assertFalse(key.sudo()._can_renew())
            # Ni su dueño, ni el administrador, ni en sudo.
            with self.assertRaises(ValidationError):
                key.with_user(key.sudo().user_id).action_renew()
            with self.assertRaises(ValidationError):
                key.sudo().action_renew()
            key.invalidate_recordset()
            self.assertEqual(key.sudo().expires_at, antes)
            self.assertLessEqual(key.sudo().expires_at,
                                 fields.Datetime.now() + timedelta(hours=8, minutes=1))
        # El portal tampoco ofrece el botón ni acepta el POST.
        self.authenticate("a.api", PASSWORD)
        page = self.url_open("/my/api-keys").text
        self.assertNotIn("/my/api-keys/%s/renew" % act_key.uuid_ref, page)
        self.assertIn("/my/api-keys/%s/renew" % self.key_a.uuid_ref, page)
