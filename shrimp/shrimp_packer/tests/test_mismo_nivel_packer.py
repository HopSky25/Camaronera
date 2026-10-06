"""Camaronera → camaronera (compra al mismo nivel) con shrimp_packer instalado.

Escenario del usuario: la camaronera A le vende parte de su cosecha a la
camaronera B, que después se la vende a una empacadora. El adulto entre
camaroneras sigue las mismas reglas de verificación que cualquier compra de
adulto; B lo recibe como producto PROPIO (puede revenderlo) y la cadena
A → B → empacadora queda enlazada. Los juveniles, en cambio, B los siembra.
"""
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_verification.tests.common_seguridad import montar_verificacion


def _camaronera(env, nombre, ruc):
    return env["res.partner"].create({
        "name": nombre, "is_company": True, "email": "%s@prueba.test" % ruc,
        "vat_or_id": ruc, "shrimp_user_type": "camaronera",
        "shrimp_razon_social": nombre + " S.A.", "shrimp_representante": "Rep",
        "shrimp_telefono": "04-1", "shrimp_ubicacion": "Guayas"})


@tagged("post_install", "-at_install")
class TestMismoNivelCamaroneras(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s, _v = montar_verificacion(env, "mnk")
        cls.cam_a = cls.s["cam"]
        cls.cam_b = _camaronera(env, "Camaronera B mnk", "0944400071001")
        cls.cam_c = _camaronera(env, "Camaronera C mnk", "0944400072001")
        cls.emp = env["res.partner"].create({
            "name": "Empacadora mnk", "is_company": True, "email": "emp.mnk@prueba.test",
            "vat_or_id": "0966600071001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora mnk S.A.", "emp_capacidad_lb_dia": 90000})
        cls.libra = env.ref("shrimp_marketplace.uom_libra")
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")
        cls.cosecha = env["shrimp.product"].create({
            "name": "Cosecha A mnk", "seller_partner_id": cls.cam_a.id, "seller_role": "camaronera",
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": cls.libra.id, "presentation": "entero", "size_grade_id": cls.talla.id,
            "batch_code": "COS-MNK-1", "production_date": date.today(),
            "initial_qty": 20000.0, "price": 2.5, "state": "published",
            "expected_delivery_date": date.today()})
        cls.juvenil = env["shrimp.product"].create({
            "name": "Juvenil A mnk", "seller_partner_id": cls.cam_a.id, "seller_role": "camaronera",
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_juvenil").id,
            "uom_id": env.ref("shrimp_marketplace.uom_millar").id,
            "initial_qty": 500.0, "price": 11.0, "state": "published",
            "expected_delivery_date": date.today()})

    def _verificada(self, lote, comprador, qty):
        """Compra con verificación: veredicto aprobado y aceptado por las dos
        partes; se concluye y el comprador la recibe."""
        res = lote.start_verified_purchase(comprador, qty, self.s["verif"], fee=50.0)
        tx, ver = res["transaction"], res["verification"]
        self.assertEqual(tx.state, "pending_verification")
        ver.write({"state": "approved", "acceptance_state": "closed"})
        tx.action_complete_after_verification()
        self.assertEqual(tx.state, "confirmed")
        tx.write({"desired_date": date.today() - timedelta(days=1)})
        tx.action_receive()
        self.assertEqual(tx.state, "done")
        return tx

    # ------------------------------------------------------------------
    def test_adulto_a_b_empacadora(self):
        # B puede comprarle a A (antes solo la empacadora).
        self.assertFalse(self.cosecha.motivo_no_comprable(self.cam_b))
        self.assertTrue(self.cosecha.requires_verification)
        # Mismas reglas de verificación que cualquier adulto: sin ella, no.
        with self.assertRaises(ValidationError):
            self.cosecha.execute_purchase_flow(self.cam_b, 100.0)

        tx1 = self._verificada(self.cosecha, self.cam_b, 8000.0)
        self.assertEqual(tx1.transaction_type, "camaronera_to_camaronera")
        self.assertEqual((tx1.seller_role, tx1.buyer_role), ("camaronera", "camaronera"))
        self.assertTrue(tx1._buyer_can_republish())
        # Comisión como en toda compra (si la unidad la cobra).
        if self.libra.commission_cents:
            self.assertEqual(len(tx1.charge_ids.filtered(lambda c: c.charge_type == "commission")), 1)
        # B recibe un producto PROPIO, del perfil camaronera, con la misma
        # presentación, talla y código de lote.
        prod_b = tx1.result_product_id
        self.assertTrue(prod_b)
        self.assertEqual((prod_b.seller_partner_id, prod_b.seller_role), (self.cam_b, "camaronera"))
        self.assertEqual((prod_b.presentation, prod_b.size_grade_id), ("entero", self.talla))
        self.assertEqual(prod_b.batch_code, "COS-MNK-1")
        self.assertFalse(prod_b.origin_pond_id, "la piscina es del vendedor anterior")
        self.assertTrue(prod_b.requires_verification)
        self.assertGreater(prod_b.available_qty, 0.0)
        self.assertAlmostEqual(self.cosecha.available_qty, 12000.0)

        # B lo publica: lo puede comprar una empacadora u otra camaronera;
        # B no (es suyo).
        prod_b.action_publish()
        self.assertFalse(prod_b.motivo_no_comprable(self.emp))
        self.assertFalse(prod_b.motivo_no_comprable(self.cam_c))
        self.assertEqual(prod_b.motivo_no_comprable(self.cam_b), "Es tu propio lote.")
        # Un laboratorio no compra adulto.
        self.assertTrue(prod_b.motivo_no_comprable(self.s["lab"]))

        tx2 = self._verificada(prod_b, self.emp, 5000.0)
        self.assertEqual(tx2.transaction_type, "camaronera_to_buyer")
        self.assertEqual((tx2.seller_role, tx2.buyer_role), ("camaronera", "empacadora"))
        self.assertFalse(tx2._buyer_can_republish())
        # La cadena: A → B → empacadora, enlazada por el movimiento.
        self.assertEqual(tx2.stock_move_ids.parent_move_id, tx1.stock_move_ids)
        cadena = [(c["name"], c["role_code"]) for c in tx2.traceability_chain()]
        self.assertEqual(cadena[:3], [(self.cam_a.name, "camaronera"),
                                      (self.cam_b.name, "camaronera"),
                                      (self.emp.name, "empacadora")])
        self.assertIn(tx1.stock_move_ids, tx2.get_full_traceability_data()["moves"])

    def test_juveniles_se_siembran(self):
        tx = self._verificada(self.juvenil, self.cam_b, 300.0)
        self.assertEqual(tx.transaction_type, "camaronera_to_camaronera")
        self.assertFalse(tx._buyer_can_republish(), "los juveniles se siembran, no se revenden")
        self.assertFalse(tx.result_product_id)
        lote = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.cam_b.id)])
        self.assertEqual(lote.product_id, self.juvenil)
        piscina = self.env["shrimp.partner.pond"].create({"partner_id": self.cam_b.id, "name": "P-mnk"})
        siembra = self.env["shrimp.lot.allocation"].create({
            "stock_lot_id": lote.id, "pond_id": piscina.id, "allocated_qty": 200.0})
        self.assertTrue(siembra.sowing_move_id, "la siembra consume el lote comprado")
        self.assertAlmostEqual(lote.available_qty, lote.initial_qty - 200.0)

    def test_parejas_y_autocompra_con_empacadora(self):
        # Un laboratorio sigue sin comprar adulto; la empacadora sí.
        self.assertTrue(self.cosecha.motivo_no_comprable(self.s["lab"]))
        self.assertFalse(self.cosecha.motivo_no_comprable(self.emp))
        # Camaronera que también es empacadora: su propia cosecha no la
        # compra con ningún perfil.
        linea = self.cam_a._shrimp_request_role("empacadora")
        linea.action_approve()
        self.assertEqual(self.cosecha.motivo_no_comprable(self.cam_a, role="empacadora"),
                         "Es tu propio lote.")
        with self.assertRaises(ValidationError):
            self.cosecha.start_verified_purchase(self.cam_a, 10.0, self.s["verif"], fee=1.0)
        # Un contacto de la propia empresa tampoco.
        hijo = self.env["res.partner"].create({"name": "Jefe de finca mnk", "parent_id": self.cam_a.id})
        self.assertIn("No puedes comprarte a ti mismo", self.cosecha.motivo_no_comprable(hijo))
        with self.assertRaises(ValidationError):
            self.cosecha.start_verified_purchase(hijo, 10.0, self.s["verif"], fee=1.0)
