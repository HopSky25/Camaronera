"""La reserva cumplida queda reservada (auditoría de procesos, hueco #8).

* Lote exclusivo de la empacadora comprometida por N días (Ajustes): las
  competidoras no pueden comprarlo ni reciben aviso.
* Comprar cierra la reserva («Comprada»).
* Vencer el plazo sin compra: incumplimiento de la empacadora, lote libre,
  re-publicado para avisos y aviso a las descartadas.
* Declaración vencida con compromiso aceptado: incumplimiento de la
  camaronera; la plataforma puede anularlo.
* Compromiso aceptado roto: las descartadas vuelven a «enviado».
"""
import base64
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import PDF_MIN, crear_socios
from odoo.addons.shrimp_packer.models.shrimp_lot_alert import ShrimpLotAlert


@tagged("post_install", "-at_install")
class TestReservaPlazo(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "rp")
        cls.cam = cls.s["cam"]
        Socio = env["res.partner"]

        def empacadora(n, ruc):
            return Socio.create({
                "name": "Empacadora rp%s" % n, "is_company": True,
                "email": "emp%s.rp@prueba.test" % n, "vat_or_id": ruc,
                "shrimp_user_type": "empacadora",
                "emp_razon_social": "Empacadora rp%s S.A." % n, "emp_capacidad_lb_dia": 50000})

        cls.emp = empacadora(1, "0933377001001")
        cls.emp2 = empacadora(2, "0933377002001")
        cls.verif = Socio.create({
            "name": "Verificadora rp", "is_company": True, "shrimp_user_type": "verificador",
            "vat_or_id": "0933377003001", "email": "verif.rp@prueba.test"})
        att = env["ir.attachment"].create({
            "name": "acred.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        env["shrimp.user.certificate.line"].create({
            "partner_id": cls.verif.id,
            "certificate_id": env.ref("shrimp_verification.cert_acreditacion_verificador").id,
            "file_attachment_id": att.id, "status": "approved", "certificate_number": "RP-1",
            "issue_date": date.today() - timedelta(days=30),
            "expiry_date": date.today() + timedelta(days=365)})
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")

    def _declaracion(self, dias=20):
        env = self.env
        pond = env["shrimp.partner.pond"].create({"partner_id": self.cam.id, "name": "P-rp"})
        esperada = date.today() + timedelta(days=dias)
        d = env["shrimp.harvest.forecast"].create({
            "farmer_partner_id": self.cam.id, "expected_date": esperada,
            "expected_lb": 30000.0, "presentation": "entero",
            "size_grade_id": self.talla.id, "pond_id": pond.id,
            "recipient_ids": [(6, 0, (self.emp | self.emp2).ids)]})
        d.action_publish(actor=self.cam)
        Commitment = env["shrimp.harvest.commitment"]
        base = {"forecast_id": d.id, "committed_lb": 20000.0, "price_mode": "fijo",
                "price_per_lb": 2.4, "step_delta_per_lb": 0.1,
                "valid_until": esperada - timedelta(days=1)}
        c1 = Commitment.create(dict(base, packer_partner_id=self.emp.id))
        c2 = Commitment.create(dict(base, packer_partner_id=self.emp2.id))
        c1.action_accept(actor=self.cam)
        self.assertEqual(c2.state, "rejected")
        return d, c1, c2

    def _cumplida(self):
        d, c1, c2 = self._declaracion()
        lote = d.action_registrar_cosecha(
            actual_lb=30000.0, actual_size_grade_id=self.talla.id,
            actual_date=d.expected_date, actor=self.cam)
        self.assertEqual(c1.state, "honored")
        return d, c1, c2, lote

    # ------------------------------------------------------------------
    def test_lote_reservado_en_exclusiva(self):
        antes = fields.Datetime.now()
        d, c1, c2, lote = self._cumplida()
        self.assertEqual(lote.state, "published")
        self.assertEqual(c1.reservation_state, "reserved")
        self.assertTrue(antes + timedelta(days=3) - timedelta(minutes=1)
                        <= c1.purchase_deadline <= fields.Datetime.now() + timedelta(days=3))
        self.assertEqual(lote.shrimp_reserved_for_id, self.emp)
        self.assertIn("reservado", lote.motivo_no_comprable(self.emp2))
        self.assertFalse(lote.motivo_no_comprable(self.emp))
        # Aviso a las dos partes.
        aviso = c1.message_ids.filtered(lambda m: "reservado en exclusiva" in (m.body or ""))
        self.assertTrue(aviso)
        self.assertTrue({self.emp.id, self.cam.id} <= set(aviso.partner_ids.ids))
        # No se puede archivar ni eliminar mientras está reservado.
        with self.assertRaises(UserError):
            lote.sudo().write({"active": False, "state": "draft"})

    def test_avisos_no_van_a_competidoras(self):
        d, c1, c2, lote = self._cumplida()
        vistos = {}

        def falso(this, packer, lotes, *a, **k):
            vistos[packer.id] = lotes
            return []

        Alert = self.env["shrimp.lot.alert"]
        with patch.object(ShrimpLotAlert, "_digest_para", falso):
            Alert._digest_para(self.emp2, lote, {}, {}, fields.Datetime.now(), date.today())
            Alert._digest_para(self.emp, lote, {}, {}, fields.Datetime.now(), date.today())
        self.assertFalse(vistos[self.emp2.id])
        self.assertEqual(vistos[self.emp.id], lote)

    def test_plazo_configurable(self):
        self.env["ir.config_parameter"].sudo().set_param("shrimp_packer.reserva_purchase_days", "7")
        d, c1, c2, lote = self._cumplida()
        self.assertGreater(c1.purchase_deadline, fields.Datetime.now() + timedelta(days=6))

    def test_comprar_cierra_la_reserva(self):
        d, c1, c2, lote = self._cumplida()
        c1.action_comprar(actor=self.emp, verifier=self.verif)
        self.assertEqual(c1.reservation_state, "bought")
        lote.invalidate_recordset()
        self.assertFalse(lote.shrimp_reserved_for_id)
        self.assertFalse(lote.motivo_no_comprable(self.emp2))

    def test_plazo_vencido_incumple_la_empacadora_y_libera(self):
        d, c1, c2, lote = self._cumplida()
        c1.purchase_deadline = fields.Datetime.now() - timedelta(minutes=1)
        lote.published_date = fields.Datetime.now() - timedelta(days=5)
        lote.invalidate_recordset()
        self.assertFalse(lote.shrimp_reserved_for_id)  # sin esperar al cron
        self.assertEqual(self.env["shrimp.harvest.commitment"]._cron_reserva_plazo(), 1)
        self.assertEqual(c1.state, "broken")
        self.assertEqual(c1.break_side, "packer")
        self.assertEqual(c1.broken_by_partner_id, self.emp)
        self.assertEqual(c1.reservation_state, "lapsed")
        self.assertEqual(self.emp.reserva_incumplidas, 1)
        self.assertFalse(lote.motivo_no_comprable(self.emp2))
        self.assertGreater(lote.published_date, fields.Datetime.now() - timedelta(minutes=5))
        aviso = c1.message_ids.filtered(lambda m: "Venció la reserva" in (m.subject or ""))
        self.assertIn(self.emp2.id, aviso.partner_ids.ids)
        # Ya no se puede comprar como reserva.
        with self.assertRaises(Exception):
            c1.action_comprar(actor=self.emp, verifier=self.verif)

    def test_compra_parcial_vence_sin_incumplimiento(self):
        d, c1, c2, lote = self._cumplida()
        lote.sudo().start_verified_purchase(self.emp, 1000.0, self.verif, fee=10.0)
        c1.purchase_deadline = fields.Datetime.now() - timedelta(minutes=1)
        self.env["shrimp.harvest.commitment"]._cron_reserva_plazo()
        self.assertEqual(c1.state, "honored")
        self.assertEqual(c1.reservation_state, "lapsed")

    def test_declaracion_vencida_incumple_la_camaronera(self):
        d, c1, c2 = self._declaracion()
        # La fecha pasa (sin la guarda de edición de una declaración publicada).
        self.env.cr.execute("UPDATE shrimp_harvest_forecast SET expected_date = %s WHERE id = %s",
                            (date.today() - timedelta(days=30), d.id))
        d.invalidate_recordset()
        self.env["shrimp.harvest.forecast"]._cron_vencer()
        self.assertEqual(d.state, "expired")
        self.assertEqual(c1.state, "broken")
        self.assertEqual(c1.break_side, "farmer")
        self.assertEqual(c1.broken_by_partner_id, self.cam)
        self.assertEqual(self.cam.reserva_incumplidas, 1)
        aviso = c1.message_ids.filtered(lambda m: "sin registrar" in (m.subject or ""))
        self.assertTrue({self.emp.id, self.cam.id} <= set(aviso.partner_ids.ids))
        # La plataforma lo anula con causa justificada.
        with self.assertRaises(UserError):
            c1.action_anular_incumplimiento("")
        wiz = self.env["shrimp.harvest.breach.annul"].create({
            "commitment_id": c1.id, "justification": "Mortalidad documentada por WSSV"})
        wiz.action_confirm()
        self.assertEqual(c1.state, "lapsed")
        self.cam.invalidate_recordset()
        self.assertEqual(self.cam.reserva_incumplidas, 0)

    def test_compromiso_roto_reabre_descartadas(self):
        d, c1, c2 = self._declaracion()
        c1.action_desistir(motivo="Sin cupo de exportación", actor=self.emp)
        self.assertEqual(d.state, "published")
        self.assertEqual(c2.state, "sent")
        aviso = c2.message_ids.filtered(lambda m: "vuelve a estar disponible" in (m.subject or ""))
        self.assertIn(self.emp2.id, aviso.partner_ids.ids)
        c2.action_accept(actor=self.cam)
        self.assertEqual(c2.state, "accepted")

    def test_cancelar_declaracion_no_reabre_descartadas(self):
        d, c1, c2 = self._declaracion()
        d.action_cancel(motivo="Mortalidad", actor=self.cam)
        self.assertEqual(c1.state, "broken")
        self.assertEqual(c2.state, "rejected")
        self.assertFalse(c2.message_ids.filtered(
            lambda m: "vuelve a estar disponible" in (m.subject or "")))
