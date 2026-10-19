"""D-35 (auditoría de procesos): anular una salida.

* El motivo es obligatorio siempre.
* Sin DAE ni factura, quien la registró la anula y la cantidad vuelve al lote.
* Con DAE o factura, la anulación queda «pendiente de aprobación»: la salida
  sigue registrada (el stock no vuelve) hasta que un administrador de la
  plataforma la aprueba; puede también rechazarla con motivo.
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import CLAVE, crear_socios, producto


@freeze_time("2026-10-16 15:00:00")
@tagged("post_install", "-at_install")
class TestExportAnulacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "xan")
        cls.emp = env["res.partner"].create({
            "name": "Empacadora xan", "is_company": True, "email": "emp.xan@prueba.test",
            "vat_or_id": "0944400019001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora xan S.A.", "emp_capacidad_lb_dia": 50000})
        cls.libra = env.ref("shrimp_marketplace.uom_libra")
        Users = env["res.users"].with_context(no_reset_password=True)
        cls.admin = Users.create({
            "name": "Admin xan", "login": "admin.xan", "password": CLAVE,
            "email": "admin.xan@prueba.test",
            "group_ids": [(6, 0, [env.ref("shrimp_marketplace.group_marketplace_manager").id])]})
        cls.oper = Users.create({
            "name": "Operador xan", "login": "oper.xan", "password": CLAVE,
            "group_ids": [(6, 0, [env.ref("shrimp_marketplace.group_shrimp_user").id])]})

    def _lote(self, qty=1000.0):
        pond = self.env["shrimp.partner.pond"].create({"partner_id": self.s["cam"].id, "name": "P-xan"})
        cosecha = producto(
            self.env, self.s["cam"], nombre="Camarón xan",
            etapa="shrimp_marketplace.shrimp_stage_engorde", initial_qty=qty,
            uom_id=self.libra.id, presentation="entero",
            size_grade_id=self.env["shrimp.size.grade"].search(
                [("presentation", "=", "entero")], limit=1).id,
            origin_pond_id=pond.id, production_date=date.today(),
            expected_delivery_date=date.today())
        tx = cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.emp, qty)["transaction"]
        tx.action_receive()
        return tx.get_full_traceability_data()["own_lots"]

    def _salida(self, lote, **docs):
        return self.env["shrimp.export"].shrimp_register(
            self.emp, dict({"destination_country_id": self.env.ref("base.es").id}, **docs),
            [(lote, 400.0)])

    def test_motivo_obligatorio(self):
        lote = self._lote()
        exp = self._salida(lote)
        for motivo in (None, "", "   "):
            with self.assertRaisesRegex(ValidationError, "motivo"):
                exp.action_cancel(reason=motivo, actor=self.emp)
        self.assertEqual(exp.state, "registered")

    def test_sin_documentos_anula_directo(self):
        lote = self._lote()
        exp = self._salida(lote)
        self.assertAlmostEqual(lote.available_qty, 600.0)
        exp.action_cancel(reason="Error de captura", actor=self.emp)
        self.assertEqual(exp.state, "cancelled")
        self.assertFalse(exp.cancel_state)
        self.assertEqual(exp.cancel_reason, "Error de captura")
        self.assertAlmostEqual(lote.available_qty, 1000.0)

    def test_con_dae_queda_pendiente_y_aprueba_admin(self):
        lote = self._lote()
        exp = self._salida(lote, dae_number="028-2026-40-00999")
        exp.action_cancel(reason="El contenedor no embarcó", actor=self.emp)
        self.assertEqual(exp.state, "registered")
        self.assertEqual(exp.cancel_state, "pending")
        self.assertAlmostEqual(lote.available_qty, 600.0, msg="el stock no vuelve hasta aprobar")
        # Aviso a la plataforma: actividad para los administradores.
        self.assertIn(self.admin, exp.activity_ids.mapped("user_id"))
        # No se pide dos veces.
        with self.assertRaisesRegex(ValidationError, "pendiente|pedida"):
            exp.action_cancel(reason="otra vez", actor=self.emp)
        # Un operador no aprueba.
        with self.assertRaises(AccessError):
            exp.with_user(self.oper).action_approve_cancel()
        exp.with_user(self.admin).action_approve_cancel()
        self.assertEqual(exp.state, "cancelled")
        self.assertEqual(exp.cancel_state, "approved")
        self.assertEqual(exp.cancel_decided_uid, self.admin)
        self.assertAlmostEqual(lote.available_qty, 1000.0)
        self.assertFalse(exp.activity_ids)
        # El titular recibe el aviso.
        aviso = exp.message_ids.filtered(lambda m: "aprobó la anulación" in (m.body or ""))
        self.assertIn(self.emp, aviso.partner_ids)

    def test_con_factura_rechazo_mantiene_registrada(self):
        lote = self._lote()
        exp = self._salida(lote, invoice_number="001-001-000000123")
        exp.action_cancel(reason="Me equivoqué", actor=self.emp)
        self.assertEqual(exp.cancel_state, "pending")
        with self.assertRaisesRegex(ValidationError, "por qué"):
            exp.with_user(self.admin).action_reject_cancel(note="")
        exp.with_user(self.admin).action_reject_cancel(note="La factura ya está autorizada en el SRI")
        self.assertEqual(exp.state, "registered")
        self.assertEqual(exp.cancel_state, "rejected")
        self.assertAlmostEqual(lote.available_qty, 600.0)
        # Puede volver a pedirla con otro motivo.
        exp.action_cancel(reason="Nota de crédito emitida", actor=self.emp)
        self.assertEqual(exp.cancel_state, "pending")

    def test_admin_desde_backoffice_anula_directo(self):
        lote = self._lote()
        exp = self._salida(lote, dae_number="028-2026-40-00777")
        wiz = self.env["shrimp.export.cancel.wizard"].with_user(self.admin).create({
            "export_id": exp.id, "mode": "cancel", "reason": "Corrección de la plataforma"})
        wiz.action_confirm()
        self.assertEqual(exp.state, "cancelled")
        self.assertAlmostEqual(lote.available_qty, 1000.0)

    def test_operador_backoffice_queda_pendiente(self):
        lote = self._lote()
        exp = self._salida(lote, dae_number="028-2026-40-00888")
        exp.with_user(self.oper).action_cancel(reason="Pedido del cliente")
        self.assertEqual(exp.cancel_state, "pending")
        self.assertEqual(exp.state, "registered")
