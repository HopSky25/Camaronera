"""Regla de pago a la verificadora (decisión de la dueña, 23-oct-2026).

«La verificadora se le pagará una vez realizado su trabajo; si no, no se debe
pagar ningún valor.»

Trabajo hecho = veredicto firmado por esa verificadora (aprobada, aprobada con
observaciones, rechazada por el informe o «Producto no presentado»):
  · con veredicto: se le liquida (al cerrarse la ronda, al quedar firme el
    rechazo o al cancelarse la compra con la ronda abierta) y el honorario
    cobrado al pagador se conserva;
  · sin veredicto: ninguna liquidación y el honorario se anula (nota de
    crédito si ya estaba facturado), aunque la visita hubiera empezado.
"""
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_decisiones_verificacion import PNG, DecisionesCommon  # noqa: F401

ANULADO = ("cancelled", "to_credit", "credited")


@tagged("post_install", "-at_install")
class TestReglaPagoVerificadora(DecisionesCommon):

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------
    def honorarios(self, v):
        return v.sudo().charge_ids.filtered(lambda c: c.charge_type == "verification_fee")

    def assertSinPago(self, v, msg=""):
        """Sin veredicto: ni liquidación ni honorario vigente."""
        self.env.invalidate_all()
        self.assertEqual(v.payout_state, "none", msg)
        self.assertFalse(v.vendor_bill_id, msg)
        cobros = self.honorarios(v)
        self.assertTrue(cobros, "la orden tenía honorario")
        self.assertTrue(all(c.state in ANULADO for c in cobros),
                        "%s honorario anulado: %s" % (msg, cobros.mapped("state")))
        self.assertFalse(v.fee_charge_id, msg)

    def assertPagada(self, v, verificadora, msg=""):
        """Con veredicto: liquidada a `verificadora` y el honorario vigente."""
        self.env.invalidate_all()
        self.assertEqual(v.payout_state, "billed", msg)
        self.assertEqual(v.vendor_bill_id.partner_id, verificadora, msg)
        self.assertAlmostEqual(v.vendor_bill_id.amount_untaxed, v.verifier_amount, places=2)
        self.assertTrue(v.fee_charge_id, "%s el honorario se conserva" % msg)
        self.assertNotIn(v.fee_charge_id.state, ANULADO, msg)

    def foto(self, v):
        import base64
        att = self.env["ir.attachment"].create({
            "name": "evidencia.png", "datas": base64.b64encode(PNG), "mimetype": "image/png",
            "res_model": "shrimp.verification", "res_id": v.id})
        v.sudo().write({"photo_ids": [(4, att.id)]})

    def cancelar_plataforma(self, tx, motivo="Cancelación de prueba"):
        return tx.shrimp_cancel("platform", reason=motivo)

    # ------------------------------------------------------------------
    # Con veredicto: se paga
    # ------------------------------------------------------------------
    def test_aprobada_se_paga_al_cerrar_la_ronda(self):
        r = self.compra("Larva pago aprobada rpv")
        v = self.en_campo(r["verification"])
        v.sudo()._close("approved")
        self.assertEqual(v.payout_state, "none", "con el veredicto todavía no")
        self.postura(v, "buyer").action_accept(actor=self.s["cam"])
        self.postura(v, "seller").action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "closed")
        self.assertPagada(v, self.s["verif"])

    def test_rechazo_se_paga_cuando_queda_firme(self):
        r = self.compra("Larva pago rechazo rpv")
        v = self.en_campo(r["verification"], cumple=False)
        v.sudo()._close("rejected", notes="Estado sanitario inaceptable")
        self.assertEqual(v.contest_state, "open")
        self.assertEqual(v.payout_state, "none", "con el plazo de impugnación abierto, todavía no")
        self.cron(v.contest_deadline + timedelta(minutes=5))
        self.assertEqual(v.contest_state, "firm")
        self.assertPagada(v, self.s["verif"])
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["lab"])

    def test_producto_no_presentado_se_paga(self):
        r = self.compra("Larva pago no presentado rpv")
        v, tx = r["verification"], r["transaction"]
        v.sudo().action_assign_technician(self.s["tec"])
        self.foto(v)
        v.action_not_presented(fields.Datetime.now() - timedelta(hours=2),
                               "Esperé dos horas y el camión no llegó")
        self.assertEqual(tx.state, "cancel")
        self.assertPagada(v, self.s["verif"], "fue a la planta y lo documentó: trabajo hecho")
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["lab"])

    def test_compra_cancelada_con_la_ronda_abierta_se_paga(self):
        """Antes: la ronda quedaba «Trato caído» sin liquidación aunque el
        honorario se conservaba (la plataforma se quedaba todo)."""
        r = self.compra("Larva pago mutuo ronda rpv")
        v, tx = r["verification"], r["transaction"]
        self.en_campo(v)
        v.sudo()._close("approved")
        self.assertEqual(tx.state, "pending_acceptance")
        tx.with_user(self.s["u_cam"]).sudo().action_cancel_request(
            "Cambiamos de plan", actor=self.s["cam"])
        tx.with_user(self.s["u_lab"]).sudo().action_cancel_request_respond(
            True, actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "approved", "el veredicto no se toca")
        self.assertEqual(v.acceptance_state, "broken")
        self.assertPagada(v, self.s["verif"])

    def test_con_veredicto_no_se_cancela_la_verificacion(self):
        r = self.compra("Larva pago no cancelar rpv")
        v = self.en_campo(r["verification"])
        v.sudo()._close("approved")
        with self.assertRaises(UserError):
            v.sudo().action_cancel()
        self.assertEqual(v.state, "approved")
        self.assertTrue(v.fee_charge_id)

    # ------------------------------------------------------------------
    # Sin veredicto: no se paga y el honorario se anula
    # ------------------------------------------------------------------
    def test_cancelada_en_campo_sin_veredicto(self):
        r = self.compra("Larva pago en campo rpv")
        v, tx = r["verification"], r["transaction"]
        self.en_campo(v)
        self.assertEqual(v.state, "in_field")
        self.assertTrue(v.field_start_date)
        self.cancelar_plataforma(tx)
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(v.state, "cancelled")
        self.assertSinPago(v, "visita empezada sin veredicto")
        self.assertIn("no se liquida", v.shrimp_payout_label())

    def test_cancelada_con_informe_completo_sin_veredicto(self):
        """Antes: «Informe completo» contaba como hecha y el honorario se
        conservaba sin liquidar nada a la verificadora."""
        r = self.compra("Larva pago informe rpv")
        v, tx = r["verification"], r["transaction"]
        self.en_campo(v)
        v.sudo().write({"state": "done"})
        tx.with_user(self.s["u_cam"]).sudo().action_cancel_request(
            "Ya no lo necesito", actor=self.s["cam"])
        tx.with_user(self.s["u_lab"]).sudo().action_cancel_request_respond(
            True, actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertSinPago(v, "informe completo sin veredicto")

    def test_cancelada_antes_de_asignar(self):
        r = self.compra("Larva pago recibida rpv")
        v, tx = r["verification"], r["transaction"]
        self.assertEqual(v.state, "received")
        self.cancelar_plataforma(tx)
        self.assertSinPago(v, "antes de asignar")

    def test_el_vendedor_desiste_antes_de_campo(self):
        r = self.compra("Larva pago desiste rpv")
        v, tx = r["verification"], r["transaction"]
        v.sudo().action_assign_technician(self.s["tec"])
        tx.with_user(self.s["u_lab"]).sudo().shrimp_cancel(
            "seller", reason="Mortalidad en la piscina", actor=self.s["lab"])
        self.assertSinPago(v, "el vendedor desistió")

    def test_declinada_y_cancelada(self):
        r = self.compra("Larva pago declina rpv")
        v, tx = r["verification"], r["transaction"]
        v.action_verifier_decline("Sin capacidad esa semana")
        self.env.invalidate_all()
        self.assertEqual(tx.shrimp_buyer_cancel_cause(), "verification_overdue")
        tx.with_user(self.s["u_cam"]).sudo().shrimp_cancel(
            "buyer", reason="Declinó", actor=self.s["cam"])
        self.assertSinPago(v, "declinada")

    def test_declinada_cambia_y_cobra_solo_la_que_dictamina(self):
        r = self.compra("Larva pago declina cambia rpv")
        v, tx = r["verification"], r["transaction"]
        v.action_verifier_decline("Sin capacidad esa semana")
        tx.with_user(self.s["u_cam"]).sudo().action_switch_verifier(
            self.s["verif2"], actor=self.s["cam"])
        self.env.invalidate_all()
        self.assertEqual(v.verifier_partner_id, self.s["verif2"])
        self.assertEqual(v.payout_state, "none")
        self.assertFalse(v.vendor_bill_id)
        # La nueva dictamina y se le paga a ella (nunca a la que declinó).
        v.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                        "larvae_avg_size_mg": 5.0, "larvae_health_status": "good",
                        "state": "in_field", "field_start_date": fields.Datetime.now()})
        v.sudo()._close("approved")
        self.postura(v, "buyer").action_accept(actor=self.s["cam"])
        self.postura(v, "seller").action_accept(actor=self.s["lab"])
        self.assertPagada(v, self.s["verif2"])
        facturas = self.env["account.move"].sudo().search([
            ("move_type", "=", "in_invoice"), ("partner_id", "=", self.s["verif"].id),
            ("ref", "ilike", v.name)])
        self.assertFalse(facturas, "la que declinó no cobra")

    def test_plazo_vencido_y_cambio_de_verificadora(self):
        r = self.compra("Larva pago vencida rpv")
        v, tx = r["verification"], r["transaction"]
        self.en_campo(v)
        with freeze_time(v.deadline_verdict + timedelta(hours=1)):
            self.env.invalidate_all()
            self.assertEqual(v.deadline_overdue, "verdict")
            tx.with_user(self.s["u_cam"]).sudo().action_switch_verifier(
                self.s["verif2"], actor=self.s["cam"])
        self.env.invalidate_all()
        self.assertEqual(v.state, "received")
        self.assertEqual(v.payout_state, "none", "la que no cumplió no cobra")
        self.assertFalse(v.vendor_bill_id)
        # Y si después se cancela sin veredicto de la nueva: nada para nadie.
        self.cancelar_plataforma(tx)
        self.assertSinPago(v, "vencida, cambiada y cancelada")

    def test_plazo_vencido_cancelacion_automatica(self):
        r = self.compra("Larva pago auto rpv")
        v, tx = r["verification"], r["transaction"]
        limite = v.deadline_assign
        self.cron(limite + timedelta(hours=1))
        self.cron(limite + timedelta(days=7, hours=1))
        self.assertEqual(tx.state, "cancel")
        self.assertSinPago(v, "cancelada sola al vencer")

    # ------------------------------------------------------------------
    # Segunda opinión
    # ------------------------------------------------------------------
    def rechazada(self, nombre):
        r = self.compra(nombre)
        v = self.en_campo(r["verification"], cumple=False)
        v.sudo()._close("rejected", notes="Estado sanitario inaceptable")
        return v, r["transaction"]

    def test_segunda_opinion_sin_veredicto_no_cobra(self):
        v, tx = self.rechazada("Larva pago segunda sin rpv")
        segunda = v.action_request_second_opinion(self.s["verif2"], actor=self.s["lab"])
        self.assertTrue(segunda.fee_charge_id)
        segunda.sudo().write({"state": "in_field", "field_start_date": fields.Datetime.now()})
        self.cancelar_plataforma(tx, "La planta cerró")
        self.env.invalidate_all()
        self.assertEqual(segunda.state, "cancelled")
        self.assertSinPago(segunda, "segunda opinión sin veredicto")
        # La primera sí dictaminó: su rechazo se mantiene y cobra.
        self.assertEqual(v.contest_state, "upheld")
        self.assertPagada(v, self.s["verif"], "la primera")
        self.assertEqual(v.fee_charge_id.payer_partner_id, self.s["lab"])

    def test_segunda_opinion_con_veredicto_cobra(self):
        v, tx = self.rechazada("Larva pago segunda con rpv")
        segunda = v.action_request_second_opinion(self.s["verif2"], actor=self.s["lab"])
        segunda.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 50.0,
                              "larvae_avg_size_mg": 5.0, "larvae_health_status": "rejected",
                              "state": "in_field", "field_start_date": fields.Datetime.now()})
        segunda.sudo()._close("rejected", notes="Confirmo el rechazo")
        self.assertEqual(tx.state, "cancel")
        self.assertPagada(segunda, self.s["verif2"], "la segunda")
        self.assertPagada(v, self.s["verif"], "la primera")

    # ------------------------------------------------------------------
    # Defensas: nada liquida sin veredicto
    # ------------------------------------------------------------------
    def test_sin_veredicto_ninguna_via_liquida(self):
        r = self.compra("Larva pago defensa rpv")
        v = self.en_campo(r["verification"])
        v.sudo()._shrimp_payout_request()
        self.assertEqual(v.payout_state, "none")
        # Una liquidación «pendiente» sin veredicto (dato viejo o a mano) se
        # retira en vez de emitirse, por el cron o por el botón.
        v.sudo().write({"payout_state": "pending"})
        v.action_retry_payout()
        self.assertEqual(v.payout_state, "none")
        self.assertFalse(v.vendor_bill_id)
        v.sudo().write({"payout_state": "error"})
        self.env["shrimp.verification"]._cron_verifier_payouts()
        self.assertEqual(v.payout_state, "none")
        self.assertFalse(v.vendor_bill_id)
