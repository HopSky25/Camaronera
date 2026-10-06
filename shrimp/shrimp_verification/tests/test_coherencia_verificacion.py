"""Ciclo de la compra verificada: reserva de stock, cobros y firmas."""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common_seguridad import abrir_ronda, montar_verificacion


@tagged("post_install", "-at_install")
class TestCoherenciaVerificacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        cls.s, cls.v = montar_verificacion(cls.env, "coh")
        cls.tx = cls.v.transaction_id
        cls.lote = cls.tx.product_id

    def _posturas(self):
        return (self.v.acceptance_ids.filtered(lambda a: a.role == "buyer"),
                self.v.acceptance_ids.filtered(lambda a: a.role == "seller"))

    def _comisiones(self):
        return self.env["shrimp.charge"].search([
            ("transaction_id", "=", self.tx.id), ("charge_type", "=", "commission")])

    # ------------------------------------------------------------------
    # 1. Sin doble venta mientras se espera la firma de las partes
    # ------------------------------------------------------------------
    def test_reserva_tambien_en_espera_de_firmas(self):
        self.assertEqual(self.tx.state, "pending_verification")
        self.assertAlmostEqual(self.lote.available_qty, 90.0)
        abrir_ronda(self.v)
        self.assertEqual(self.tx.state, "pending_acceptance")
        self.lote.invalidate_recordset()
        self.lote._compute_available_qty()
        # Antes volvía a 100: el lote se podía vender dos veces.
        self.assertAlmostEqual(self.lote.available_qty, 90.0)
        self.assertAlmostEqual(self.lote.pending_verification_qty, 10.0)
        with self.assertRaises(ValidationError):
            self.lote.execute_purchase_flow(self.s["cam"], 95.0)

    # ------------------------------------------------------------------
    # Precio y cobros
    # ------------------------------------------------------------------
    def test_mismo_precio_que_la_compra_directa(self):
        self.assertEqual(self.tx.price_unit, self.lote.price_for_partner(self.s["cam"]))

    def test_honorario_se_factura_al_iniciar(self):
        cobro = self.v.fee_charge_id
        self.assertTrue(cobro)
        self.assertEqual(cobro.charge_type, "verification_fee")
        self.assertEqual(cobro.payer_partner_id, self.s["cam"])
        self.assertAlmostEqual(cobro.amount, 50.0)
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(self.v.invoice_id, cobro.invoice_id)
        # Ninguna comisión todavía: la venta no se cerró.
        self.assertFalse(self._comisiones())

    def test_comision_una_vez_al_firmar_las_partes(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertFalse(self._comisiones())
        vendedor.sudo().action_accept(actor=self.s["lab"])
        self.assertEqual(self.tx.state, "confirmed")
        self.assertEqual(len(self._comisiones()), 1)
        # Repetir el cierre no cobra dos veces.
        self.tx.action_confirm()
        self.v._create_payer_invoice()
        self.assertEqual(len(self._comisiones()), 1)
        self.assertEqual(len(self.v.charge_ids.filtered(
            lambda c: c.state not in ("cancelled", "credited"))), 1)

    def test_reasignar_honorario_emite_nota_de_credito(self):
        cobro_inicial = self.v.fee_charge_id
        self.v.write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                      "larvae_avg_size_mg": 5.0, "larvae_health_status": "rejected",
                      "state": "done", "verdict_notes": "Sanidad no conforme"})
        self.v.action_reject()
        self.assertEqual(self.tx.state, "cancel")
        self.assertEqual(cobro_inicial.state, "credited")
        self.assertEqual(cobro_inicial.refund_id.state, "posted")
        nuevo = self.v.fee_charge_id
        self.assertTrue(nuevo)
        self.assertNotEqual(nuevo, cobro_inicial)
        self.assertEqual(nuevo.payer_partner_id, self.s["lab"])
        self.assertEqual(nuevo.state, "invoiced", nuevo.invoice_error)
        # La reserva se liberó.
        self.lote._compute_available_qty()
        self.assertAlmostEqual(self.lote.available_qty, 100.0)

    def test_cancelar_verificacion_anula_el_honorario(self):
        cobro = self.v.fee_charge_id
        self.v.action_cancel()
        self.assertEqual(cobro.state, "credited")
        self.assertFalse(self.v.fee_charge_id)

    # ------------------------------------------------------------------
    # Firma de las partes (mixin común) y nombres
    # ------------------------------------------------------------------
    def test_firma_solo_del_titular(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        with self.assertRaises(AccessError):
            vendedor.sudo().action_accept(actor=self.s["cam"])
        with self.assertRaises(AccessError):
            comprador.sudo().action_reject(reason="x", actor=self.s["lab"])
        self.assertEqual((comprador.decision, vendedor.decision), ("pending", "pending"))

    def test_verification_ids_y_alias(self):
        self.assertEqual(self.tx.verification_ids, self.v)
        self.assertEqual(self.tx.verification_id, self.v)
        self.assertEqual(self.env["shrimp.transaction"].search(
            [("verification_id", "=", self.v.id)]), self.tx)
        self.assertEqual(self.tx.verifier_partner_id, self.s["verif"])
        self.assertFalse(self.tx.is_cancelled)
