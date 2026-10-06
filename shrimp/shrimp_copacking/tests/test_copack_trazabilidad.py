"""El empaque mueve el inventario y aparece en la trazabilidad de la venta
posterior (hallazgos 2 y 6b del escenario de trazabilidad)."""
from datetime import date, timedelta

from odoo.tests import tagged

from .common import CopackCommon


@tagged("post_install", "-at_install")
class TestCopackTrazabilidad(CopackCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.emp = cls.env["res.partner"].create({
            "name": "Exportadora trz", "is_company": True, "email": "exp.trz@prueba.test",
            "vat_or_id": "0955500009001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Exportadora trz S.A.", "emp_capacidad_lb_dia": 90000})
        cls.cosecha = cls.env["shrimp.product"].create({
            "name": "Cosecha trz", "seller_partner_id": cls.cli.id, "seller_role": "camaronera",
            "stage_id": cls.env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": cls.env.ref("shrimp_marketplace.uom_libra").id,
            "presentation": "entero",
            "size_grade_id": cls.env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1).id,
            "initial_qty": 54000.0, "price": 2.3, "state": "published",
            "expected_delivery_date": date.today(),
        })

    def _empacar_propio(self, recibidas=54000.0, empacadas=53760.0):
        sol = self.env["shrimp.copack.request"].create({
            "client_partner_id": self.cli.id, "quantity_lb": recibidas, "presentation": "entero",
            "needed_from": date.today(), "needed_to": date.today() + timedelta(days=7),
            "copacker_partner_id": self.maq.id, "product_id": self.cosecha.id,
        })
        sol.action_publish()
        _of, orden = self.adjudicar(sol)
        orden.write({"received_lb": recibidas})
        orden.action_register_reception()
        orden.write({"packed_lb": empacadas, "boxes": 2688})
        orden.action_register_packing()
        for firma in orden.acceptance_ids:
            firma.action_accept(actor=firma.partner_id)
        return orden

    def test_empaque_crea_lote_empacado_y_consume_merma(self):
        orden = self._empacar_propio()
        self.assertEqual(orden.state, "signed")
        self.assertTrue(orden.packed_lot_id)
        self.assertAlmostEqual(orden.packed_lot_id.available_qty, 53760.0)
        tipos = sorted(orden.packing_move_ids.mapped("move_type"))
        self.assertEqual(tipos, ["consumption", "packing"])
        merma = orden.packing_move_ids.filtered(lambda m: m.move_type == "consumption")
        self.assertAlmostEqual(merma.qty, 240.0)
        # La merma ya no está a la venta.
        self.assertAlmostEqual(self.cosecha.available_qty, 53760.0)
        # Idempotente.
        orden._shrimp_apply_packing_stock()
        self.assertEqual(len(orden.packing_move_ids), 2)

    def test_venta_posterior_lleva_el_empaque(self):
        orden = self._empacar_propio()
        tx = self.cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.emp, 53760.0)["transaction"]
        self.assertEqual(tx.stock_move_ids.parent_move_id.move_type, "packing")
        self.assertIn(orden, tx.copack_order_ids)
        cadena = [n["role_code"] for n in tx.traceability_chain()]
        self.assertIn("maquilador", cadena)
        # El dueño no se repite después del servicio de empaque.
        self.assertEqual(cadena[-2:], ["maquilador", "empacadora"])

    def test_empaque_de_la_compra_del_comprador(self):
        """La empacadora empaca lo que compró: su lote recibido se transforma
        y el empaque sale en SU compra."""
        tx = self.cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.emp, 20000.0)["transaction"]
        tx.action_receive()
        lote = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.emp.id)])
        orden = self.env["shrimp.copack.order"].create({
            "client_partner_id": self.emp.id, "copacker_partner_id": self.maq.id,
            "transaction_id": tx.id, "product_id": self.cosecha.id, "stock_lot_id": lote.id,
            "agreed_qty_lb": 20000.0, "rate_per_lb": 0.16})
        orden.write({"received_lb": 20000.0})
        orden.action_register_reception()
        orden.write({"packed_lb": 19950.0})
        orden.action_register_packing()
        for firma in orden.acceptance_ids:
            firma.action_accept(actor=firma.partner_id)
        self.assertAlmostEqual(lote.available_qty, 0.0)
        self.assertAlmostEqual(orden.packed_lot_id.available_qty, 19950.0)
        data = tx.get_full_traceability_data()
        self.assertIn(orden.packed_lot_id, data["own_lots"])
        self.assertIn(orden, tx.copack_order_ids)
