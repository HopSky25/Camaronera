"""Decisiones de la auditoría de procesos (verificación), 19.0.1.24.0.

D-21 la verificadora acepta o declina la orden (y el comprador elige otra).
D-23 acreditación que vence: termina lo abierto, no recibe nuevas; avisos.
D-24 liquidación a la verificadora al cerrarse la ronda; reintento.
D-38 una sola contraoferta, sin reiniciar el plazo.
D-22 silencio (o rechazo) del vendedor ante la contraoferta: precio original.
D-16 veredicto sin campo solo por «producto no presentado» con evidencia.
D-25 segunda opinión pedida por el vendedor dentro del plazo.
D-41 plazo de la verificación declarada y desistimiento del vendedor.
"""
import base64
from datetime import timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import producto

from ..models.shrimp_verification_declared_deadline import DECISIONS_SINCE_PARAM
from ..models.shrimp_verification_deadline import DEADLINE_SINCE_PARAM
from .common_seguridad import abrir_ronda, montar_verificacion

# PNG 1x1.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class DecisionesCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "dvd")
        cls.Tx = cls.env["shrimp.transaction"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        for clave, valor in (("deadline_assign_hours", "48"), ("deadline_verdict_days", "3"),
                             ("deadline_reminder_hours", "24"), ("deadline_auto_cancel_days", "7"),
                             ("verifier_response_hours", "24"), ("contest_hours", "24"),
                             ("declared_days", "5"),
                             ("acceptance_hours", "48")):
            cls.ICP.set_param("shrimp_verification.%s" % clave, valor)
        cls.ICP.set_param(DEADLINE_SINCE_PARAM, "2000-01-01")
        cls.ICP.set_param(DECISIONS_SINCE_PARAM, "2000-01-01")
        cls.hoy = cls.Tx._shrimp_today()

    def compra(self, nombre, fee=50.0, mode="platform"):
        lote = producto(self.env, self.s["lab"], nombre=nombre, expected_delivery_date=self.hoy)
        if mode == "declared":
            return lote.start_verified_purchase(self.s["cam"], 10.0, None, fee=0.0, mode="declared")
        return lote.start_verified_purchase(self.s["cam"], 10.0, self.s["verif"], fee=fee)

    def cron(self, cuando):
        with freeze_time(cuando):
            self.env.invalidate_all()
            self.env["shrimp.verification"]._cron_vencer_plazos()
        self.env.invalidate_all()

    def en_campo(self, v, cumple=True):
        """Técnico asignado, en campo y con el informe de larva cargado."""
        v.sudo().action_assign_technician(self.s["tec"])
        v.sudo().action_start_field()
        v.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                        "larvae_avg_size_mg": 5.0,
                        "larvae_health_status": "good" if cumple else "rejected"})
        return v

    def postura(self, v, rol):
        return v.acceptance_ids.filtered(lambda a: a.role == rol).sudo()

    def strikes(self, tx):
        return self.env["shrimp.seller.strike"].sudo().search([("transaction_id", "=", tx.id)])


# ======================================================================
# D-21
# ======================================================================
@tagged("post_install", "-at_install")
class TestRespuestaVerificadora(DecisionesCommon):

    def test_nace_por_responder_y_asignar_acepta(self):
        r = self.compra("Larva acepta dvd")
        v = r["verification"]
        self.assertEqual(v.verifier_response, "pending")
        self.assertAlmostEqual((v.verifier_response_deadline - v.assigned_date).total_seconds(),
                               24 * 3600, delta=5)
        v.sudo().action_assign_technician(self.s["tec"])
        self.assertEqual(v.verifier_response, "accepted")

    def test_declina_y_el_comprador_elige_otra(self):
        r = self.compra("Larva declina dvd")
        v, tx = r["verification"], r["transaction"]
        cobro = v.fee_charge_id
        with self.assertRaises(ValidationError):
            v.action_verifier_decline("")  # motivo obligatorio
        v.action_verifier_decline("Sin capacidad esa semana")
        self.env.invalidate_all()
        self.assertEqual(v.verifier_response, "declined")
        self.assertEqual(v.deadline_overdue, "declined")
        self.assertEqual(tx.shrimp_buyer_cancel_cause(), "verification_overdue")
        self.assertTrue(tx.message_ids.filtered(lambda m: "Elige otra verificadora" in (m.subject or "")))
        with self.assertRaises(UserError):
            v.sudo().action_assign_technician(self.s["tec"])
        tx.with_user(self.s["u_cam"]).sudo().action_switch_verifier(
            self.s["verif2"], actor=self.s["cam"])
        self.env.invalidate_all()
        self.assertEqual(v.verifier_partner_id, self.s["verif2"])
        self.assertEqual(v.verifier_response, "pending", "la nueva también responde")
        self.assertFalse(v.deadline_overdue)
        esperado = self.env["shrimp.verification.fee"].compute(tx.transaction_qty, self.s["verif2"])
        self.assertEqual(v.fee, esperado, "honorario con la tarifa de la nueva (D-40)")
        if esperado != 50.0:
            self.assertNotEqual(v.fee_charge_id, cobro)

    def test_fuera_de_plazo_no_declina_y_silencio_cuenta_como_declinada(self):
        r = self.compra("Larva calla dvd")
        v, tx = r["verification"], r["transaction"]
        limite = v.verifier_response_deadline
        with freeze_time(limite + timedelta(hours=1)):
            self.env.invalidate_all()
            self.assertTrue(v.shrimp_can_decline())
            with self.assertRaises(ValidationError):
                v.action_verifier_decline("tarde")
            self.assertEqual(v.deadline_overdue, "response")
        self.cron(limite + timedelta(hours=1))
        self.assertTrue(tx.message_ids.filtered(lambda m: "Elige otra verificadora" in (m.subject or "")))
        avisos = len(tx.message_ids)
        self.cron(limite + timedelta(hours=2))
        self.assertEqual(len(tx.message_ids), avisos, "sin avisos duplicados")
        # Nadie elige: se cancela sola a los 7 días, sin strike.
        self.cron(limite + timedelta(days=7, hours=2))
        self.assertEqual(tx.state, "cancel")
        self.assertFalse(self.strikes(tx))

    def test_solo_el_admin_responde(self):
        r = self.compra("Larva admin dvd")
        v = r["verification"]
        with self.assertRaises(AccessError):
            v.with_user(self.s["u_tec"]).sudo()._shrimp_check_response_admin(self.s["tec"])
        self.assertTrue(v.with_user(self.s["u_verif"]).sudo()._shrimp_check_response_admin(
            self.s["verif"]))
        v.action_verifier_accept()
        self.assertEqual(v.verifier_response, "accepted")


# ======================================================================
# D-23
# ======================================================================
@tagged("post_install", "-at_install")
class TestAcreditacionVencida(DecisionesCommon):

    def linea(self):
        return self.s["verif"].certificate_line_ids.filtered(
            lambda c: c.certificate_id.role == "verificador")[:1]

    def test_avisos_30_dias_y_al_vencer(self):
        linea = self.linea()
        Line = self.env["shrimp.user.certificate.line"]
        hoy = fields.Date.context_today(Line)
        linea.sudo().write({"expiry_date": hoy + timedelta(days=40)})
        Line._cron_verifier_accreditation_expiry()
        self.assertFalse(linea.shrimp_expiry_notice)
        linea.sudo().write({"expiry_date": hoy + timedelta(days=20)})
        Line._cron_verifier_accreditation_expiry()
        self.assertEqual(linea.shrimp_expiry_notice, "soon")
        msgs = self.s["verif"].message_ids.filtered(lambda m: "vence el" in (m.subject or ""))
        self.assertTrue(msgs)
        self.assertTrue(msgs.filtered(lambda m: self.s["verif"] in m.partner_ids),
                        "aviso a la verificadora")
        Line._cron_verifier_accreditation_expiry()
        self.assertEqual(len(self.s["verif"].message_ids.filtered(
            lambda m: "vence el" in (m.subject or ""))), len(msgs), "sin duplicados")
        with freeze_time(fields.Datetime.now() + timedelta(days=21)):
            self.env.invalidate_all()
            Line._cron_verifier_accreditation_expiry()
            self.assertEqual(linea.shrimp_expiry_notice, "expired")
            self.assertTrue(self.s["verif"].message_ids.filtered(
                lambda m: "venció" in (m.subject or "")))
            # Sin acreditación: no recibe órdenes nuevas...
            self.assertFalse(self.s["verif"].verifier_is_accredited)
            self.assertNotIn(self.s["verif"], self.env["res.partner"].available_verifiers())
            lote = producto(self.env, self.s["lab"], nombre="Larva vencida dvd")
            with self.assertRaises(ValidationError):
                lote.start_verified_purchase(self.s["cam"], 10.0, self.s["verif"], fee=50.0)
            # ...pero termina la que tenía abierta.
            v = self.en_campo(self.v)
            v.sudo()._close("approved")
            self.assertEqual(v.state, "approved")
        # Renovación: los avisos vuelven a empezar.
        linea.sudo().write({"expiry_date": hoy + timedelta(days=15)})
        Line._cron_verifier_accreditation_expiry()
        self.assertEqual(linea.shrimp_expiry_notice, "soon")


# ======================================================================
# D-24
# ======================================================================
@tagged("post_install", "-at_install")
class TestLiquidacionVerificadora(DecisionesCommon):

    def test_se_liquida_al_cerrar_la_ronda_no_con_el_veredicto(self):
        r = self.compra("Larva liquida dvd")
        v = self.en_campo(r["verification"])
        v.sudo()._close("approved")
        self.assertEqual(v.payout_state, "none", "el veredicto ya no liquida")
        self.assertFalse(v.vendor_bill_id)
        self.assertIn("ronda", v.shrimp_payout_label())
        self.postura(v, "buyer").action_accept(actor=self.s["cam"])
        self.postura(v, "seller").action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "closed")
        self.assertEqual(v.payout_state, "billed")
        self.assertTrue(v.vendor_bill_id)
        self.assertEqual(v.vendor_bill_id.move_type, "in_invoice")
        self.assertEqual(v.vendor_bill_id.partner_id, self.s["verif"])
        self.assertAlmostEqual(v.vendor_bill_id.amount_untaxed, v.verifier_amount, places=2)
        # La liquidación va en la misma compañía que el cobro del honorario.
        honorario = v.charge_ids.filtered(lambda c: c.charge_type == "verification_fee")[:1]
        self.assertTrue(honorario)
        self.assertEqual(v.vendor_bill_id.company_id, honorario.company_id)
        self.assertIn(v, self.env["shrimp.verification"].shrimp_payouts_for(self.s["verif"]))
        self.assertTrue(v.shrimp_payout_label().startswith("Liquidada"))

    def test_trato_caido_tambien_liquida(self):
        r = self.compra("Larva caida dvd")
        v = self.en_campo(r["verification"])
        v.sudo()._close("approved")
        self.postura(v, "buyer").action_reject(reason="No me sirve", actor=self.s["cam"])
        self.postura(v, "seller").action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "broken")
        self.assertEqual(v.payout_state, "billed")

    def test_fallo_y_reintento_del_cron(self):
        r = self.compra("Larva falla dvd")
        v = self.en_campo(r["verification"])
        v.sudo()._close("approved")
        Charge = type(self.env["shrimp.charge"])
        with patch.object(Charge, "_create_vendor_bill", side_effect=UserError("Sin diario")):
            self.postura(v, "buyer").action_accept(actor=self.s["cam"])
            self.postura(v, "seller").action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "closed", "un fallo no tumba el cierre")
        self.assertEqual(v.payout_state, "error")
        self.assertEqual(v.payout_attempts, 1)
        self.assertIn("Sin diario", v.payout_error)
        self.env["shrimp.verification"]._cron_verifier_payouts()
        self.assertEqual(v.payout_state, "billed")
        self.assertTrue(v.vendor_bill_id)


# ======================================================================
# D-38 y D-22
# ======================================================================
@tagged("post_install", "-at_install")
class TestContraoferta(DecisionesCommon):

    def ronda_no_cumple(self, nombre):
        r = self.compra(nombre)
        v = abrir_ronda(r["verification"], cumple=False)
        return v, r["transaction"]

    def test_una_sola_contraoferta_y_sin_reiniciar_el_plazo(self):
        v, tx = self.ronda_no_cumple("Larva una dvd")
        plazo = v.acceptance_deadline
        comprador = self.postura(v, "buyer")
        with freeze_time(fields.Datetime.now() + timedelta(hours=10)):
            comprador.action_counter(tx.price_unit * 0.8, actor=self.s["cam"])
        self.assertEqual(v.acceptance_deadline, plazo, "D-38: no reinicia el plazo")
        comprador.action_signoff_undo(reason="me equivoqué", actor=self.s["cam"])
        self.assertEqual(comprador.decision, "pending")
        self.assertFalse(v.shrimp_counter_allowed())
        with self.assertRaises(UserError):
            comprador.action_counter(tx.price_unit * 0.7, actor=self.s["cam"])

    def test_silencio_del_vendedor_cierra_al_precio_original(self):
        v, tx = self.ronda_no_cumple("Larva calla dvd")
        precio = tx.price_unit
        self.postura(v, "buyer").action_counter(precio * 0.8, actor=self.s["cam"])
        self.cron(v.acceptance_deadline - timedelta(minutes=5))
        self.assertEqual(v.acceptance_state, "waiting")
        self.cron(v.acceptance_deadline + timedelta(minutes=5))
        self.assertEqual(v.counter_outcome, "expired")
        self.assertEqual(v.acceptance_state, "closed")
        self.assertEqual(tx.price_unit, precio, "al precio original")
        self.assertIn(tx.state, ("confirmed", "done"))
        self.assertEqual(v.fee_payer_partner_id, self.s["cam"])
        self.assertFalse(self.strikes(tx), "sin strike")

    def test_rechazo_expreso_cierra_al_precio_original(self):
        v, tx = self.ronda_no_cumple("Larva rechaza dvd")
        precio = tx.price_unit
        self.postura(v, "buyer").action_counter(precio * 0.8, actor=self.s["cam"])
        with self.assertRaises(AccessError):
            self.postura(v, "seller").action_reject(actor=self.s["cam"])  # solo el vendedor
        self.postura(v, "seller").action_reject(actor=self.s["lab"])
        self.assertEqual(v.counter_outcome, "rejected")
        self.assertEqual(v.acceptance_state, "closed")
        self.assertEqual(tx.price_unit, precio)
        self.assertIn(tx.state, ("confirmed", "done"))
        self.assertFalse(self.strikes(tx))

    def test_el_vendedor_acepta_la_contraoferta(self):
        v, tx = self.ronda_no_cumple("Larva acepta dvd")
        nuevo = round(tx.price_unit * 0.8, 2)
        self.postura(v, "buyer").action_counter(nuevo, actor=self.s["cam"])
        self.postura(v, "seller").action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "closed")
        self.assertAlmostEqual(tx.price_unit, nuevo, places=2)


# ======================================================================
# D-16
# ======================================================================
@tagged("post_install", "-at_install")
class TestProductoNoPresentado(DecisionesCommon):

    def foto(self, v):
        att = self.env["ir.attachment"].create({
            "name": "evidencia.png", "datas": base64.b64encode(PNG), "mimetype": "image/png",
            "res_model": "shrimp.verification", "res_id": v.id})
        v.sudo().write({"photo_ids": [(4, att.id)]})

    def test_sin_campo_no_se_dictamina(self):
        r = self.compra("Larva sin campo dvd")
        v = r["verification"]
        with self.assertRaises(UserError):
            v.sudo()._close("rejected", notes="No me gustó")
        v.sudo().action_assign_technician(self.s["tec"])
        v.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                        "larvae_avg_size_mg": 5.0, "larvae_health_status": "good"})
        with self.assertRaises(UserError):
            v.sudo()._close("approved")
        self.assertEqual(v.state, "assigned")

    def test_no_presentado_exige_evidencia_y_carga_al_vendedor(self):
        r = self.compra("Larva no presentada dvd")
        v, tx = r["verification"], r["transaction"]
        v.sudo().action_assign_technician(self.s["tec"])
        llegada = fields.Datetime.now() - timedelta(hours=2)
        with self.assertRaises(UserError):
            v.action_not_presented(llegada, "No llegó el camión")  # sin fotos
        self.foto(v)
        with self.assertRaises(UserError):
            v.action_not_presented(False, "No llegó el camión")  # sin hora
        v.action_not_presented(llegada, "Esperé dos horas y el camión no llegó")
        self.assertEqual(v.state, "rejected")
        self.assertEqual(v.rejection_kind, "not_presented")
        self.assertEqual(tx.state, "cancel", "se cancela en el acto")
        self.assertFalse(v.contest_state, "sin segunda opinión")
        self.assertEqual(v.fee_payer_partner_id, self.s["lab"])
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["lab"])
        strike = self.strikes(tx)
        self.assertEqual(strike.reason, "product_not_presented")
        self.assertIn("no presentado", strike.name.lower())
        self.assertEqual(v.payout_state, "billed", "la visita se paga")


# ======================================================================
# D-25
# ======================================================================
@tagged("post_install", "-at_install")
class TestSegundaOpinion(DecisionesCommon):

    def rechazada(self, nombre):
        r = self.compra(nombre)
        v = self.en_campo(r["verification"], cumple=False)
        v.sudo()._close("rejected", notes="Estado sanitario inaceptable")
        return v, r["transaction"]

    def test_sin_impugnar_queda_firme(self):
        v, tx = self.rechazada("Larva firme dvd")
        self.assertEqual(v.contest_state, "open")
        self.assertEqual(tx.state, "pending_verification", "la compra queda retenida")
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["cam"], "todavía sin reasignar")
        self.assertEqual(v.payout_state, "none")
        self.cron(v.contest_deadline - timedelta(hours=1))
        self.assertEqual(v.contest_state, "open")
        self.cron(v.contest_deadline + timedelta(minutes=5))
        self.assertEqual(v.contest_state, "firm")
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["lab"])
        self.assertEqual(v.payout_state, "billed")
        self.assertFalse(self.strikes(tx))

    def test_segunda_opinion_aprueba_y_la_compra_sigue(self):
        v, tx = self.rechazada("Larva revierte dvd")
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_cam"]).sudo().action_request_second_opinion(
                self.s["verif2"], actor=self.s["cam"])  # solo el vendedor
        with self.assertRaises(ValidationError):
            tx.with_user(self.s["u_lab"]).sudo().action_request_second_opinion(
                self.s["verif"], actor=self.s["lab"])  # nunca la misma
        segunda = tx.with_user(self.s["u_lab"]).sudo().action_request_second_opinion(
            self.s["verif2"], actor=self.s["lab"])
        self.env.invalidate_all()
        self.assertTrue(v.superseded)
        self.assertEqual(v.contest_state, "contested")
        self.assertEqual(tx.verification_ids, segunda, "la compra ve la vigente")
        self.assertEqual(tx.verifier_partner_id, self.s["verif2"])
        self.assertEqual(segunda.second_opinion_of_id, v)
        self.assertEqual(segunda.fee, self.env["shrimp.verification.fee"].compute(
            tx.transaction_qty, self.s["verif2"]))
        self.assertEqual(segunda.fee_charge_id.payer_partner_id, self.s["lab"], "la paga quien la pide")
        self.assertEqual(tx.state, "pending_verification")
        # La segunda aprueba y las partes aceptan.
        segunda.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                              "larvae_avg_size_mg": 5.0, "larvae_health_status": "good",
                              "state": "in_field", "field_start_date": fields.Datetime.now()})
        segunda.sudo()._close("approved")
        self.assertEqual(segunda.fee_payer_partner_id, self.s["lab"])
        self.postura(segunda, "buyer").action_accept(actor=self.s["cam"])
        self.postura(segunda, "seller").action_accept(actor=self.s["lab"])
        self.env.invalidate_all()
        self.assertEqual(segunda.acceptance_state, "closed")
        self.assertIn(tx.state, ("confirmed", "done"))
        self.assertEqual(segunda.fee_charge_id.payer_partner_id, self.s["lab"])
        self.assertEqual(v.contest_state, "overturned")
        self.assertEqual(v.fee_payer_partner_id, self.s["cam"], "la primera: reglas normales")
        self.assertEqual(v.payout_state, "billed")
        self.assertEqual(segunda.payout_state, "billed")

    def test_segunda_opinion_confirma_el_rechazo(self):
        v, tx = self.rechazada("Larva confirma dvd")
        segunda = v.action_request_second_opinion(self.s["verif2"], actor=self.s["lab"])
        segunda.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 50.0,
                              "larvae_avg_size_mg": 5.0, "larvae_health_status": "rejected",
                              "state": "in_field", "field_start_date": fields.Datetime.now()})
        segunda.sudo()._close("rejected", notes="Confirmo: estado sanitario malo")
        self.env.invalidate_all()
        self.assertFalse(segunda.contest_state, "una segunda opinión no se impugna")
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.contest_state, "upheld")
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["lab"])
        self.assertEqual(segunda.fee_charge_id.payer_partner_id, self.s["lab"])
        self.assertEqual(v.payout_state, "billed")
        self.assertEqual(segunda.payout_state, "billed")

    def test_fuera_de_plazo_no_se_impugna(self):
        v, tx = self.rechazada("Larva tarde dvd")
        with freeze_time(v.contest_deadline + timedelta(minutes=1)):
            self.env.invalidate_all()
            with self.assertRaises(ValidationError):
                v.action_request_second_opinion(self.s["verif2"], actor=self.s["lab"])


# ======================================================================
# D-41
# ======================================================================
@tagged("post_install", "-at_install")
class TestDeclaradaPlazo(DecisionesCommon):

    def test_vence_sin_presentar_y_se_cancela_sin_costo(self):
        r = self.compra("Larva declarada dvd", mode="declared")
        v, tx = r["verification"], r["transaction"]
        self.assertEqual(v.state, "declared_draft")
        limite = v.declared_deadline
        ref = max(tx.delivery_date or self.hoy, self.hoy)
        self.assertGreaterEqual(limite.date(), ref + timedelta(days=4))
        self.cron(limite - timedelta(hours=30))
        self.assertFalse(tx.message_ids.filtered(lambda m: "Presenta el informe" in (m.subject or "")))
        self.cron(limite - timedelta(hours=10))
        self.assertTrue(tx.message_ids.filtered(lambda m: "Presenta el informe" in (m.subject or "")))
        self.assertEqual(tx.state, "pending_verification")
        self.cron(limite + timedelta(minutes=5))
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "cancelled")
        reg = tx.shrimp_cancellation()
        self.assertEqual((reg.kind, reg.cause), ("system", "declared_overdue"))
        self.assertFalse(reg.strike_id)

    def test_el_vendedor_desiste_con_strike(self):
        r = self.compra("Larva desiste dvd", mode="declared")
        tx = r["transaction"]
        reg = tx.with_user(self.s["u_lab"]).sudo().shrimp_cancel(
            "seller", reason="No voy a poder cosechar", actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(reg.strike_id.reason, "seller_cancel")
