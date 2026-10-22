"""Recepción de la compra llamada por RPC desde el portal.

Antes, cualquier usuario del portal forzaba por /web/dataset/call_kw que una
compra confirmada quedara completada: action_receive / action_receive_ref
eran públicos y trabajaban en sudo, y shrimp_confirm_reception(auto=True)
no comprobaba la parte. También se podían colar por el contexto las banderas
shrimp_reception_* (saltarse la fecha, darla por verificada...).
"""
from datetime import timedelta

from odoo.exceptions import AccessError, ValidationError
from odoo.service.model import get_public_method
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from .common import CLAVE, crear_socios, csrf_de, producto
from .test_recepcion import RecepcionCommon

GATE_LOG = "odoo.addons.shrimp_user_registry.models.shrimp_rpc_gate"


@tagged("post_install", "-at_install")
class TestRpcAuthzRecepcion(RecepcionCommon):

    def test_action_receive_no_es_rpc(self):
        for metodo in ("action_receive", "action_receive_ref"):
            with self.assertRaises(AccessError, msg=metodo):
                get_public_method(self.Tx, metodo)

    def test_tercero_no_confirma_ni_con_auto(self):
        tx = self.comprar("sem", "lab")
        for user in (self.s["u_lab2"], self.s["u_sem"]):
            T = tx.with_user(user)
            with self.assertRaises(AccessError, msg=user.login):
                T.shrimp_confirm_reception(auto=True)
            with self.assertRaises(AccessError, msg=user.login):
                T.shrimp_confirm_reception(actor=self.s["lab"].id)
            with self.assertRaises(AccessError, msg=user.login):
                T.with_context(shrimp_reception_verified=True,
                               shrimp_reception_auto=True).shrimp_confirm_reception(auto=True)
            with self.assertRaises(AccessError, msg=user.login):
                T.shrimp_report_reception_issue("x", actor=self.s["lab"].id)
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_lab2"]).action_mark_dispatched(actor=self.s["sem"].id)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "confirmed")
        self.assertFalse(tx.dispatched_at)
        self.assertFalse(tx.reception_status == "incidence")

    def test_comprador_por_rpc_sin_banderas(self):
        """El comprador sí confirma lo suyo, pero `auto` y las banderas del
        contexto no valen: queda como recepción normal, a su nombre."""
        tx = self.comprar("sem", "lab")
        comprador = self.s["u_lab"]
        tx.with_user(comprador).with_context(
            shrimp_reception_verified=True, shrimp_reception_user=self.s["u_sem"].id,
        ).shrimp_confirm_reception(auto=True, actor=self.s["sem"].id)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "received")
        self.assertFalse(tx.reception_auto_confirmed)
        self.assertEqual(tx.received_by_id, comprador)

    def test_fecha_futura_no_se_salta_por_contexto(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        with self.assertRaises(ValidationError):
            tx.with_user(self.s["u_lab"]).with_context(
                shrimp_reception_skip_date=True).shrimp_confirm_reception()
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "confirmed")

    def test_cron_sigue_confirmando(self):
        tx = self.comprar("sem", "lab")
        tx.sudo().shrimp_confirm_reception(auto=True)
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "auto")


@tagged("post_install", "-at_install")
class TestRpcAuthzRecepcionHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "rpr")
        cls.Tx = cls.env["shrimp.transaction"]
        cls.hoy = cls.Tx._shrimp_today()

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _rpc(self, modelo, metodo, args, kwargs=None, ruta="/web/dataset/call_kw"):
        r = self.url_open(ruta, json={"jsonrpc": "2.0", "method": "call", "id": 1, "params": {
            "model": modelo, "method": metodo, "args": args, "kwargs": kwargs or {}}})
        self.assertEqual(r.status_code, 200)
        return r.json()

    @mute_logger(GATE_LOG, "odoo.http")
    def test_rpc_bloqueado_y_portal_funciona(self):
        lote = producto(self.env, self.s["sem"], nombre="Nauplio rpc rcp",
                        expected_delivery_date=self.hoy)
        tx = lote.execute_purchase_flow(self.s["lab"], 3.0)["transaction"]
        tx.sudo().write({"desired_date": self.hoy})
        # Un tercero (y el propio comprador) no la cierran por JSON-RPC.
        for user in (self.s["u_lab2"], self.s["u_lab"]):
            self.authenticate(user.login, CLAVE)
            for metodo, kw in (("action_receive", {}), ("action_receive_ref", {}),
                               ("shrimp_confirm_reception", {"auto": True})):
                res = self._rpc("shrimp.transaction", metodo, [tx.ids], kw)
                self.assertIn("error", res, "%s %s" % (user.login, metodo))
            res = self._rpc("shrimp.transaction", "shrimp_confirm_reception", [tx.ids],
                            {"auto": True}, ruta="/web/dataset/call_button")
            self.assertIn("error", res)
        self.assertEqual(tx.state, "confirmed")
        # El comprador confirma por el portal (el controlador llama en sudo).
        self.authenticate(self.s["u_lab"].login, CLAVE)
        lista = self.url_open("/marketplace/purchases")
        r = self.url_open("/marketplace/purchases/%s/receive" % tx.uuid_ref, data={
            "csrf_token": csrf_de(lista.text), "note": "Todo bien",
            "next": "/marketplace/purchases"}, allow_redirects=False)
        self.assertIn("saved=received", r.headers.get("Location", ""))
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "received")
