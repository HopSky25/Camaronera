"""Autorización de los métodos públicos de la verificación llamados por RPC.

En Odoo 19 /web/dataset/call_kw es auth="user": cualquier usuario del portal
llama a los métodos públicos sobre los ids que quiera, con los argumentos
(JSON: enteros, textos) y el contexto que quiera. Aquí se llama como lo haría
ese RPC (`call_kw` con `.with_user(portal)`, NUNCA en sudo). El camino
legítimo (el controlador o la API, que llaman en sudo tras comprobar quién es
quién) se prueba al lado para ver que sigue funcionando.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE
from odoo.addons.shrimp_marketplace.tests.test_permisos_subcuentas import subusuario

from .common_seguridad import abrir_ronda
from .test_decisiones_verificacion import DecisionesCommon

# Métodos que solo llama el servidor (portal, API, reserva): @api.private.
PRIVADOS = [
    ("shrimp.verification", "action_declared_save"),
    ("shrimp.verification", "action_declared_submit"),
    ("shrimp.dispatch", "registrar_plan"),
    ("shrimp.dispatch", "registrar_llegada"),
    ("shrimp.product", "start_verified_purchase"),
]


@tagged("post_install", "-at_install")
class TestRpcAuthzVerificacion(DecisionesCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Member = cls.env["shrimp.account.member"].sudo()
        Member._shrimp_ensure(cls.s["u_verif"], cls.s["verif"], is_admin=True)
        Member._shrimp_ensure(cls.s["u_lab"], cls.s["lab"], is_admin=True)
        # Subusuarios que solo pueden VER (sin «aprobar» ni «vender»).
        cls.sub_ver = subusuario(cls.env, cls.s["verif"], "verif.ver.rpc",
                                 {"verificador": {"view"}})
        cls.sub_lab = subusuario(cls.env, cls.s["lab"], "lab.ver.rpc",
                                 {"laboratorio": {"view"}})
        # El administrador de OTRA verificadora y un socio ajeno a la compra.
        cls.u_verif2 = cls.env["res.users"].create({
            "name": cls.s["verif2"].name, "login": "verif2.rpc", "partner_id": cls.s["verif2"].id,
            "password": CLAVE,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        cls.ajeno = cls.s["u_lab2"]

    def rpc(self, user, model, method, ids, *args, **kwargs):
        """Lo que hace /web/dataset/call_kw (o call_button) con ese usuario,
        SIN la puerta general de ir.http (shrimp_rpc_gate), que a un usuario
        del portal le niega cualquier método de shrimp.*: así se prueba que
        cada método se protege solo."""
        publico = getattr(get_public_method, "__wrapped__", get_public_method)
        publico(self.env[model], method)
        return getattr(self.env[model].with_user(user).browse(ids), method)(*args, **kwargs)

    def no_verificadora(self):
        """Quien no es el administrador de la verificadora asignada."""
        return (self.ajeno, self.u_verif2, self.s["u_tec"], self.sub_ver, self.s["u_cam"])

    # ------------------------------------------------------------------
    def test_privados_no_se_llaman_por_rpc(self):
        for model, method in PRIVADOS:
            with self.assertRaises(AccessError, msg="%s.%s" % (model, method)):
                get_public_method(self.env[model], method)
        # Los botones del back-office siguen siendo públicos.
        get_public_method(self.env["shrimp.verification"], "action_retry_payout")
        get_public_method(self.env["shrimp.verification"], "action_assign_technician")

    # ---- D-21: aceptar o declinar la orden (arreglo de la respuesta) -----
    def test_aceptar_declinar_solo_el_admin_de_la_verificadora(self):
        v = self.compra("Larva rpc responde")["verification"]
        for user in self.no_verificadora():
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.verification", "action_verifier_accept", v.ids)
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.verification", "action_verifier_decline", v.ids, "x")
        self.env.invalidate_all()
        self.assertEqual(v.verifier_response, "pending")
        # El administrador (portal) sí, también por RPC.
        self.rpc(self.s["u_verif"], "shrimp.verification", "action_verifier_accept", v.ids)
        self.env.invalidate_all()
        self.assertEqual(v.verifier_response, "accepted")

        otra = self.compra("Larva rpc declina")["verification"]
        v_admin = otra.with_user(self.s["u_verif"])
        v_admin.action_verifier_decline("Sin capacidad esa semana")
        self.env.invalidate_all()
        self.assertEqual(otra.verifier_response, "declined")

    # ---- Reparto del trabajo: técnico responsable y equipo ---------------
    def test_asignar_tecnico_y_equipo_solo_el_admin(self):
        v = self.compra("Larva rpc asigna")["verification"]
        v.with_user(self.s["u_verif"]).sudo().action_assign_technician(self.s["tec"])
        self.assertEqual(v.state, "assigned")
        # Sin respuesta registrada: asignar (otra vez) la daría por aceptada.
        v.sudo().write({"verifier_response": "pending"})
        mensajes = len(v.sudo().message_ids)
        for user in self.no_verificadora():
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.verification", "action_assign_technician", v.ids)
            for metodo in ("action_add_support_technician", "action_remove_support_technician",
                           "action_make_responsible"):
                with self.assertRaises(AccessError, msg="%s %s" % (user.login, metodo)):
                    self.rpc(user, "shrimp.verification", metodo, v.ids, self.s["tec"].id)
        self.env.invalidate_all()
        self.assertEqual(v.verifier_response, "pending")
        self.assertEqual(len(v.sudo().message_ids), mensajes, "sin avisos")
        # El controlador (admin, en sudo) sigue funcionando.
        v.with_user(self.s["u_verif"]).sudo().action_assign_technician()
        self.assertEqual(v.verifier_response, "accepted")

    # ---- D-16: rechazo por «Producto no presentado» -----------------------
    def test_no_presentado_solo_quien_dictamina(self):
        v = self.compra("Larva rpc no presentado")["verification"]
        v.sudo().action_assign_technician(self.s["tec"])
        for user in (self.ajeno, self.u_verif2, self.sub_ver, self.s["u_cam"], self.s["u_lab"]):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.verification", "action_not_presented", v.ids,
                         False, "No estaba")
        self.env.invalidate_all()
        self.assertFalse(v.rejection_kind)
        self.assertEqual(v.state, "assigned")
        # El técnico responsable y el administrador pasan el control.
        self.assertTrue(v.with_user(self.s["u_tec"])._shrimp_check_verifier_caller(verdict=True))
        self.assertTrue(v.with_user(self.s["u_verif"])._shrimp_check_verifier_caller(verdict=True))
        with self.assertRaises(AccessError):
            v.with_user(self.s["u_tec"])._shrimp_check_verifier_caller()  # repartir: solo el admin

    # ---- Botones del back-office -----------------------------------------
    def test_reintentos_de_facturacion_solo_la_plataforma(self):
        v = self.compra("Larva rpc liquida")["verification"]
        v.sudo().write({"payout_state": "error", "payout_attempts": 3})
        Ver = type(self.env["shrimp.verification"])
        with patch.object(Ver, "_create_verifier_bill") as liquidar:
            for user in (self.ajeno, self.s["u_verif"], self.s["u_cam"]):
                with self.assertRaises(AccessError, msg=user.login):
                    self.rpc(user, "shrimp.verification", "action_retry_payout", v.ids)
                with self.assertRaises(AccessError, msg=user.login):
                    self.rpc(user, "shrimp.verification",
                             "action_generate_verification_documents", v.ids)
            self.assertFalse(liquidar.called)
            self.env.invalidate_all()
            self.assertEqual(v.payout_attempts, 3)
            # La plataforma sí.
            v.action_retry_payout()
            self.assertTrue(liquidar.called)
        self.assertEqual(v.payout_attempts, 0)

    # ---- Aceptación del informe por las partes ----------------------------
    def test_postura_no_se_firma_por_otro(self):
        v = abrir_ronda(self.compra("Larva rpc ronda")["verification"], cumple=False)
        comprador = self.postura(v, "buyer")
        for user in (self.ajeno, self.s["u_lab"], self.s["u_verif"]):
            for metodo, args in (("action_accept", ()), ("action_reject", ("no",)),
                                 ("action_counter", (1.0,))):
                with self.assertRaises(AccessError, msg="%s %s" % (user.login, metodo)):
                    self.rpc(user, "shrimp.verification.acceptance", metodo, comprador.ids,
                             *args, actor=self.s["cam"].id)
        self.env.invalidate_all()
        self.assertEqual(comprador.decision, "pending")
        # El controlador (la parte, en sudo) sigue funcionando.
        comprador.with_user(self.s["u_cam"]).sudo().action_accept(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "accepted")

    # ---- D-25 y D-40: segunda opinión y cambio de verificadora ------------
    def test_segunda_opinion_y_cambio_solo_la_parte(self):
        r = self.compra("Larva rpc impugna")
        v, tx = self.en_campo(r["verification"], cumple=False), r["transaction"]
        v.sudo()._close("rejected", notes="Estado sanitario inaceptable")
        self.assertEqual(v.contest_state, "open")
        for user in (self.ajeno, self.sub_lab, self.s["u_cam"]):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.verification", "action_request_second_opinion", v.ids,
                         self.s["verif2"].id, actor=self.s["lab"].id)
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.transaction", "action_request_second_opinion", tx.ids,
                         self.s["verif2"].id, actor=self.s["lab"].id)
        for user in (self.ajeno, self.s["u_lab"]):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.transaction", "action_switch_verifier", tx.ids,
                         self.s["verif2"].id, actor=self.s["cam"].id)
        self.env.invalidate_all()
        self.assertEqual(v.contest_state, "open")
        self.assertFalse(v.superseded)
        # El controlador (el vendedor, en sudo) sigue funcionando.
        segunda = tx.with_user(self.s["u_lab"]).sudo().action_request_second_opinion(
            self.s["verif2"], actor=self.s["lab"])
        self.assertEqual(segunda.second_opinion_of_id, v)
