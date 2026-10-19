"""Recepción por el comprador y matriz de verificación.

- D-26: TODA compra directa lleva recepción; la columna «Confirmación de
  recepción» de la matriz es informativa y no se puede desactivar;
- una compra VERIFICADA no la lleva: se completa con la aceptación del
  informe (recepción «verified») y el cron de recepción directa no la toca;
- sin ninguna regla (matriz archivada) también la lleva.
"""
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import producto

from .common_seguridad import abrir_ronda, montar_verificacion


@tagged("post_install", "-at_install")
class TestRecepcionMatriz(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "rcm")
        cls.Rule = cls.env["shrimp.verification.rule"].sudo().with_context(active_test=False)
        cls.Tx = cls.env["shrimp.transaction"]
        cls.hoy = cls.Tx._shrimp_today()

    def _cron(self, dia):
        with freeze_time(datetime.combine(dia, datetime.min.time()) + timedelta(hours=17)):
            self.env.invalidate_all()
            self.Tx._cron_reception_and_delivery()
        self.env.invalidate_all()

    def _compra_directa(self):
        lote = producto(self.env, self.s["lab"], nombre="Larva directa rcm",
                        expected_delivery_date=self.hoy)
        return lote.execute_purchase_flow(self.s["cam"], 2.0)["transaction"]

    def test_verificada_no_lleva_recepcion(self):
        tx = self.v.transaction_id
        abrir_ronda(self.v)
        compra = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        venta = self.v.acceptance_ids.filtered(lambda a: a.role == "seller")
        compra.sudo().action_accept(actor=self.s["cam"])
        venta.sudo().action_accept(actor=self.s["lab"])
        # El informe aceptado acredita la recepción: la compra se completa
        # sola (antes quedaba «confirmada» para siempre).
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "verified")
        self.assertFalse(tx._shrimp_is_direct_delivery())
        self.assertFalse(tx.reception_required)
        self._cron(self.hoy + timedelta(days=60))
        self.assertEqual(tx.state, "done")
        self.assertFalse(tx.reception_notify_marks, "ni recibe avisos de entrega directa")

    def test_columna_de_la_matriz(self):
        """D-26: toda compra directa lleva recepción; la columna de la matriz
        es informativa y no se puede desactivar."""
        from odoo.exceptions import ValidationError
        # Matriz archivada (montar_verificacion): igual lleva recepción.
        self.assertTrue(self._compra_directa().reception_required)
        self.Rule.search([]).write({"active": True})
        general = self.Rule.search([("seller_role", "=", "laboratorio"),
                                    ("buyer_role", "=", "camaronera"),
                                    ("product_tipo", "=", False)])
        self.assertTrue(general.requires_reception_confirmation, "sembrada en la matriz")
        with self.assertRaises(ValidationError):
            general.requires_reception_confirmation = False
        nueva = self.Rule.search([("seller_role", "=", "laboratorio"), ("buyer_role", "=", "camaronera"),
                                  ("product_tipo", "=", "larva")]) or self.Rule.create({
            "seller_role": "laboratorio", "buyer_role": "camaronera", "product_tipo": "larva",
            "policy": "none"})
        nueva.write({"policy": "none"})
        self.assertTrue(nueva.requires_reception_confirmation, "activa por defecto")
        self.assertTrue(self.Rule.new({"seller_role": "laboratorio"}).requires_reception_confirmation)
        tx = self._compra_directa()
        self.assertTrue(tx.reception_required)
        self.assertEqual(tx.reception_status, "waiting")
