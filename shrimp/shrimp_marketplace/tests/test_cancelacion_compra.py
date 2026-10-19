"""Huecos #3 y #5 de la auditoría de procesos.

#3 Sin despacho la recepción NO se confirma sola: al vencer el plazo la
   compra queda «No entregada» (aviso a la plataforma, nunca cuenta como
   entrega cumplida), el comprador confirma o cancela sin costo (strike al
   vendedor) y, si nadie actúa, se cancela sola a los N días.
#5 Cancelación general: vendedor (antes del despacho, con strike), mutuo
   acuerdo (con caducidad), plataforma (asistente con motivo obligatorio),
   desenlaces de la incidencia («no se entregó» y entrega parcial), registro
   de la cancelación, portal y borradores legales.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, csrf_de, producto
from .test_retraso_entrega import RetrasoCommon
from ..models.shrimp_cancel_policy import DONE_PARAM, MARKER


@tagged("post_install", "-at_install")
class TestNoEntregada(RetrasoCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP.set_param("shrimp_marketplace.not_delivered_cancel_days", "7")

    def test_sin_despacho_no_se_confirma_sola(self):
        tx = self.comprar("sem", "lab")
        limite = tx.reception_deadline
        self.cron(limite + timedelta(days=1))
        self.assertEqual(tx.state, "confirmed", "sin despacho no hay autoconfirmación")
        self.assertEqual(tx.reception_status, "not_delivered")
        self.assertTrue(tx.reception_had_incidence)
        self.assertFalse(tx.reception_auto_confirmed)
        self.assertEqual(tx.shrimp_not_delivered_since(), limite + timedelta(days=1))
        self.assertTrue(self.mensajes(tx, "no fue despachada"))
        self.assertTrue(self.mensajes(tx, "no entregada"))
        self.assertTrue(self.mensajes(tx, "Compra no entregada"), "aviso a la plataforma")
        # Nunca cuenta como entrega cumplida.
        self.s["sem"].invalidate_recordset()
        self.assertEqual(self.s["sem"].shrimp_delivery_auto_count, 0)
        # El vendedor ya no puede marcar el despacho.
        with self.assertRaises(ValidationError):
            self.vendedor(tx).action_mark_dispatched(actor=self.s["sem"])
        # El comprador puede cancelar sin costo...
        self.assertEqual(tx.shrimp_delay_cancel_cause(), "not_delivered")
        self.assertEqual(tx.shrimp_buyer_cancel_cause(), "not_delivered")
        # ... o confirmar si en realidad la recibió.
        self.comprador(tx).shrimp_confirm_reception(actor=self.s["lab"])
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "received")

    def test_con_despacho_si_se_confirma_sola(self):
        tx = self.comprar("sem", "lab")
        self.vendedor(tx).action_mark_dispatched(actor=self.s["sem"])
        self.cron(tx.reception_deadline + timedelta(days=1))
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "auto")

    def test_comprador_cancela_no_entregada(self):
        self.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        tx = self.comprar("sem", "lab")
        lote = tx.stock_move_ids.filtered(lambda m: m.move_type == "transfer").lot_id
        antes = lote.available_qty
        self.cron(tx.reception_deadline + timedelta(days=1))
        reg = self.comprador(tx).shrimp_cancel("buyer", reason="Nunca llegó", actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual((reg.kind, reg.cause), ("buyer", "not_delivered"))
        lote.invalidate_recordset()
        self.assertEqual(lote.available_qty, antes + tx.transaction_qty)
        # D-42: un strike por compra (aquí ya lo había dado el atraso sin aviso).
        self.assertEqual(len(self.strikes(tx)), 1)
        self.assertTrue(all(c.state in ("cancelled", "to_credit", "credited")
                            for c in tx.charge_ids))
        self.assertEqual(tx.delay_ids.filtered(lambda d: d.kind == "cancel").cancel_cause,
                         "not_delivered")

    def test_se_cancela_sola_a_los_n_dias(self):
        tx = self.comprar("sem", "lab")
        dia = tx.reception_deadline + timedelta(days=1)
        self.cron(dia)
        self.assertEqual(tx.shrimp_not_delivered_cancel_date(), dia + timedelta(days=7))
        self.cron(dia + timedelta(days=6))
        self.assertEqual(tx.state, "confirmed")
        self.cron(dia + timedelta(days=7))
        self.assertEqual(tx.state, "cancel")
        reg = tx.shrimp_cancellation()
        self.assertEqual((reg.kind, reg.cause), ("system", "not_delivered"))
        # D-42: el único strike de la compra (el del atraso sin aviso).
        self.assertTrue(reg.strike_id)
        self.assertEqual(len(self.strikes(tx)), 1)
        # 0 = nunca.
        self.ICP.set_param("shrimp_marketplace.not_delivered_cancel_days", "0")
        tx2 = self.comprar("sem", "lab")
        d2 = tx2.reception_deadline + timedelta(days=1)
        self.cron(d2)
        self.cron(d2 + timedelta(days=40))
        self.assertEqual(tx2.state, "confirmed")
        self.assertEqual(tx2.reception_status, "not_delivered")
        self.ICP.set_param("shrimp_marketplace.not_delivered_cancel_days", "7")

    def test_plataforma_reabre_el_plazo(self):
        tx = self.comprar("sem", "lab")
        self.cron(tx.reception_deadline + timedelta(days=1))
        tx.action_incidence_reopen()
        self.assertEqual(tx.reception_status, "waiting")
        self.assertFalse(tx.shrimp_not_delivered_since())
        self.vendedor(tx).action_mark_dispatched(actor=self.s["sem"])
        self.assertTrue(tx.dispatched_at)


@tagged("post_install", "-at_install")
class TestCancelacionGeneral(RetrasoCommon):

    def setUp(self):
        super().setUp()
        self.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0

    def test_vendedor_cancela_antes_del_despacho(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        lote = tx.stock_move_ids.filtered(lambda m: m.move_type == "transfer").lot_id
        self.assertTrue(tx.shrimp_seller_can_cancel())
        with self.assertRaises(AccessError):  # el comprador no cancela como vendedor
            self.comprador(tx).shrimp_cancel("seller", reason="x", actor=self.s["lab"])
        with self.assertRaises(ValidationError):  # motivo obligatorio
            self.vendedor(tx).shrimp_cancel("seller", reason=" ", actor=self.s["sem"])
        reg = self.vendedor(tx).shrimp_cancel("seller", reason="Mortalidad", actor=self.s["sem"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual((reg.kind, reg.cause, reg.from_state),
                         ("seller", "seller_withdrawal", "confirmed"))
        self.assertEqual(reg.strike_id.reason, "seller_cancel")
        self.assertEqual(reg.stock_returned_qty, tx.transaction_qty)
        self.assertGreaterEqual(reg.charges_voided, 1)
        lote.invalidate_recordset()
        self.assertEqual(lote.available_qty, 100.0)
        self.assertTrue(self.mensajes(tx, "El vendedor canceló"))
        # Despachada: ya no.
        tx2 = self.comprar("sem", "lab")
        self.vendedor(tx2).action_mark_dispatched(actor=self.s["sem"])
        self.assertIn("despachada", tx2.shrimp_seller_cancel_problem())
        with self.assertRaises(ValidationError):
            self.vendedor(tx2).shrimp_cancel("seller", reason="x", actor=self.s["sem"])

    def test_mutuo_acuerdo(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        req = self.comprador(tx).action_cancel_request("Ya no hace falta", actor=self.s["lab"])
        self.assertEqual(req.state, "requested")
        self.assertTrue(self.mensajes(tx, "mutuo acuerdo"))
        with self.assertRaises(ValidationError):  # una a la vez
            self.vendedor(tx).action_cancel_request("Otra", actor=self.s["sem"])
        with self.assertRaises(ValidationError):  # la responde la otra parte
            self.comprador(tx).action_cancel_request_respond(True, actor=self.s["lab"])
        self.vendedor(tx).action_cancel_request_respond(False, note="No", actor=self.s["sem"])
        self.assertEqual(req.state, "rejected")
        self.assertEqual(tx.state, "confirmed")
        req2 = self.vendedor(tx).action_cancel_request("Lote perdido", actor=self.s["sem"])
        self.comprador(tx).action_cancel_request_respond(True, actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(req2.state, "done")
        self.assertFalse(req2.strike_id, "el mutuo acuerdo no da strike")
        self.assertFalse(self.strikes(tx))

    def test_mutuo_acuerdo_caduca(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=10))
        req = self.comprador(tx).action_cancel_request("Cambio de plan", actor=self.s["lab"])
        self.cron(self.hoy + timedelta(days=3))
        self.assertEqual(req.state, "expired")
        self.assertEqual(tx.state, "confirmed")
        self.assertTrue(self.mensajes(tx, "caducada"))
        req3 = self.comprador(tx).action_cancel_request("Otra vez", actor=self.s["lab"])
        self.comprador(tx).action_cancel_request_withdraw(actor=self.s["lab"])
        self.assertEqual(req3.state, "withdrawn")

    def test_plataforma_cancela_con_motivo(self):
        tx = self.comprar("sem", "lab")
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_sem"]).shrimp_cancel("platform", reason="x")
        with self.assertRaises(ValidationError):
            tx.shrimp_cancel("platform", reason="")
        wiz = self.env["shrimp.transaction.cancel.wizard"].create({
            "transaction_id": tx.id, "mode": "cancel", "reason": "Fraude", "seller_fault": True})
        wiz.action_confirm()
        reg = tx.shrimp_cancellation()
        self.assertEqual((reg.kind, reg.seller_fault), ("platform", True))
        self.assertEqual(reg.strike_id.reason, "platform_cancel")
        with self.assertRaises(ValidationError):  # una cancelada no se cancela otra vez
            tx.shrimp_cancel("platform", reason="otra")
        # Sin culpa del vendedor: sin strike. También en borrador.
        borrador = self.comprar("sem", "lab")
        borrador.sudo().write({"state": "draft"})
        reg2 = borrador.shrimp_cancel("platform", reason="Compra de prueba")
        self.assertEqual(borrador.state, "cancel")
        self.assertFalse(reg2.strike_id)
        self.assertEqual(reg2.stock_returned_qty, 0.0)
        # Completada: no.
        hecha = self.comprar("sem", "lab")
        self.vendedor(hecha).action_mark_dispatched(actor=self.s["sem"])
        self.comprador(hecha).shrimp_confirm_reception(actor=self.s["lab"])
        with self.assertRaises(ValidationError):
            hecha.shrimp_cancel("platform", reason="x")

    def test_incidencia_no_se_entrego(self):
        tx = self.comprar("sem", "lab")
        self.comprador(tx).shrimp_report_reception_issue("No llegó nada", actor=self.s["lab"])
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_lab"]).action_incidence_not_delivered()
        accion = tx.with_context(shrimp_cancel_mode="not_delivered").action_open_cancel_wizard()
        wiz = self.env["shrimp.transaction.cancel.wizard"].with_context(accion["context"]).create(
            {"reason": "El vendedor confirmó que no despachó"})
        self.assertEqual(wiz.mode, "not_delivered")
        wiz.action_confirm()
        self.assertEqual(tx.state, "cancel")
        reg = tx.shrimp_cancellation()
        self.assertEqual((reg.kind, reg.cause), ("platform", "not_delivered"))
        self.assertTrue(reg.strike_id)
        self.assertIn("no despachó", tx.incidence_resolution)

    def test_incidencia_entrega_parcial(self):
        tx = self.comprar("sem", "lab", qty=40.0)
        venta = tx.stock_move_ids.filtered(lambda m: m.move_type == "transfer")
        lote_v = venta.lot_id
        comision = tx.charge_ids.filtered(lambda c: c.charge_type == "commission")
        self.assertAlmostEqual(comision.amount, 40.0 * 5 / 100.0)
        self.comprador(tx).shrimp_report_reception_issue("Llegó la mitad", actor=self.s["lab"])
        with self.assertRaises(ValidationError):
            tx.action_incidence_partial(40.0)
        tx.action_incidence_partial(25.0, "Faltaron 15 millares")
        self.assertEqual(tx.state, "done")
        lotes_c = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", venta.ids), ("owner_id", "=", self.s["lab"].id)])
        self.assertAlmostEqual(sum(lotes_c.mapped("available_qty")), 25.0)
        lote_v.invalidate_recordset()
        self.assertAlmostEqual(lote_v.available_qty, 100.0 - 25.0)
        self.assertIn(comision.state, ("cancelled", "to_credit", "credited"))
        nueva = tx.charge_ids.filtered(lambda c: c.charge_type == "commission" and c != comision)
        self.assertEqual(len(nueva), 1)
        self.assertAlmostEqual(nueva.qty, 25.0)
        self.assertAlmostEqual(nueva.amount, 25.0 * 5 / 100.0)
        self.assertTrue(self.mensajes(tx, "entrega parcial"))
        data = tx.get_full_traceability_data()
        self.assertTrue(any(m.move_type == "adjustment" and m.direction == "in"
                            for m in data["moves"]), "la devolución queda en la trazabilidad")

    def test_avisos_de_retraso_abiertos_se_cierran(self):
        entrega = self.hoy + timedelta(days=4)
        tx = self.comprar("sem", "lab", entrega=entrega)
        aviso = self.avisar(tx, entrega + timedelta(days=3))
        tx.shrimp_cancel("platform", reason="Error de carga")
        self.assertEqual(aviso.state, "cancelled")

    def test_solicitar_anulacion_del_strike(self):
        """Fuerza mayor: la cancelación del vendedor suma strike, pero puede
        pedir que se anule con justificación y evidencia; decide la plataforma."""
        admin = self.env.ref("base.user_admin")
        admin.sudo().write({"group_ids": [(4, self.env.ref(
            "shrimp_marketplace.group_marketplace_manager").id)]})
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        reg = self.vendedor(tx).shrimp_cancel("seller", reason="Mortalidad por mancha blanca",
                                              actor=self.s["sem"])
        strike = reg.strike_id
        self.assertTrue(strike.is_active)
        self.assertTrue(strike.shrimp_can_appeal())
        with self.assertRaises(AccessError):  # solo el vendedor
            strike.with_user(self.s["u_lab"]).sudo().action_request_annulment(
                "x", actor=self.s["lab"])
        with self.assertRaises(ValidationError):
            strike.with_user(self.s["u_sem"]).sudo().action_request_annulment(
                " ", actor=self.s["sem"])
        ap = strike.with_user(self.s["u_sem"]).sudo().action_request_annulment(
            "Mortalidad certificada", actor=self.s["sem"],
            evidence={"content": b"%PDF-1.4 x", "mimetype": "application/pdf", "name": "ok.pdf"})
        self.assertEqual(ap.state, "pending")
        self.assertTrue(ap.evidence_id)
        self.assertFalse(strike.shrimp_can_appeal(), "una solicitud abierta a la vez")
        # Un portal no decide; rechazar exige respuesta.
        with self.assertRaises(AccessError):
            ap.with_user(self.s["u_sem"]).action_approve()
        with self.assertRaises(UserError):
            ap.with_user(admin).action_reject()
        ap.with_user(admin).write({"decision_note": "Falta el informe del laboratorio"})
        ap.with_user(admin).action_reject()
        self.assertEqual(ap.state, "rejected")
        self.assertFalse(strike.annulled)
        # Vuelve a pedir con evidencia y se aprueba: strike anulado.
        ap2 = strike.with_user(self.s["u_sem"]).sudo().action_request_annulment(
            "Adjunto informe del laboratorio", actor=self.s["sem"])
        ap2.with_user(admin).action_approve()
        self.assertEqual(ap2.state, "approved")
        self.assertTrue(strike.annulled)
        self.assertIn("Solicitud del vendedor aprobada", strike.annul_justification)

    def test_borradores_legales_solo_borradores(self):
        Doc = self.env["shrimp.legal.document"].sudo()
        doc = Doc.search([("code", "=", "contract")], limit=1)
        if not doc or not doc.current_version_id:
            self.skipTest("Sin contrato en esta base")
        publicada = doc.current_version_id
        texto_pub = str(publicada.content)
        self.ICP.set_param(DONE_PARAM, "")
        Policy = self.env["shrimp.cancel.policy"]
        Policy._shrimp_update_purchase_drafts()
        borrador = doc.version_ids.filtered(lambda v: v.state == "draft")[:1]
        self.assertTrue(borrador)
        self.assertIn(MARKER, str(borrador.content))
        self.assertIn("Compra no entregada", str(borrador.content))
        self.assertEqual(str(publicada.content), texto_pub, "la publicada no se toca")
        n = len(doc.version_ids)
        # Una vez por base, y por documento solo si falta la marca.
        Policy._shrimp_update_purchase_drafts()
        Policy._shrimp_update_purchase_drafts(force=True)
        self.assertEqual(len(doc.version_ids), n)
        self.assertEqual(str(borrador.content).count(MARKER + "</h4>"), 1)


@tagged("post_install", "-at_install")
class TestCancelacionPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "cxp")
        cls.env["ir.config_parameter"].sudo().set_param("shrimp_marketplace.delay_rules_since",
                                                        "2000-01-01")

    def _post(self, url, page, **data):
        r = self.url_open(page)
        data["csrf_token"] = csrf_de(r.text)
        r = self.url_open(url, data=data, allow_redirects=False)
        r.loc = r.headers.get("Location", "")
        return r

    def test_portal_vendedor_y_mutuo(self):
        hoy = fields.Date.today()
        lote = producto(self.env, self.s["sem"], nombre="Nauplio portal cxp",
                        expected_delivery_date=hoy + timedelta(days=5))
        tx = lote.execute_purchase_flow(self.s["lab"], 5.0)["transaction"]
        pagina = "/marketplace/purchases/%s/traceability" % tx.uuid_ref
        # Comprador: propone mutuo acuerdo.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self.url_open(pagina)
        self.assertIn('id="cancelacion"', r.text)
        self.assertIn("Proponer cancelar de mutuo acuerdo", r.text)
        self.assertNotIn("Cancelar la venta", r.text)
        self._post("/marketplace/purchases/%s/cancel/request" % tx.uuid_ref, pagina,
                   reason="Ya no la necesito")
        self.assertTrue(tx.shrimp_cancel_request_open())
        # Vendedor: ve la solicitud y su botón de cancelar la venta; acepta.
        self.authenticate(self.s["u_sem"].login, CLAVE)
        r = self.url_open(pagina)
        self.assertIn("pide cancelar la compra de mutuo acuerdo", r.text)
        self.assertIn("Cancelar la venta", r.text)
        r = self._post("/marketplace/purchases/%s/cancel/request/accept" % tx.uuid_ref, pagina)
        self.assertIn("saved=cancel_accepted", r.loc)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "cancel")
        r = self.url_open(pagina)
        self.assertIn("Cancelada de mutuo acuerdo", r.text)
        # Otra parte (ajena) no puede.
        lote2 = producto(self.env, self.s["sem"], nombre="Nauplio portal cxp 2",
                         expected_delivery_date=hoy + timedelta(days=5))
        tx2 = lote2.execute_purchase_flow(self.s["lab"], 5.0)["transaction"]
        self.authenticate(self.s["u_cam"].login, CLAVE)
        r = self._post("/marketplace/sales/%s/cancel" % tx2.uuid_ref, "/marketplace", reason="x")
        self.assertEqual(r.status_code, 403)
        tx2.invalidate_recordset()
        self.assertEqual(tx2.state, "confirmed")
        # Vendedor cancela la segunda (con strike).
        self.authenticate(self.s["u_sem"].login, CLAVE)
        pagina2 = "/marketplace/purchases/%s/traceability" % tx2.uuid_ref
        r = self._post("/marketplace/sales/%s/cancel" % tx2.uuid_ref, pagina2, reason="Mortalidad")
        self.assertIn("saved=cancel_seller", r.loc)
        tx2.invalidate_recordset()
        self.assertEqual(tx2.state, "cancel")
        self.assertEqual(tx2.shrimp_cancellation().kind, "seller")
