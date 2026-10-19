"""Huecos #1 y #2 de la auditoría de procesos.

#1 La compra verificada se completa al aceptarse el informe (lote del
   comprador, recepción «verified»), el cron reintenta las que quedaron
   confirmadas y la migración cierra las atascadas sin correos.
#2 Plazos de la verificadora: recordatorio, vencimiento (aviso + escalamiento
   a la plataforma), cancelación sin costo o cambio de verificadora del
   comprador, cancelación automática, reasignar / cancelar por la plataforma,
   cancelación del vendedor antes de ir a campo, piso «desde el -u».
"""
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import producto

from ..models.shrimp_verification_deadline import DEADLINE_SINCE_PARAM
from .common_seguridad import abrir_ronda, montar_verificacion


class PlazosCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "cyp")
        cls.Tx = cls.env["shrimp.transaction"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        for clave, valor in (("deadline_assign_hours", "48"), ("deadline_verdict_days", "3"),
                             ("deadline_reminder_hours", "24"),
                             ("deadline_auto_cancel_days", "7")):
            cls.ICP.set_param("shrimp_verification.%s" % clave, valor)
        cls.ICP.set_param(DEADLINE_SINCE_PARAM, "2000-01-01")
        # Estos tests miden los plazos de asignar y dictaminar: el plazo de
        # respuesta de la verificadora (D-21, 19.0.1.24.0) se aleja para que
        # no se adelante; se prueba en test_decisiones_verificacion.
        cls.ICP.set_param("shrimp_verification.verifier_response_hours", "720")
        cls.hoy = cls.Tx._shrimp_today()

    def otra_compra(self, nombre="Larva cyp", fee=50.0):
        lote = producto(self.env, self.s["lab"], nombre=nombre,
                        expected_delivery_date=self.hoy)
        return lote.start_verified_purchase(self.s["cam"], 10.0, self.s["verif"], fee=fee)

    def cron(self, cuando):
        with freeze_time(cuando):
            self.env.invalidate_all()
            self.env["shrimp.verification"]._cron_vencer_plazos()
        self.env.invalidate_all()

    def msgs(self, rec, asunto):
        return rec.sudo().message_ids.filtered(lambda m: asunto in (m.subject or ""))


@tagged("post_install", "-at_install")
class TestCierreVerificada(PlazosCommon):

    def aceptar(self, v):
        abrir_ronda(v)
        v.acceptance_ids.filtered(lambda a: a.role == "buyer").sudo().action_accept(
            actor=self.s["cam"])
        v.acceptance_ids.filtered(lambda a: a.role == "seller").sudo().action_accept(
            actor=self.s["lab"])

    def test_aceptacion_completa_la_compra_y_crea_el_lote(self):
        tx = self.v.transaction_id
        self.aceptar(self.v)
        self.assertEqual(self.v.acceptance_state, "closed")
        self.assertEqual(tx.state, "done", "la verificación aceptada acredita la recepción")
        self.assertEqual(tx.reception_status, "verified")
        self.assertTrue(tx.received_date)
        lotes = self.env["shrimp.stock.lot"].sudo().search([
            ("origin_move_id", "in", tx.stock_move_ids.ids),
            ("owner_id", "=", self.s["cam"].id)])
        self.assertTrue(lotes, "el comprador recibe su lote")
        self.assertAlmostEqual(sum(lotes.mapped("initial_qty")), tx.transaction_qty)
        self.assertTrue(tx.charge_ids.filtered(lambda c: c.charge_type == "commission"),
                        "la comisión se registró al confirmar (paso 1)")
        self.assertTrue(self.msgs(tx, "completada"))

    def test_silencio_cierra_y_cron_reintenta(self):
        r = self.otra_compra("Larva silencio cyp")
        v, tx = r["verification"], r["transaction"]
        abrir_ronda(v)
        # Nadie responde: vence el plazo de aceptación y el cron cierra.
        self.cron(fields.Datetime.now() + timedelta(hours=49))
        self.assertEqual(tx.state, "done")
        # Una verificada «confirmada» con el informe aceptado (dato viejo) la
        # cierra el cron horario de entregas.
        r2 = self.otra_compra("Larva vieja cyp")
        v2, tx2 = r2["verification"], r2["transaction"]
        abrir_ronda(v2)
        v2.sudo().write({"acceptance_state": "closed"})
        tx2.sudo().action_confirm()
        self.assertEqual(tx2.state, "confirmed")
        self.Tx._cron_reception_and_delivery()
        self.assertEqual(tx2.state, "done")
        self.assertEqual(tx2.reception_status, "verified")

    def test_migracion_cierra_sin_correos(self):
        r = self.otra_compra("Larva migra cyp")
        v, tx = r["verification"], r["transaction"]
        abrir_ronda(v)
        v.sudo().write({"acceptance_state": "closed"})
        tx.sudo().action_confirm()
        antes = len(self.msgs(tx, "completada"))
        cerradas = self.Tx.search([("id", "=", tx.id)])._shrimp_close_verified_purchase(silent=True)
        self.assertEqual(cerradas, tx)
        self.assertEqual(tx.state, "done")
        self.assertEqual(len(self.msgs(tx, "completada")), antes, "sin correos a las partes")
        self.assertTrue(tx.message_ids.filtered(lambda m: "acreditada por la verificación"
                                                in (m.body or "")))


@tagged("post_install", "-at_install")
class TestPlazosVerificadora(PlazosCommon):

    def test_plazos_y_piso_desde_el_upgrade(self):
        v = self.v
        self.assertEqual(v.state, "received")
        self.assertAlmostEqual(
            (v.deadline_assign - v.assigned_date).total_seconds(), 48 * 3600, delta=5)
        self.assertFalse(v.deadline_overdue)
        # Orden vieja: con el piso del -u en el futuro, sus plazos cuentan desde ahí.
        manana = self.hoy + timedelta(days=1)
        self.ICP.set_param(DEADLINE_SINCE_PARAM, manana.isoformat())
        v.invalidate_recordset()
        self.assertGreater(v.deadline_assign, datetime.combine(manana, datetime.min.time()))
        self.assertGreaterEqual(v.deadline_verdict.date(), manana + timedelta(days=3))
        self.ICP.set_param(DEADLINE_SINCE_PARAM, "2000-01-01")

    def test_recordatorio_vencimiento_y_escalamiento(self):
        v, tx = self.v, self.v.transaction_id
        limite = v.deadline_assign
        self.cron(limite - timedelta(hours=30))
        self.assertFalse(self.msgs(v, "Plazo por vencer"))
        self.cron(limite - timedelta(hours=10))
        self.assertEqual(len(self.msgs(v, "Plazo por vencer")), 1)
        self.cron(limite - timedelta(hours=5))
        self.assertEqual(len(self.msgs(v, "Plazo por vencer")), 1, "sin duplicados")
        self.cron(limite + timedelta(hours=1))
        self.assertEqual(len(self.msgs(v, "Plazo vencido")), 1)
        self.assertTrue(self.msgs(tx, "está vencida"), "aviso al comprador")
        self.assertTrue(self.msgs(tx, "verificación vencida"), "aviso al vendedor")
        self.assertTrue(self.msgs(v, "Verificación vencida"), "escalamiento a la plataforma")
        self.cron(limite + timedelta(hours=3))
        self.assertEqual(len(self.msgs(v, "Plazo vencido")), 1, "sin duplicados")
        with freeze_time(limite + timedelta(hours=1)):
            v.invalidate_recordset()
            tx.invalidate_recordset()
            self.assertEqual(v.deadline_overdue, "assign")
            self.assertEqual(tx.shrimp_buyer_cancel_cause(), "verification_overdue")
        # Asignar técnico pasa al plazo del veredicto.
        v.sudo().action_assign_technician(self.s["tec"])
        with freeze_time(limite + timedelta(hours=1)):
            v.invalidate_recordset()
            self.assertFalse(v.deadline_overdue)

    def test_comprador_cancela_sin_costo_al_vencer(self):
        v, tx = self.v, self.v.transaction_id
        producto_v = tx.product_id
        reservado = producto_v.available_qty
        cobros = v.charge_ids.filtered(lambda c: c.charge_type == "verification_fee")
        # Antes de vencer: no puede.
        with self.assertRaises(ValidationError):
            tx.with_user(self.s["u_cam"]).sudo().shrimp_cancel("buyer", actor=self.s["cam"])
        with freeze_time(v.deadline_assign + timedelta(hours=1)):
            self.env.invalidate_all()
            # El vendedor no es quien cancela por la verificación vencida.
            with self.assertRaises(AccessError):
                tx.with_user(self.s["u_lab"]).sudo().shrimp_cancel("buyer", actor=self.s["lab"])
            reg = tx.with_user(self.s["u_cam"]).sudo().shrimp_cancel(
                "buyer", reason="La verificadora nunca vino", actor=self.s["cam"])
        self.env.invalidate_all()
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "cancelled")
        self.assertEqual(reg.kind, "buyer")
        self.assertEqual(reg.cause, "verification_overdue")
        self.assertFalse(reg.strike_id, "sin strike: la culpa es de la verificadora")
        self.assertFalse(self.env["shrimp.seller.strike"].sudo().search(
            [("transaction_id", "=", tx.id)]))
        self.assertTrue(all(c.state in ("cancelled", "to_credit", "credited") for c in cobros),
                        "honorario anulado")
        self.assertGreater(producto_v.available_qty, reservado, "se libera la reserva")

    def test_comprador_cambia_de_verificadora(self):
        r = self.otra_compra("Larva cambio cyp")
        v, tx = r["verification"], r["transaction"]
        cobro = v.fee_charge_id
        with self.assertRaises(ValidationError):
            tx.with_user(self.s["u_cam"]).sudo().action_switch_verifier(
                self.s["verif2"], actor=self.s["cam"])
        v.sudo().action_assign_technician(self.s["tec"])
        with freeze_time(v.deadline_verdict + timedelta(hours=1)):
            self.env.invalidate_all()
            self.assertEqual(v.deadline_overdue, "verdict")
            tx.with_user(self.s["u_cam"]).sudo().action_switch_verifier(
                self.s["verif2"], actor=self.s["cam"])
            self.env.invalidate_all()
            self.assertEqual(v.verifier_partner_id, self.s["verif2"])
            self.assertEqual(v.state, "received")
            self.assertFalse(v.technician_partner_id)
            self.assertFalse(v.deadline_overdue, "plazos nuevos desde ahora")
        # D-40: el honorario se recalcula con la tarifa de la nueva
        # verificadora; el cobro anterior se anula y se emite uno nuevo.
        esperado = self.env["shrimp.verification.fee"].compute(tx.transaction_qty, self.s["verif2"])
        self.assertEqual(v.fee, esperado)
        if esperado != 50.0:
            self.assertIn(cobro.state, ("cancelled", "to_credit", "credited"))
            self.assertNotEqual(v.fee_charge_id, cobro)
            self.assertEqual(v.fee_charge_id.amount, esperado)
        else:
            self.assertEqual(v.fee_charge_id, cobro, "mismo honorario")
        self.assertTrue(self.msgs(tx, "Nueva verificadora"))
        # Solo una verificadora acreditada y que no sea parte.
        with self.assertRaises(ValidationError):
            v._shrimp_reassign_verifier(self.s["lab"], "x", "plataforma")

    def test_cancelacion_automatica(self):
        r = self.otra_compra("Larva auto cyp")
        v, tx = r["verification"], r["transaction"]
        limite = v.deadline_assign
        self.cron(limite + timedelta(hours=1))
        self.assertEqual(tx.state, "pending_verification")
        self.cron(limite + timedelta(days=6))
        self.assertEqual(tx.state, "pending_verification")
        self.cron(limite + timedelta(days=7, hours=1))
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "cancelled")
        reg = tx.shrimp_cancellation()
        self.assertEqual((reg.kind, reg.cause), ("system", "verification_overdue"))
        # 0 = nunca.
        self.ICP.set_param("shrimp_verification.deadline_auto_cancel_days", "0")
        r2 = self.otra_compra("Larva nunca cyp")
        self.cron(r2["verification"].deadline_assign + timedelta(days=30))
        self.assertEqual(r2["transaction"].state, "pending_verification")
        self.ICP.set_param("shrimp_verification.deadline_auto_cancel_days", "7")

    def test_plataforma_reasigna_y_cancela(self):
        r = self.otra_compra("Larva plataforma cyp")
        v, tx = r["verification"], r["transaction"]
        with self.assertRaises(AccessError):
            v.with_user(self.s["u_cam"]).action_open_reassign_wizard()
        wiz = self.env["shrimp.verification.reassign.wizard"].create({
            "verification_id": v.id, "verifier_partner_id": self.s["verif2"].id,
            "reason": "Sin capacidad"})
        wiz.action_confirm()
        self.assertEqual(v.verifier_partner_id, self.s["verif2"])
        accion = v.action_open_cancel_purchase_wizard()
        self.assertEqual(accion["res_model"], "shrimp.transaction.cancel.wizard")
        cw = self.env["shrimp.transaction.cancel.wizard"].with_context(accion["context"]).create(
            {"reason": "Fraude detectado"})
        cw.action_confirm()
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "cancelled")
        self.assertEqual(tx.shrimp_cancellation().kind, "platform")

    def test_vendedor_cancela_antes_de_campo(self):
        r = self.otra_compra("Larva vendedor cyp")
        v, tx = r["verification"], r["transaction"]
        vend = tx.with_user(self.s["u_lab"]).sudo()
        with self.assertRaises(ValidationError):
            vend.shrimp_cancel("seller", reason="", actor=self.s["lab"])  # sin motivo
        reg = vend.shrimp_cancel("seller", reason="Mortalidad en la piscina", actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "cancelled")
        self.assertTrue(reg.strike_id)
        self.assertEqual(reg.strike_id.reason, "seller_cancel")
        self.assertTrue(all(c.state in ("cancelled", "to_credit", "credited")
                            for c in v.charge_ids), "el comprador no paga la verificación")
        # Ya en campo: el vendedor no puede.
        r2 = self.otra_compra("Larva campo cyp")
        r2["verification"].sudo().action_assign_technician(self.s["tec"])
        r2["verification"].sudo().action_start_field()
        self.assertTrue(r2["transaction"].shrimp_seller_cancel_problem())
        with self.assertRaises(ValidationError):
            r2["transaction"].with_user(self.s["u_lab"]).sudo().shrimp_cancel(
                "seller", reason="x", actor=self.s["lab"])

    def test_mutuo_acuerdo_en_verificacion(self):
        r = self.otra_compra("Larva mutuo cyp")
        tx = r["transaction"]
        tx.with_user(self.s["u_cam"]).sudo().action_cancel_request(
            "Cambiamos de plan", actor=self.s["cam"])
        with self.assertRaises(ValidationError):
            tx.with_user(self.s["u_cam"]).sudo().action_cancel_request_respond(
                True, actor=self.s["cam"])
        tx.with_user(self.s["u_lab"]).sudo().action_cancel_request_respond(
            True, actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        reg = tx.shrimp_cancellation()
        self.assertEqual(reg.kind, "mutual")
        self.assertFalse(reg.strike_id)
        self.assertEqual(r["verification"].state, "cancelled")
