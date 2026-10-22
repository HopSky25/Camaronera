"""Métodos públicos de las reservas de cosecha llamados por RPC.

/web/dataset/call_kw deja a cualquier usuario del portal llamar a un método
público sobre cualquier id, con los argumentos que quiera (el `actor` llega
como un número, nunca como un registro). Cada acción de las partes tiene que
comprobar sola que quien llama ES esa parte, con el permiso de subusuario de
su lado; el controlador y la API llaman en sudo y siguen funcionando.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_marketplace.tests.common import CLAVE

from .test_reserva_deshacer import cosecha_fuera_de_banda, montar_reserva


def _subusuario(env, empresa, login, permisos):
    contacto = env["res.partner"].sudo().create({
        "name": "Sub %s" % login, "email": "%s@prueba.test" % login,
        "parent_id": empresa.id, "type": "contact"})
    user = env["res.users"].sudo().create({
        "name": contacto.name, "login": login, "partner_id": contacto.id, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    env["shrimp.account.member"].sudo()._shrimp_ensure(user, empresa, permissions=permisos)
    return user


@tagged("post_install", "-at_install")
class TestRpcAuthzPacker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = montar_reserva(env, "rz", "0933391001001")
        cls.cam, cls.emp = cls.s["cam"], cls.s["emp"]
        cls.u_cam, cls.u_emp = cls.s["u_cam"], cls.s["u_emp"]
        # Ajeno a la reserva: otro usuario del portal (el laboratorio).
        cls.u_ajeno = cls.s["u_lab"]
        Member = env["shrimp.account.member"].sudo()
        Member._shrimp_ensure(cls.u_cam, cls.cam, is_admin=True)
        Member._shrimp_ensure(cls.u_emp, cls.emp, is_admin=True)
        # Subusuarios que solo VEN: no venden (camaronera) ni compran (empacadora).
        cls.u_cam_ver = _subusuario(env, cls.cam, "cam.ver.rz", {"camaronera": {"view"}})
        cls.u_emp_ver = _subusuario(env, cls.emp, "emp.ver.rz", {"empacadora": {"view"}})
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")

    def _declaracion(self, publicar=True):
        env = self.env
        pond = env["shrimp.partner.pond"].create({"partner_id": self.cam.id, "name": "P-rz"})
        esperada = fields.Date.today() + timedelta(days=20)
        d = env["shrimp.harvest.forecast"].create({
            "farmer_partner_id": self.cam.id, "expected_date": esperada,
            "expected_lb": 30000.0, "presentation": "entero",
            "size_grade_id": self.talla.id, "pond_id": pond.id,
            "recipient_ids": [(6, 0, self.emp.ids)]})
        if publicar:
            d.action_publish(actor=self.cam)
        return d

    def _compromiso(self, d):
        return self.env["shrimp.harvest.commitment"].create({
            "forecast_id": d.id, "packer_partner_id": self.emp.id,
            "committed_lb": 20000.0, "price_mode": "fijo", "price_per_lb": 2.4,
            "step_delta_per_lb": 0.1, "valid_until": d.expected_date - timedelta(days=1)})

    def _cumplido(self):
        d = self._declaracion()
        c = self._compromiso(d)
        c.action_accept(actor=self.cam)
        d.action_registrar_cosecha(actual_lb=30000.0, actual_size_grade_id=self.talla.id,
                                   actual_date=d.expected_date, actor=self.cam)
        self.assertEqual(c.state, "honored")
        return d, c

    # ------------------------------------------------------------------
    # La declaración (camaronera)
    # ------------------------------------------------------------------
    @mute_logger("odoo.models")
    def test_declaracion_ajeno_y_subusuario_sin_permiso(self):
        borrador = self._declaracion(publicar=False)
        publicada = self._declaracion()
        for user in (self.u_ajeno, self.u_emp):
            with self.assertRaises(AccessError):
                borrador.with_user(user).action_publish(actor=self.cam.id)
            with self.assertRaises(AccessError):
                publicada.with_user(user).action_cancel(motivo="x", actor=self.cam.id)
            with self.assertRaises(AccessError):
                publicada.with_user(user).action_registrar_cosecha(
                    20000.0, self.talla.id, actor=self.cam.id)
        # El subusuario de la camaronera que solo ve: el permiso de vender.
        with self.assertRaisesRegex(AccessError, "permiso"):
            publicada.with_user(self.u_cam_ver).action_cancel(motivo="x")
        with self.assertRaisesRegex(AccessError, "permiso"):
            publicada.with_user(self.u_cam_ver).action_registrar_cosecha(20000.0, self.talla.id)
        self.assertEqual(borrador.state, "draft")
        self.assertEqual(publicada.state, "published")
        self.assertFalse(publicada.product_id)

    def test_declaracion_camino_del_controlador(self):
        # El controlador y la API llaman en sudo con su actor: siguen igual.
        d = self._declaracion(publicar=False)
        d.with_user(self.u_cam).sudo().action_publish(actor=self.cam)
        self.assertEqual(d.state, "published")

    # ------------------------------------------------------------------
    # El compromiso (las dos partes)
    # ------------------------------------------------------------------
    @mute_logger("odoo.models")
    def test_compromiso_ajeno(self):
        d = self._declaracion()
        c = self._compromiso(d)
        ajeno = c.with_user(self.u_ajeno)
        with self.assertRaises(AccessError):
            ajeno.action_accept(actor=self.cam.id)
        with self.assertRaises(AccessError):
            ajeno.action_reject(motivo="x", actor=self.cam.id)
        with self.assertRaises(AccessError):
            ajeno.action_withdraw(actor=self.emp.id)
        with self.assertRaises(AccessError):
            ajeno.action_desistir(motivo="x", actor=self.emp.id)
        with self.assertRaises(AccessError):
            ajeno.action_comprar(actor=self.emp.id)
        # La empacadora no acepta su propio compromiso por la camaronera.
        with self.assertRaises(AccessError):
            c.with_user(self.u_emp).action_accept(actor=self.cam.id)
        self.assertEqual(c.state, "sent")

    @mute_logger("odoo.models")
    def test_compromiso_subusuario_sin_permiso(self):
        d = self._declaracion()
        c = self._compromiso(d)
        with self.assertRaisesRegex(AccessError, "permiso"):
            c.with_user(self.u_cam_ver).action_accept()
        with self.assertRaisesRegex(AccessError, "permiso"):
            c.with_user(self.u_cam_ver).action_reject(motivo="x")
        with self.assertRaisesRegex(AccessError, "permiso"):
            c.with_user(self.u_emp_ver).action_withdraw()
        c.action_accept(actor=self.cam)
        with self.assertRaisesRegex(AccessError, "permiso"):
            c.with_user(self.u_emp_ver).action_desistir(motivo="x")
        with self.assertRaisesRegex(AccessError, "permiso"):
            c.with_user(self.u_cam_ver).action_desistir(motivo="x")
        self.assertEqual(c.state, "accepted")

    @mute_logger("odoo.models")
    def test_comprar_reserva_vencida_por_un_ajeno(self):
        """Antes el plazo se miraba antes que quién compra: cualquiera hacía
        vencer (con avisos) la reserva de otra empacadora."""
        d, c = self._cumplido()
        self.assertEqual(c.reservation_state, "reserved")
        c.purchase_deadline = fields.Datetime.now() - timedelta(minutes=1)
        for user in (self.u_ajeno, self.u_cam):
            with self.assertRaises(AccessError):
                c.with_user(user).action_comprar(actor=self.emp.id)
        with self.assertRaisesRegex(AccessError, "permiso"):
            c.with_user(self.u_emp_ver).action_comprar()
        self.assertEqual(c.reservation_state, "reserved")
        self.assertEqual(c.state, "honored")

    def test_compromiso_camino_del_controlador(self):
        d = self._declaracion()
        c = self._compromiso(d)
        c.with_user(self.u_cam).sudo().action_accept(actor=self.cam)
        self.assertEqual(c.state, "accepted")
        c.with_user(self.u_emp).sudo().action_desistir(motivo="Sin cupo", actor=self.emp)
        self.assertEqual(c.state, "broken")
        self.assertEqual(c.break_side, "packer")

    # ------------------------------------------------------------------
    # La confirmación de la cosecha fuera de banda
    # ------------------------------------------------------------------
    @mute_logger("odoo.models")
    def test_confirmacion_ajeno_y_subusuario(self):
        cm = cosecha_fuera_de_banda(self.env, self.s, self.talla, "rz1")
        f_cam = cm.confirmation_ids.filtered(lambda c: c.role == "farmer")
        f_emp = cm.confirmation_ids.filtered(lambda c: c.role == "packer")
        for user in (self.u_ajeno, self.u_emp):
            with self.assertRaises(AccessError):
                f_cam.with_user(user).action_accept(actor=self.cam.id)
            with self.assertRaises(AccessError):
                f_cam.with_user(user).action_reject(motivo="x", actor=self.cam.id)
        with self.assertRaisesRegex(AccessError, "permiso"):
            f_cam.with_user(self.u_cam_ver).action_accept()
        with self.assertRaisesRegex(AccessError, "permiso"):
            f_emp.with_user(self.u_emp_ver).action_reject(motivo="x")
        self.assertEqual(f_cam.decision, "pending")
        self.assertEqual(f_emp.decision, "pending")
        # Deshacer: igual. La empacadora firma (como el controlador, en sudo)
        # y ni un ajeno ni su subusuario sin permiso se la deshacen.
        f_emp.with_user(self.u_emp).sudo().action_accept(actor=self.emp)
        self.assertEqual(f_emp.decision, "accepted")
        with self.assertRaises(AccessError):
            f_emp.with_user(self.u_ajeno).action_signoff_undo(actor=self.emp.id)
        with self.assertRaises(AccessError):
            f_emp.with_user(self.u_ajeno).action_signoff_undo(reason="x", on_behalf=True)
        with self.assertRaisesRegex(AccessError, "permiso"):
            f_emp.with_user(self.u_emp_ver).action_signoff_undo()
        self.assertEqual(f_emp.decision, "accepted")
        # El camino del controlador sigue funcionando.
        f_emp.with_user(self.u_emp).sudo().action_signoff_undo(actor=self.emp)
        self.assertEqual(f_emp.decision, "pending")

    # ------------------------------------------------------------------
    # Lecturas en sudo que no se exponen por RPC
    # ------------------------------------------------------------------
    def test_lecturas_privadas(self):
        Producto = self.env["shrimp.product"]
        for metodo in ("mejor_precio_hoy", "simulador_cosecha"):
            with self.assertRaises(AccessError):
                get_public_method(Producto, metodo)
        # Las acciones de las partes siguen siendo públicas (las usa la API).
        get_public_method(self.env["shrimp.harvest.commitment"], "action_comprar")
        get_public_method(self.env["shrimp.harvest.confirmation"], "action_signoff_undo")
