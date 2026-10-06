"""El empaque enlazado a SU compra, la comisión de la plataforma y la
presentación empacada como selección."""
from datetime import date, timedelta

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from odoo.addons.shrimp_marketplace.tests.test_coherencia import cargar_migracion

from .common import CopackCommon


@tagged("post_install", "-at_install")
class TestCopackCoherencia(CopackCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Socio = env["res.partner"]
        cls.emp1 = Socio.create({
            "name": "Empacadora uno", "is_company": True, "email": "emp1.cc@prueba.test",
            "vat_or_id": "0955500011001", "shrimp_user_type": "empacadora"})
        cls.emp2 = Socio.create({
            "name": "Empacadora dos", "is_company": True, "email": "emp2.cc@prueba.test",
            "vat_or_id": "0955500012001", "shrimp_user_type": "empacadora"})
        lote = env["shrimp.product"].create({
            "name": "Engorde cc", "seller_partner_id": cls.cli.id, "seller_role": "camaronera",
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "presentation": "entero",
            "size_grade_id": env.ref("shrimp_marketplace.size_entero_3040").id,
            "initial_qty": 100000.0, "price": 2.5, "state": "published"})
        cls.lote = lote
        Tx = env["shrimp.transaction"]

        def compra(comprador, libras):
            return Tx.create({
                "transaction_type": "camaronera_to_buyer", "product_id": lote.id,
                "seller_partner_id": cls.cli.id, "buyer_partner_id": comprador.id,
                "state": "confirmed", "transaction_qty": libras, "desired_qty": libras,
                "price_unit": 2.5, "amount_total": 2.5 * libras,
                "desired_date": date.today()})

        cls.tx1 = compra(cls.emp1, 40000.0)
        cls.tx2 = compra(cls.emp2, 30000.0)

    def _solicitud_de_compra(self, cliente, tx):
        vals = self.env["shrimp.copack.request"]._shrimp_resolver_origen(
            cliente, "t:%s" % tx.uuid_ref)
        sol = self.env["shrimp.copack.request"].create(dict(vals, **{
            "client_partner_id": cliente.id, "quantity_lb": 40000,
            "presentation": "entero", "needed_from": date.today(),
            "needed_to": date.today() + timedelta(days=7),
            "copacker_partner_id": self.maq.id}))
        sol.action_publish()
        return sol

    def test_la_empacadora_elige_su_compra(self):
        origenes = self.env["shrimp.copack.request"]._shrimp_origenes_del_cliente(self.emp1)
        refs = [o["ref"] for o in origenes]
        self.assertIn("t:%s" % self.tx1.uuid_ref, refs)
        self.assertNotIn("t:%s" % self.tx2.uuid_ref, refs)
        with self.assertRaises(ValidationError):
            self.env["shrimp.copack.request"]._shrimp_resolver_origen(
                self.emp1, "t:%s" % self.tx2.uuid_ref)
        # Ni a mano: la restricción del modelo también lo impide.
        with self.assertRaises(ValidationError):
            self.env["shrimp.copack.request"].create({
                "client_partner_id": self.emp1.id, "transaction_id": self.tx2.id,
                "quantity_lb": 1000, "presentation": "entero",
                "needed_from": date.today(), "needed_to": date.today()})

    def test_el_empaque_sale_solo_en_su_compra(self):
        sol = self._solicitud_de_compra(self.emp1, self.tx1)
        _oferta, orden = self.adjudicar(sol)
        self.assertEqual(orden.transaction_id, self.tx1)
        self.assertEqual(orden.product_id, self.lote)
        orden.write({"received_lb": 40000})
        orden.action_register_reception()
        orden.write({"packed_lb": 39900, "packed_presentation": "cola",
                     "packed_presentation_note": "master 5 lb"})
        orden.action_register_packing()
        for firma in orden.acceptance_ids:
            firma.action_accept(actor=firma.partner_id)
        self.assertEqual(orden.state, "signed")
        self.assertIn(orden, self.tx1.copack_order_ids)
        # La otra compra del MISMO producto no lo muestra (antes sí).
        self.assertNotIn(orden, self.tx2.copack_order_ids)
        # Comisión de la plataforma al maquilador, una sola vez.
        cobros = orden.charge_ids.filtered(lambda c: c.charge_type == "copack_platform")
        self.assertEqual(len(cobros), 1)
        self.assertEqual(cobros.payer_partner_id, self.maq)
        self.assertAlmostEqual(cobros.amount, orden.platform_amount)
        orden._register_platform_charge()
        self.assertEqual(len(orden.charge_ids), 1)

    def test_cada_parte_firma_el_acta_suya(self):
        _sol, orden = self.hasta_empacar()
        firma_cli = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        with self.assertRaises(AccessError):
            firma_cli.action_accept(actor=self.maq)
        with self.assertRaises(ValidationError):
            firma_cli.action_reject(motivo="", actor=self.cli)

    def test_migracion_presentacion_empacada(self):
        _sol, orden = self.hasta_empacar()
        cr = self.env.cr
        cr.execute("UPDATE shrimp_copack_order SET packed_presentation = 'Cola directa IQF',"
                   " packed_presentation_note = NULL WHERE id = %s", (orden.id,))
        mig = cargar_migracion("shrimp_copacking", "19.0.1.2.0", "pre")
        mig.migrate(cr, "19.0.1.1.0")
        mig.migrate(cr, "19.0.1.1.0")
        orden.invalidate_recordset()
        self.assertEqual(orden.packed_presentation, "cola")
        self.assertEqual(orden.packed_presentation_note, "Cola directa IQF")

    def test_secuencia_de_solicitudes(self):
        sol = self.solicitud()
        self.assertTrue(sol.name.startswith("SOL-EMP-"), sol.name)
