"""Deshacer la firma de una cosecha fuera de banda (firmas de dos partes).

* Con el compromiso «pendiente de confirmar», cada parte deshace la suya.
* Un «no la acepto» libera el compromiso en el acto (regla de negocio); se
  puede deshacer solo dentro de la ventana de gracia y si el lote sigue en
  borrador y sin compra.
* Con dos conformes el compromiso se cumple (lote publicado): ya no.
"""
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios, csrf_de


def montar_reserva(env, sufijo, ruc):
    s = crear_socios(env, sufijo)
    emp = env["res.partner"].create({
        "name": "Empacadora %s" % sufijo, "is_company": True,
        "email": "emp.%s@prueba.test" % sufijo,
        "vat_or_id": ruc, "shrimp_user_type": "empacadora",
        "emp_razon_social": "Empacadora %s S.A." % sufijo, "emp_capacidad_lb_dia": 50000})
    s["emp"] = emp
    s["u_emp"] = env["res.users"].create({
        "name": emp.name, "login": "emp." + sufijo, "partner_id": emp.id, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    return s


def cosecha_fuera_de_banda(env, s, talla, sufijo):
    cam, emp = s["cam"], s["emp"]
    pond = env["shrimp.partner.pond"].create({"partner_id": cam.id, "name": "P-%s" % sufijo})
    esperada = date.today() + timedelta(days=20)
    declaracion = env["shrimp.harvest.forecast"].create({
        "farmer_partner_id": cam.id, "expected_date": esperada,
        "expected_lb": 30000.0, "presentation": "entero",
        "size_grade_id": talla.id, "pond_id": pond.id,
        "recipient_ids": [(6, 0, emp.ids)]})
    declaracion.action_publish(actor=cam)
    compromiso = env["shrimp.harvest.commitment"].create({
        "forecast_id": declaracion.id, "packer_partner_id": emp.id,
        "committed_lb": 30000.0, "price_mode": "fijo", "price_per_lb": 2.4,
        "step_delta_per_lb": 0.1, "valid_until": esperada - timedelta(days=1)})
    compromiso.action_accept(actor=cam)
    declaracion.action_registrar_cosecha(
        actual_lb=5000.0, actual_size_grade_id=talla.id, actual_date=esperada, actor=cam)
    return compromiso


@tagged("post_install", "-at_install")
class TestReservaDeshacer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = montar_reserva(cls.env, "rd", "0933390001001")
        cls.talla = cls.env.ref("shrimp_marketplace.size_entero_3040")

    def setUp(self):
        super().setUp()
        self.cm = cosecha_fuera_de_banda(self.env, self.s, self.talla, "rd%s" % self._testMethodName[-6:])
        self.assertEqual(self.cm.state, "to_confirm")
        self.f_cam = self.cm.confirmation_ids.filtered(lambda c: c.role == "farmer")
        self.f_emp = self.cm.confirmation_ids.filtered(lambda c: c.role == "packer")

    def test_deshacer_aceptacion_pendiente_de_la_otra(self):
        self.f_cam.action_accept(actor=self.s["cam"])
        self.assertEqual(self.cm.state, "to_confirm")
        self.f_cam.action_signoff_undo(reason="Revisé mal", actor=self.s["cam"])
        self.assertEqual(self.f_cam.decision, "pending")
        self.assertEqual(self.cm.state, "to_confirm")
        eventos = self.env["shrimp.signoff.event"].history_for(self.cm)
        self.assertEqual(sorted(eventos.mapped("kind")), ["decision", "undo"])
        self.assertIn("Revisé mal", " ".join(self.cm.message_ids.mapped("body")))

    def test_deshacer_rechazo_en_la_ventana_de_gracia(self):
        self.f_emp.action_reject(motivo="Talla chica", actor=self.s["emp"])
        self.assertEqual(self.cm.state, "released")
        self.assertEqual(self.cm.product_id.state, "draft")
        self.assertTrue(self.f_emp.signoff_can_undo(actor=self.s["emp"]))
        self.f_emp.action_signoff_undo(actor=self.s["emp"])
        self.assertEqual(self.f_emp.decision, "pending")
        self.assertEqual(self.cm.state, "to_confirm")
        # Y la ronda sigue normal.
        self.f_emp.action_accept(actor=self.s["emp"])
        self.f_cam.action_accept(actor=self.s["cam"])
        self.assertEqual(self.cm.state, "honored")

    def test_no_se_deshace_el_rechazo_pasada_la_gracia(self):
        self.f_emp.action_reject(motivo="Talla chica", actor=self.s["emp"])
        self.f_emp.sudo().write({"decided_at": fields.Datetime.now() - timedelta(minutes=16)})
        with self.assertRaises(UserError):
            self.f_emp.action_signoff_undo(actor=self.s["emp"])
        self.assertEqual(self.cm.state, "released")
        # Con la ventana configurada más amplia, sí.
        self.env["ir.config_parameter"].sudo().set_param("shrimp.signoff_undo_minutes", "60")
        self.f_emp.action_signoff_undo(actor=self.s["emp"])
        self.assertEqual(self.cm.state, "to_confirm")

    def test_no_se_deshace_el_rechazo_si_el_lote_ya_se_publico(self):
        self.f_cam.action_reject(motivo="Me lo pagan mejor", actor=self.s["cam"])
        self.assertEqual(self.cm.state, "released")
        self.cm.product_id.sudo().write({"state": "published", "price": 2.0})
        with self.assertRaises(UserError):
            self.f_cam.action_signoff_undo(actor=self.s["cam"])
        self.assertEqual(self.cm.state, "released")

    def test_no_se_deshace_un_compromiso_cumplido(self):
        self.f_cam.action_accept(actor=self.s["cam"])
        self.f_emp.action_accept(actor=self.s["emp"])
        self.assertEqual(self.cm.state, "honored")
        for firma, socio in ((self.f_cam, self.s["cam"]), (self.f_emp, self.s["emp"])):
            with self.assertRaises(UserError):
                firma.action_signoff_undo(actor=socio)

    def test_no_se_deshace_la_ajena(self):
        self.f_cam.action_accept(actor=self.s["cam"])
        with self.assertRaises(AccessError):
            self.f_cam.action_signoff_undo(actor=self.s["emp"])
        self.assertEqual(self.f_cam.decision, "accepted")

    def test_aviso_a_la_otra_parte(self):
        self.f_cam.action_accept(actor=self.s["cam"])
        enviados = []

        def falso(registro, xmlid, email_to, ctx=None):
            enviados.append((email_to, (ctx or {}).get("portal_url")))
            return True

        with patch.object(type(self.env["shrimp.signoff.event"]), "_send_template", falso):
            self.f_cam.action_signoff_undo(actor=self.s["cam"])
        self.assertEqual(len(enviados), 1)
        self.assertEqual(enviados[0][0], self.s["emp"].email)
        self.assertIn("/packer/reservations/", enviados[0][1])


@tagged("post_install", "-at_install")
class TestReservaDeshacerPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = montar_reserva(cls.env, "rp", "0933390002001")
        cls.talla = cls.env.ref("shrimp_marketplace.size_entero_3040")
        cls.cm = cosecha_fuera_de_banda(cls.env, cls.s, cls.talla, "rp")

    def test_portal_deshacer(self):
        f_cam = self.cm.confirmation_ids.filtered(lambda c: c.role == "farmer")
        f_cam.action_accept(actor=self.s["cam"])
        self.env.flush_all()
        url = "/marketplace/reservations/confirmations/%s/undo" % f_cam.uuid_ref
        # La empacadora no puede deshacer la firma de la camaronera.
        self.authenticate(self.s["u_emp"].login, CLAVE)
        pagina = self.url_open("/packer/reservations/%s" % self.cm.forecast_id.uuid_ref).text
        self.assertNotIn(url, pagina)
        token = csrf_de(pagina)
        self.url_open(url, data={"csrf_token": token})
        f_cam.invalidate_recordset()
        self.assertEqual(f_cam.decision, "accepted")
        # La camaronera sí.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        pagina = self.url_open("/marketplace/reservations/%s" % self.cm.forecast_id.uuid_ref).text
        self.assertIn(url, pagina)
        self.assertIn("Historial de decisiones", pagina)
        r = self.url_open(url, data={"csrf_token": csrf_de(pagina), "motivo": "Error"},
                          allow_redirects=False)
        self.assertIn("deshecha", r.headers.get("Location", ""))
        f_cam.invalidate_recordset()
        self.assertEqual(f_cam.decision, "pending")
