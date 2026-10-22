"""Autorización de los métodos públicos de la maquila llamados por RPC.

En Odoo 19 /web/dataset/call_kw es auth="user": cualquier usuario del portal
llama a los métodos públicos sobre los ids que quiera. Los que trabajan en
sudo (acta de devolución, pedido de cancelación a la plataforma, retirarlos,
firmar el acta de devolución) comprobaban la parte con un `actor` que por RPC
no se puede falsificar, pero no el permiso del subusuario que sí exige el
portal (matriz de rutas). Aquí se llama con `.with_user(portal)` (NUNCA sudo):
eso es lo que ve un RPC del portal.
"""
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from .common import CopackCommon


def _subusuario(env, empresa, login, permisos):
    contacto = env["res.partner"].sudo().create({
        "name": "Sub %s" % login, "email": "%s@prueba.test" % login,
        "parent_id": empresa.id, "type": "contact"})
    user = env["res.users"].sudo().create({
        "name": contacto.name, "login": login, "partner_id": contacto.id,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    env["shrimp.account.member"].sudo()._shrimp_ensure(user, empresa, permissions=permisos)
    return user


@tagged("post_install", "-at_install")
class TestRpcAuthzCopacking(CopackCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Subusuarios que solo pueden VER: sin «vender» (planta) ni «comprar»
        # (cliente).
        cls.sub_maq = _subusuario(cls.env, cls.maq, "sub.maq.rpc", {"maquilador": {"view"}})
        cls.sub_cli = _subusuario(cls.env, cls.cli, "sub.cli.rpc", {"camaronera": {"view"}})

    # ------------------------------------------------------------------ ayudas
    def _recibida(self):
        sol = self.solicitud()
        _, orden = self.adjudicar(sol)
        orden.write({"received_lb": 40000})
        orden.action_register_reception()
        return sol, orden

    def _con_acta_de_devolucion(self):
        sol, orden = self._recibida()
        acta = orden.action_request_return("Avería", returned_lb=40000, actor=self.maq)
        return sol, orden, acta

    # ------------------------------------------------------- acta de devolución
    def test_solo_la_planta_pide_devolucion_o_cancelacion(self):
        _, orden = self._recibida()
        for user in (self.u_otro, self.u_cli, self.sub_maq):
            with self.assertRaises(AccessError, msg=user.login):
                orden.with_user(user).action_request_return("x", returned_lb=100)
            with self.assertRaises(AccessError, msg=user.login):
                orden.with_user(user).action_request_return(
                    "x", returned_lb=100, actor=self.maq.id)
            with self.assertRaises(AccessError, msg=user.login):
                orden.with_user(user).action_request_platform_cancel("x")
        self.assertFalse(orden.pending_cancellation_id)
        # La planta (su administrador) sí, también por RPC.
        orden.with_user(self.u_maq).action_request_return("Avería", returned_lb=40000)
        self.assertEqual(orden.pending_cancellation_id.kind, "return")

    def test_solo_el_cliente_firma_el_acta_de_devolucion(self):
        _, orden, acta = self._con_acta_de_devolucion()
        for user in (self.u_otro, self.u_maq, self.sub_cli):
            with self.assertRaises(AccessError, msg=user.login):
                acta.with_user(user).action_client_sign(True)
            with self.assertRaises(AccessError, msg=user.login):
                acta.with_user(user).action_client_sign(True, actor=self.cli.id)
        self.assertEqual(acta.state, "pending")
        self.assertEqual(orden.state, "received")
        acta.with_user(self.u_cli).action_client_sign(False, note="Esperen la reparación")
        self.assertEqual(acta.state, "rejected")
        self.assertEqual(orden.state, "received")

    def test_solo_la_planta_retira_su_solicitud(self):
        _, _, acta = self._con_acta_de_devolucion()
        for user in (self.u_otro, self.u_cli, self.sub_maq):
            with self.assertRaises(AccessError, msg=user.login):
                acta.with_user(user).action_withdraw()
            with self.assertRaises(AccessError, msg=user.login):
                acta.with_user(user).action_withdraw(actor=self.maq.id)
        self.assertEqual(acta.state, "pending")
        acta.with_user(self.u_maq).action_withdraw()
        self.assertEqual(acta.state, "withdrawn")

    def test_la_plataforma_no_decide_por_rpc_del_portal(self):
        _, orden, acta = self._con_acta_de_devolucion()
        for user in (self.u_otro, self.u_cli, self.u_maq):
            with self.assertRaises(AccessError, msg=user.login):
                acta.with_user(user).action_platform_decide(True, note="x")
        self.assertEqual(acta.state, "pending")
        self.assertEqual(orden.state, "received")

    # ------------------------------------------------------- orden y solicitud
    def test_sin_actor_no_cancela_como_plataforma(self):
        sol = self.solicitud()
        _, orden = self.adjudicar(sol)
        with self.assertRaises(AccessError):
            orden.with_user(self.u_otro).action_cancel()
        with self.assertRaises(AccessError):
            orden.with_user(self.u_otro).action_cancel(actor=self.cli.id)
        with self.assertRaises(AccessError):
            sol.with_user(self.u_otro).action_cancel()
        self.assertEqual(orden.state, "confirmed")
        self.assertEqual(sol.state, "assigned")

    def test_reabrir_el_acta_por_rpc(self):
        _, orden = self.hasta_empacar()
        orden.acceptance_ids.filtered(lambda f: f.role == "client").action_reject(
            "No cuadra", actor=self.cli)
        self.assertEqual(orden.acceptance_state, "disputed")
        with self.assertRaises(AccessError):
            orden.with_user(self.u_otro).action_reabrir_acta("x")
        with self.assertRaises(AccessError):
            orden.with_user(self.u_otro).action_reabrir_acta("x", actor=self.cli.id)
        for user in (self.u_otro, self.u_cli, self.u_maq):
            with self.assertRaises(AccessError, msg=user.login):
                orden.with_user(user).action_reabrir_acta("x", platform=True)
            with self.assertRaises(AccessError, msg=user.login):
                orden.with_user(user).action_mediation_uphold("x")
        self.assertEqual(orden.acceptance_state, "disputed")
        self.assertEqual(orden.state, "packed")

    def test_firmas_y_ofertas_no_aceptan_actor_ajeno(self):
        _, orden = self.hasta_empacar()
        firma = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        with self.assertRaises(AccessError):
            firma.with_user(self.u_otro).action_accept(actor=self.cli.id)
        with self.assertRaises(AccessError):
            firma.with_user(self.u_maq).action_reject("x", actor=self.cli.id)
        self.assertEqual(firma.decision, "pending")

        sol = self.solicitud(dirigida=False)
        oferta = self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": self.maq.id,
            "rate_per_lb": 0.2, "capacity_lb": sol.quantity_lb,
            "available_from": sol.needed_from, "available_to": sol.needed_to,
        })
        for metodo in ("action_accept", "action_reject"):
            with self.assertRaises(AccessError, msg=metodo):
                getattr(oferta.with_user(self.u_maq), metodo)(actor=self.cli.id)
        with self.assertRaises(AccessError):
            oferta.with_user(self.u_otro).action_withdraw(actor=self.maq.id)
        self.assertEqual(oferta.state, "sent")

    def test_empaque_propio_no_se_llama_por_rpc(self):
        Orden = self.env["shrimp.copack.order"].with_user(self.u_cli)
        with self.assertRaises(AccessError):
            get_public_method(Orden, "shrimp_create_self_packing")
        # Lo demás de la orden sigue siendo público (botones del backoffice).
        # (con el entorno del servidor: al portal la puerta general de ir.http
        # le niega cualquier método de shrimp.*, ver shrimp_rpc_gate.py).
        self.assertTrue(get_public_method(self.env["shrimp.copack.order"], "action_close"))
