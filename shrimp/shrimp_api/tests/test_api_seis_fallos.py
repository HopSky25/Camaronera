"""API: nota pública de vendedor sin las reseñas recibidas como comprador
(fallo 3) y fecha de la compra en la trazabilidad pública igual a la del
certificado PDF (fallo 6)."""
from datetime import date, datetime

from odoo.tests import tagged

from odoo.addons.shrimp_api.controllers import serializers as S

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiSeisFallos(ApiCase):

    def test_3_nota_de_vendedor_coherente(self):
        Review = self.env["shrimp.review"]
        Review.create({"direction": "to_seller", "seller_partner_id": self.farm_a.id,
                       "reviewer_partner_id": self.packer.id, "rating": 5})
        Review.create({"direction": "to_buyer", "seller_partner_id": self.farm_a.id,
                       "reviewer_partner_id": self.lab.id, "rating": 1})
        self.farm_a.invalidate_recordset()
        data = S.partner_public(self.farm_a)
        self.assertEqual(data["ratings"]["as_seller"], {"avg": 5.0, "count": 1})
        self.assertNotIn("as_buyer", data["ratings"], "La nota como comprador no es pública")
        self.assertEqual(len(self.farm_a._shrimp_seller_reviews()), data["ratings"]["as_seller"]["count"])

    def test_6_fecha_de_compra_publica(self):
        adult = self.env["shrimp.product"].create({
            "name": "Camarón API 6f", "seller_partner_id": self.farm_a.id, "seller_role": "camaronera",
            "stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": self.env.ref("shrimp_marketplace.uom_libra").id, "presentation": "entero",
            "size_grade_id": self.env["shrimp.size.grade"].search(
                [("presentation", "=", "entero")], limit=1).id,
            "initial_qty": 5000.0, "price": 2.2, "state": "published",
            "expected_delivery_date": date.today()})
        tx = adult.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.packer, 1000.0)["transaction"]
        tx.stock_move_ids.filtered(lambda m: m.move_type == "transfer").write(
            {"date": datetime(2026, 2, 14, 17, 0, 0)})
        self.env.cr.execute("UPDATE shrimp_transaction SET create_date = %s WHERE id = %s",
                            (datetime(2026, 1, 2, 17, 0, 0), tx.id))
        tx.invalidate_recordset()
        data = tx._public_traceability_data()
        self.assertEqual(data["dates"]["purchase"], "2026-02-14")
