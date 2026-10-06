"""Camarón adulto: verificación obligatoria por todos los caminos de compra."""
import base64
from datetime import date, timedelta

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import PDF_MIN, crear_socios


@tagged("post_install", "-at_install")
class TestCoherenciaPacker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "pc")
        Socio = env["res.partner"]
        cls.emp = Socio.create({
            "name": "Empacadora pc", "is_company": True, "email": "emp.pc@prueba.test",
            "vat_or_id": "0933300002001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora pc S.A.", "emp_capacidad_lb_dia": 50000})
        cls.verif = Socio.create({
            "name": "Verificadora pc", "is_company": True, "shrimp_user_type": "verificador",
            "vat_or_id": "0933300003001", "email": "verif.pc@prueba.test"})
        att = env["ir.attachment"].create({
            "name": "acred.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        env["shrimp.user.certificate.line"].create({
            "partner_id": cls.verif.id,
            "certificate_id": env.ref("shrimp_verification.cert_acreditacion_verificador").id,
            "file_attachment_id": att.id, "status": "approved", "certificate_number": "PC-1",
            "issue_date": date.today() - timedelta(days=30),
            "expiry_date": date.today() + timedelta(days=365)})
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")
        cls.engorde = env.ref("shrimp_marketplace.shrimp_stage_engorde")
        cls.adulto = env["shrimp.product"].create({
            "name": "Engorde pc", "seller_partner_id": cls.s["cam"].id,
            "seller_role": "camaronera", "stage_id": cls.engorde.id,
            "presentation": "entero", "size_grade_id": cls.talla.id,
            "initial_qty": 1000.0, "price": 2.5, "state": "published"})

    def test_matriz_empacadora(self):
        self.assertTrue(self.emp._shrimp_can("buy_from_camaronera"))
        self.assertFalse(self.s["lab"]._shrimp_can("buy_from_camaronera"))
        self.assertTrue(self.adulto.motivo_no_comprable(self.s["lab"]))
        self.assertFalse(self.adulto.motivo_no_comprable(self.emp))
        # El perfil común recibe los alias de la empacadora.
        self.assertEqual(self.emp.shrimp_razon_social, "Empacadora pc S.A.")
        self.assertEqual((self.emp.shrimp_capacity_value, self.emp.shrimp_capacity_unit),
                         (50000.0, "lb_day"))

    def test_adulto_no_se_compra_directo(self):
        self.assertTrue(self.adulto.requires_verification)
        with self.assertRaises(ValidationError):
            self.adulto.execute_purchase_flow(self.emp, 10.0)
        # Tampoco en sudo (como lo llama el controlador).
        with self.assertRaises(ValidationError):
            self.adulto.sudo().execute_purchase_flow(self.emp, 10.0)

    def test_adulto_no_se_compra_por_solicitud_de_chequeo(self):
        cr = self.env["shrimp.check.request"].create({
            "product_id": self.adulto.id, "seller_partner_id": self.s["cam"].id,
            "buyer_partner_id": self.emp.id, "qty": 10.0})
        with self.assertRaises(ValidationError):
            cr.action_approve()

    def test_adulto_con_verificacion(self):
        res = self.adulto.start_verified_purchase(self.emp, 10.0, self.verif, fee=20.0)
        tx = res["transaction"]
        self.assertEqual(tx.state, "pending_verification")
        self.assertEqual(tx.price_unit, self.adulto.price_for_partner(self.emp))

    # ------------------------------------------------------------------
    # Reserva anticipada: también con verificación
    # ------------------------------------------------------------------
    def _reserva_cumplida(self, libras_cosechadas=30000.0):
        env = self.env
        cam = self.s["cam"]
        pond = env["shrimp.partner.pond"].create({"partner_id": cam.id, "name": "P-pc"})
        esperada = date.today() + timedelta(days=20)
        declaracion = env["shrimp.harvest.forecast"].create({
            "farmer_partner_id": cam.id, "expected_date": esperada,
            "expected_lb": 30000.0, "presentation": "entero",
            "size_grade_id": self.talla.id, "pond_id": pond.id,
            "recipient_ids": [(6, 0, self.emp.ids)]})
        declaracion.action_publish(actor=cam)
        compromiso = env["shrimp.harvest.commitment"].create({
            "forecast_id": declaracion.id, "packer_partner_id": self.emp.id,
            "committed_lb": 30000.0, "price_mode": "fijo", "price_per_lb": 2.4,
            "step_delta_per_lb": 0.1, "valid_until": esperada - timedelta(days=1)})
        compromiso.action_accept(actor=cam)
        declaracion.action_registrar_cosecha(
            actual_lb=libras_cosechadas, actual_size_grade_id=self.talla.id,
            actual_date=esperada, actor=cam)
        return compromiso

    def test_reserva_exige_verificador(self):
        compromiso = self._reserva_cumplida()
        self.assertEqual(compromiso.state, "honored")
        self.assertEqual(compromiso.product_id.state, "published")
        with self.assertRaises(ValidationError):
            compromiso.action_comprar(actor=self.emp)
        tx = compromiso.action_comprar(actor=self.emp, verifier=self.verif)
        self.assertEqual(tx.state, "pending_verification")
        self.assertTrue(tx.needs_verification)
        self.assertEqual(tx.verification_ids.verifier_partner_id, self.verif)
        self.assertEqual(compromiso.transaction_id, tx)
        # El honorario de la verificación es el primer cobro de la plataforma.
        self.assertEqual(tx.verification_ids.fee_charge_id.payer_partner_id, self.emp)
        with self.assertRaises(ValidationError):
            compromiso.action_comprar(actor=self.emp, verifier=self.verif)

    def test_confirmacion_fuera_de_banda_cada_parte_la_suya(self):
        compromiso = self._reserva_cumplida(libras_cosechadas=5000.0)
        self.assertEqual(compromiso.state, "to_confirm")
        firma_emp = compromiso.confirmation_ids.filtered(lambda c: c.role == "packer")
        firma_cam = compromiso.confirmation_ids.filtered(lambda c: c.role == "farmer")
        with self.assertRaises(AccessError):
            firma_emp.action_accept(actor=self.s["cam"])
        with self.assertRaises(ValidationError):
            firma_cam.action_reject(motivo="", actor=self.s["cam"])
        firma_cam.action_accept(actor=self.s["cam"])
        with self.assertRaises(ValidationError):
            firma_cam.action_accept(actor=self.s["cam"])   # ya firmó
