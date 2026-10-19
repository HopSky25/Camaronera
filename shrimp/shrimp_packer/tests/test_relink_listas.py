"""D-49: el lote atado a una lista archivada pasa a la nueva lista vigente
del mismo emisor (o se desata, con su precio, si no hay otra)."""
from odoo.tests import TransactionCase, tagged

from .test_aguaje_lista import _socios


@tagged("post_install", "-at_install", "shrimp_packer")
class TestRelinkListas(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.emp, cls.cam, cls.u_emp, _o, _g = _socios(env, "7")
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")
        cls.aguaje = env["shrimp.aguaje"].seleccionables()[0]

    def _lista(self, nombre, precio):
        lista = self.env["shrimp.price.list"].create({
            "name": nombre, "issuer_partner_id": self.emp.id, "aguaje_id": self.aguaje.id,
            "recipient_ids": [(6, 0, self.cam.ids)],
            "line_ids": [(0, 0, {"size_grade_id": self.talla.id, "uom": "kg",
                                 "quality": "ab", "price": precio})]})
        lista.action_publish()
        return lista

    def test_lote_pasa_a_la_lista_nueva_y_luego_se_desata(self):
        env = self.env
        vieja = self._lista("Lista vieja rl", 2.50)
        lote = env["shrimp.product"].create({
            "name": "Cosecha rl", "seller_partner_id": self.cam.id, "seller_role": "camaronera",
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "initial_qty": 1000.0, "uom_id": env.ref("shrimp_marketplace.uom_libra").id,
            "presentation": "entero", "size_grade_id": self.talla.id,
            "price_list_id": vieja.id})
        lb = 2.2046226218
        self.assertAlmostEqual(lote.price, 2.50 / lb, places=4)
        nueva = self._lista("Lista nueva rl", 2.80)
        self.assertEqual(vieja.state, "archived")
        self.assertEqual(lote.price_list_id, nueva)
        self.assertAlmostEqual(lote.price, 2.80 / lb, places=4)
        self.assertTrue(lote.message_ids.filtered(lambda m: "Lista nueva rl" in (m.body or "")))
        # Sin otra lista del emisor: se desata y conserva el precio.
        nueva.action_archive_list()
        self.assertFalse(lote.price_list_id)
        self.assertAlmostEqual(lote.price, 2.80 / lb, places=4)
        self.assertTrue(lote.message_ids.filtered(lambda m: "quedó sin lista" in (m.body or "")))
