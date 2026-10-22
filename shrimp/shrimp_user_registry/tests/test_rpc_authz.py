"""Métodos públicos llamados por RPC desde el portal.

En Odoo 19 /web/dataset/call_kw y call_button son auth="user": un usuario
del portal llamaba métodos públicos que trabajan en sudo() sobre cualquier
id (hacerse administrador de cualquier empresa, suspender o rechazar
cuentas, desactivar usuarios ajenos...). Se comprueba:

* cada método se protege solo (llamado con el entorno del usuario del
  portal, sin sudo, como lo hace call_kw);
* la puerta general (models/shrimp_rpc_gate.py) niega por RPC los modelos
  shrimp.* y los métodos action_* / shrimp_* a los usuarios del portal,
  salvo la lista blanca;
* los caminos legítimos siguen: el administrador de la cuenta transfiere
  la administración desde /my/team y la plataforma suspende desde el
  back-office.
"""
import re

from odoo.exceptions import AccessError
from odoo.service.model import call_kw, get_public_method
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_user_registry.models.shrimp_rpc_gate import (
    RPC_ALLOWLIST, shrimp_rpc_denied)

CLAVE = "Clave-de-prueba-rpc-123"


def _socios(env, sufijo):
    """Empresa A (administrador + dos subusuarios) y empresa B (ajena)."""
    P = env["res.partner"].sudo()
    Users = env["res.users"].sudo()
    Member = env["shrimp.account.member"].sudo()
    portal = env.ref("base.group_portal")
    s = {}
    for clave, ruc in (("a", "0912388001001"), ("b", "0912388002001")):
        s[clave] = P.create({
            "name": "Lab RPC %s %s" % (clave, sufijo), "is_company": True,
            "email": "lab.rpc.%s.%s@prueba.test" % (clave, sufijo),
            "vat_or_id": ruc[:-3] + sufijo, "shrimp_user_type": "laboratorio",
            "shrimp_razon_social": "Lab RPC %s S.A." % clave, "shrimp_ubicacion": "Durán"})
        s["u_" + clave] = Users.create({
            "name": "Admin %s" % clave, "login": "rpc.%s.%s" % (clave, sufijo),
            "email": "lab.rpc.%s.%s@prueba.test" % (clave, sufijo), "password": CLAVE,
            "partner_id": s[clave].id, "group_ids": [(6, 0, [portal.id])]})
        Member._shrimp_ensure(s["u_" + clave], s[clave], is_admin=True)
    for clave in ("sub", "sub2"):
        contacto = P.create({"name": "%s RPC %s" % (clave, sufijo), "parent_id": s["a"].id,
                             "email": "%s.rpc.%s@prueba.test" % (clave, sufijo), "type": "contact"})
        s["u_" + clave] = Users.create({
            "name": contacto.name, "login": "%s.rpc.%s" % (clave, sufijo), "password": CLAVE,
            "partner_id": contacto.id, "group_ids": [(6, 0, [portal.id])]})
        Member._shrimp_ensure(s["u_" + clave], s["a"],
                              permissions={"laboratorio": {"view", "buy", "sell"}})
    s["interno"] = Users.create({
        "name": "Interno RPC", "login": "interno.rpc.%s" % sufijo, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_user").id])]})
    s["plataforma"] = Users.create({
        "name": "Plataforma RPC", "login": "plataforma.rpc.%s" % sufijo, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_system").id])]})
    return s


@tagged("post_install", "-at_install")
class TestRpcAuthzRegistro(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = _socios(cls.env, "101")
        cls.Member = cls.env["shrimp.account.member"].sudo()

    def _portales(self):
        return [self.s["u_sub"], self.s["u_b"], self.s["u_a"]]

    # ------------------------------------------------------------------
    # 1. Miembros de la cuenta: hacerse administrador / (des)activar
    # ------------------------------------------------------------------
    def test_miembros_no_por_rpc(self):
        m = self.s["u_sub"]._shrimp_member()
        Member = self.env["shrimp.account.member"].with_user(self.s["u_sub"])
        for nombre in ("_shrimp_make_admin", "_shrimp_set_active"):
            with self.assertRaises(AccessError):
                get_public_method(Member, nombre)
        for viejo in ("action_make_admin", "action_set_active"):
            with self.assertRaises(AttributeError):
                get_public_method(Member, viejo)
        # Y la puerta general niega cualquier método del modelo al portal.
        with mute_logger("odoo.addons.shrimp_user_registry.models.shrimp_rpc_gate"), \
                self.assertRaises(AccessError):
            call_kw(Member, "read", [[m.id], ["is_admin"]], {})
        self.assertFalse(m.is_admin)
        # El camino del servidor sigue: el controlador llama en sudo.
        m._shrimp_make_admin()
        self.assertTrue(m.is_admin)
        self.assertFalse(self.s["u_a"]._shrimp_member().is_admin)

    # ------------------------------------------------------------------
    # 2. Cuentas y perfiles: suspender, rechazar, aprobar
    # ------------------------------------------------------------------
    def test_cuenta_solo_la_plataforma(self):
        cuenta = self.s["b"]
        linea = cuenta.shrimp_role_ids[:1]
        self.assertTrue(linea)
        for user in self._portales() + [self.s["interno"]]:
            P = cuenta.with_user(user)
            for metodo, kw in (("action_shrimp_suspend_account", {"reason": "x"}),
                               ("action_shrimp_unsuspend_account", {}),
                               ("action_shrimp_reject_account", {"reason": "x"}),
                               ("action_shrimp_approve_account", {}),
                               ("action_shrimp_reset_account", {})):
                with self.subTest(user=user.login, metodo=metodo), self.assertRaises(AccessError):
                    getattr(P, metodo)(**kw)
            R = linea.with_user(user)
            for metodo, kw in (("action_reject", {"reason": "x"}), ("action_approve", {}),
                               ("action_reset", {})):
                with self.subTest(user=user.login, metodo=metodo), self.assertRaises(AccessError):
                    getattr(R, metodo)(**kw)
        self.assertFalse(cuenta.shrimp_suspended)
        self.assertNotEqual(linea.state, "rejected")
        # El administrador de la plataforma sí (botón y asistente).
        admin = self.s["plataforma"]
        self.env["shrimp.account.suspend"].with_user(admin).create({
            "partner_ids": [(6, 0, cuenta.ids)], "reason": "Revisión RPC"}).action_confirm()
        self.assertTrue(cuenta.shrimp_suspended)
        cuenta.with_user(admin).action_shrimp_unsuspend_account()
        self.assertFalse(cuenta.shrimp_suspended)
        linea.with_user(admin).action_reject(reason="Documentos incompletos")
        self.assertEqual(linea.state, "rejected")

    def test_solicitud_lopdp_solo_la_plataforma(self):
        req = self.env["shrimp.data.request"].sudo().create({
            "request_type": "acceso", "partner_id": self.s["b"].id, "account_id": self.s["b"].id})
        for user in (self.s["u_sub"], self.s["u_b"]):
            R = req.with_user(user)
            for metodo in ("action_generate_export", "action_anonymize", "action_start",
                           "action_reopen"):
                with self.subTest(user=user.login, metodo=metodo), self.assertRaises(AccessError):
                    getattr(R, metodo)()
        self.assertFalse(req.export_attachment_id)
        with self.assertRaises(AccessError):
            self.env["shrimp.data.retention.run"].with_user(self.s["u_b"]).action_run_dry()

    # ------------------------------------------------------------------
    # 5. Puerta general del RPC
    # ------------------------------------------------------------------
    def test_puerta_general(self):
        env_portal = self.env(user=self.s["u_sub"], su=False)
        self.assertTrue(shrimp_rpc_denied(env_portal, "shrimp.account.member", "read"))
        self.assertTrue(shrimp_rpc_denied(env_portal, "res.partner", "action_shrimp_suspend_account"))
        self.assertTrue(shrimp_rpc_denied(env_portal, "res.partner", "shrimp_is_operational"))
        self.assertFalse(shrimp_rpc_denied(env_portal, "res.partner", "read"))
        for modelo, metodo in RPC_ALLOWLIST:
            self.assertFalse(shrimp_rpc_denied(env_portal, modelo, metodo))
        # Internos y sudo: sin límite.
        env_interno = self.env(user=self.s["interno"], su=False)
        self.assertFalse(shrimp_rpc_denied(env_interno, "shrimp.account.member", "read"))
        self.assertFalse(shrimp_rpc_denied(env_portal(su=True), "shrimp.account.member", "read"))
        # call_kw (el mismo que usan /web/dataset, /jsonrpc y /xmlrpc) la aplica.
        with mute_logger("odoo.addons.shrimp_user_registry.models.shrimp_rpc_gate"), \
                self.assertRaises(AccessError):
            call_kw(self.env["res.partner"].with_user(self.s["u_sub"]),
                    "action_shrimp_suspend_account", [self.s["b"].ids], {"reason": "x"})
        self.assertFalse(self.s["b"].shrimp_suspended)


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto) or \
        re.search(r'value="([^"]+)"\s+name="csrf_token"', texto)
    return m.group(1) if m else ""


@tagged("post_install", "-at_install")
class TestRpcAuthzRegistroHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = _socios(cls.env, "102")

    def _rpc(self, ruta, modelo, metodo, args, kwargs=None):
        r = self.url_open(ruta, json={"jsonrpc": "2.0", "method": "call", "id": 1, "params": {
            "model": modelo, "method": metodo, "args": args, "kwargs": kwargs or {}}})
        self.assertEqual(r.status_code, 200)
        return r.json()

    @mute_logger("odoo.addons.shrimp_user_registry.models.shrimp_rpc_gate", "odoo.http")
    def test_call_kw_del_portal(self):
        self.authenticate(self.s["u_sub"].login, CLAVE)
        m = self.s["u_sub"]._shrimp_member()
        # Antes: el subusuario se hacía administrador de su empresa.
        res = self._rpc("/web/dataset/call_kw/shrimp.account.member/action_make_admin",
                        "shrimp.account.member", "action_make_admin", [[m.id]])
        self.assertIn("error", res)
        res = self._rpc("/web/dataset/call_kw/shrimp.account.member/_shrimp_make_admin",
                        "shrimp.account.member", "_shrimp_make_admin", [[m.id]])
        self.assertIn("error", res)
        # ... o suspendía cualquier cuenta.
        res = self._rpc("/web/dataset/call_button", "res.partner",
                        "action_shrimp_suspend_account", [self.s["b"].ids], {"reason": "x"})
        self.assertIn("error", res)
        self.assertEqual(res["error"]["data"]["name"], "odoo.exceptions.AccessError")
        res = self._rpc("/web/dataset/call_kw", "shrimp.partner.role", "read",
                        [self.s["b"].shrimp_role_ids.ids, ["state"]])
        self.assertIn("error", res)
        self.assertFalse(m.is_admin)
        self.assertFalse(self.s["b"].shrimp_suspended)
        # Lo que no toca la regla sigue funcionando (p. ej. leer su usuario).
        res = self._rpc("/web/dataset/call_kw/res.users/read", "res.users", "read",
                        [[self.s["u_sub"].id], ["name"]])
        self.assertNotIn("error", res)
        self.assertEqual(res["result"][0]["name"], self.s["u_sub"].name)

    def test_portal_transferir_administracion(self):
        """El administrador de la cuenta transfiere la administración y
        desactiva a un usuario desde /my/team (llaman en sudo)."""
        self.authenticate(self.s["u_a"].login, CLAVE)
        token = _csrf(self.url_open("/my/team").text)
        self.assertTrue(token)
        m2 = self.s["u_sub2"]._shrimp_member()
        self.url_open("/my/team/%s/toggle" % m2.uuid_ref, data={"csrf_token": token})
        self.assertFalse(self.s["u_sub2"].with_context(active_test=False).active)
        m = self.s["u_sub"]._shrimp_member()
        self.url_open("/my/team/%s/make-admin" % m.uuid_ref,
                      data={"csrf_token": token, "confirm": "1"})
        self.assertTrue(m.is_admin)
        self.assertFalse(self.s["u_a"]._shrimp_member().is_admin)

    def test_plataforma_suspende_desde_el_backoffice(self):
        self.authenticate(self.s["plataforma"].login, CLAVE)
        res = self._rpc("/web/dataset/call_kw/shrimp.account.suspend/create",
                        "shrimp.account.suspend", "create",
                        [{"partner_ids": [(6, 0, self.s["b"].ids)], "reason": "Revisión"}])
        self.assertNotIn("error", res)
        wizard_id = res["result"]
        wizard_id = wizard_id[0] if isinstance(wizard_id, list) else wizard_id
        res = self._rpc("/web/dataset/call_button/shrimp.account.suspend/action_confirm",
                        "shrimp.account.suspend", "action_confirm", [[wizard_id]])
        self.assertNotIn("error", res)
        self.assertTrue(self.s["b"].shrimp_suspended)
        res = self._rpc("/web/dataset/call_button", "res.partner",
                        "action_shrimp_unsuspend_account", [self.s["b"].ids])
        self.assertNotIn("error", res)
        self.assertFalse(self.s["b"].shrimp_suspended)

    def test_usuario_interno_sin_admin_no_suspende(self):
        self.authenticate(self.s["interno"].login, CLAVE)
        with mute_logger("odoo.http"):
            res = self._rpc("/web/dataset/call_button", "res.partner",
                            "action_shrimp_suspend_account", [self.s["b"].ids], {"reason": "x"})
        self.assertIn("error", res)
        self.assertFalse(self.s["b"].shrimp_suspended)
