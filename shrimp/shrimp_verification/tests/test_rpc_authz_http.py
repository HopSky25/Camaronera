"""Aceptar / declinar la orden: JSON-RPC bloqueado, portal funcionando.

Antes cualquier usuario del portal aceptaba o declinaba por
/web/dataset/call_kw la orden de otra verificadora
(action_verifier_accept / action_verifier_decline trabajaban en sudo sin
comprobar quién llama). Ahora el método exige al administrador de la
verificadora asignada y, además, la puerta general de ir.http niega al
portal cualquier RPC del ORM sobre shrimp.*. El administrador sigue
respondiendo desde su pantalla.
"""
import re

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_marketplace.tests.common import CLAVE

from .test_decisiones_verificacion import DecisionesCommon

GATE_LOG = "odoo.addons.shrimp_user_registry.models.shrimp_rpc_gate"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto) or \
        re.search(r'value="([^"]+)"\s+name="csrf_token"', texto)
    return m.group(1) if m else ""


@tagged("post_install", "-at_install")
class TestRpcAuthzVerificacionHttp(DecisionesCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["shrimp.account.member"].sudo()._shrimp_ensure(
            cls.s["u_verif"], cls.s["verif"], is_admin=True)
        # Sin dominio propio del sitio del verificador: si no, redirige a
        # verificadores.localhost (petición externa en las pruebas).
        sitio = cls.env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _rpc(self, modelo, metodo, args, kwargs=None):
        r = self.url_open("/web/dataset/call_kw/%s/%s" % (modelo, metodo), json={
            "jsonrpc": "2.0", "method": "call", "id": 1, "params": {
                "model": modelo, "method": metodo, "args": args, "kwargs": kwargs or {}}})
        self.assertEqual(r.status_code, 200)
        return r.json()

    @mute_logger(GATE_LOG, "odoo.http")
    def test_json_rpc_bloqueado(self):
        v = self.compra("Larva rpc http")["verification"]
        for user in (self.s["u_lab2"], self.s["u_cam"], self.s["u_verif"]):
            self.authenticate(user.login, CLAVE)
            res = self._rpc("shrimp.verification", "action_verifier_decline", [v.ids, "x"])
            self.assertIn("error", res, user.login)
            res = self._rpc("shrimp.verification", "action_verifier_accept", [v.ids])
            self.assertIn("error", res, user.login)
        self.assertEqual(v.verifier_response, "pending")

    def test_admin_declina_por_el_portal(self):
        v = self.compra("Larva portal declina")["verification"]
        self.authenticate(self.s["u_verif"].login, CLAVE)
        pagina = self.url_open("/verifier/verifications/%s/assign-technician" % v.uuid_ref)
        self.assertEqual(pagina.status_code, 200)
        r = self.url_open("/verifier/verifications/%s/respond" % v.uuid_ref, data={
            "csrf_token": _csrf(pagina.text), "decision": "decline",
            "reason": "Sin capacidad esa semana"}, allow_redirects=False)
        self.assertIn("saved=declined", r.headers.get("Location", ""))
        self.assertEqual(v.verifier_response, "declined")
        self.assertEqual(v.verifier_decline_reason, "Sin capacidad esa semana")
