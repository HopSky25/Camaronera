"""API y compras al mismo nivel (camaronera → camaronera): las mismas reglas
que el portal. La compra muestra su tipo y los roles de cada parte, el
comprador la recibe por la API y el camarón que recibe aparece entre SUS
productos; el aviso «lote publicado» llega también a otras camaroneras."""
from datetime import date, timedelta

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.webhook import _lot_published_audience

from .common import ApiCase


@tagged("post_install", "-at_install")
class TestApiMismoNivel(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.adulto = env["shrimp.product"].create({
            "name": "Cosecha A mismo nivel API", "seller_partner_id": cls.farm_a.id,
            "seller_role": "camaronera",
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": env.ref("shrimp_marketplace.uom_libra").id, "presentation": "entero",
            "size_grade_id": env.ref("shrimp_marketplace.size_entero_3040").id,
            "initial_qty": 10000.0, "price": 2.4, "state": "published",
            "expected_delivery_date": date.today()})

    def test_aviso_de_lote_publicado_llega_a_otra_camaronera(self):
        self.assertTrue(_lot_published_audience(self.adulto, self.farm_b))
        self.assertTrue(_lot_published_audience(self.adulto, self.packer))
        self.assertFalse(_lot_published_audience(self.adulto, self.lab))

    def test_compra_entre_camaroneras_por_la_api(self):
        # La compra la crea el servidor (flujo verificado, aquí abreviado).
        tx = self.env["shrimp.transaction"].create({
            "transaction_type": "camaronera_to_camaronera", "product_id": self.adulto.id,
            "seller_partner_id": self.farm_a.id, "buyer_partner_id": self.farm_b.id,
            "transaction_qty": 4000.0, "desired_qty": 4000.0, "price_unit": 2.4,
            "amount_total": 9600.0,
            # Ayer: «hoy» del servidor (UTC) puede ir un día por delante del
            # de la zona del usuario.
            "desired_date": date.today() - timedelta(days=1)})
        tx.action_confirm()
        resp = self.api("GET", "/transactions/%s" % tx.uuid_ref, raw=self.raw_b)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["type"], "camaronera_to_camaronera")
        self.assertEqual((data["seller_role"], data["buyer_role"]), ("camaronera", "camaronera"))
        self.assertEqual(data["my_role"], "buyer")
        # El vendedor no confirma la recepción; el comprador sí.
        self.assertProblem(self.api("POST", "/transactions/%s:receive" % tx.uuid_ref,
                                    raw=self.raw_a, body={}), 403)
        resp = self.api("POST", "/transactions/%s:receive" % tx.uuid_ref, raw=self.raw_b, body={})
        self.assertEqual(resp.status_code, 200, resp.text)
        recibido = resp.json()["result_product"]
        self.assertTrue(recibido, "el adulto entre camaroneras nace como producto propio")
        # Aparece entre los productos de B, como camaronera y en borrador.
        resp = self.api("GET", "/products/%s" % recibido["id"], raw=self.raw_b)
        self.assertEqual(resp.status_code, 200, resp.text)
        prod = resp.json()
        self.assertEqual((prod["seller_type"], prod["state"]), ("camaronera", "draft"))
        self.assertEqual(prod["presentation"], "entero")
        self.assertTrue(prod["requires_verification"])
        # A no lo ve como suyo.
        self.assertEqual(self.api("GET", "/products/%s" % recibido["id"], raw=self.raw_a).status_code, 404)
