"""«Estado real del lote» por la API: GET/POST /products/{id}/observations y
«real_status» en la ficha pública. Las rutas viejas no cambian."""
from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install")
class TestApiObservaciones(ApiCase):

    def test_observar_por_api(self):
        pl15 = self.env.ref("shrimp_marketplace.shrimp_stage_pl15")
        disponible = self.product.available_qty
        r = self.api("POST", "/products/%s/observations" % self.product.uuid_ref, self.raw_lab, body={
            "stage": pl15.uuid_ref, "avg_size_mg": 3.2, "survival_rate": 88,
            "water_temp_c": 28.1, "salinity_ppt": 30, "dissolved_oxygen_mg_l": 5.4, "ph": 7.8,
            "estimated_qty": 500, "note": "API"})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["origin"], "observation")
        self.assertEqual(body["stage"]["id"], pl15.uuid_ref)
        self.assertEqual(body["published_stage"]["id"], self.stage_pl12.uuid_ref)
        self.assertTrue(body["diverges_from_publication"])
        self.assertEqual(body["water"]["ph"], 7.8)
        self.assertEqual(body["estimated_qty"], 500)
        self.assertTrue(body["editable"])
        self.product.invalidate_recordset()
        self.assertEqual(self.product.stage_id, self.stage_pl12, "no cambia la publicación")
        self.assertEqual(self.product.available_qty, disponible, "ni el stock")

        lista = self.api("GET", "/products/%s/observations" % self.product.uuid_ref, self.raw_lab).json()
        self.assertEqual(lista["meta"]["count"], 1)
        # Ficha pública: «real_status» sin la cantidad estimada.
        pub = self.url_open("/api/v1/public/products/%s" % self.product.uuid_ref).json()
        self.assertEqual(pub["stage"]["id"], self.stage_pl12.uuid_ref)
        self.assertEqual(pub["real_status"]["stage"]["id"], pl15.uuid_ref)
        self.assertNotIn("estimated_qty", pub["real_status"])
        # La ruta vieja de evoluciones sigue igual y trae el origen.
        evos = self.api("GET", "/products/%s/evolutions" % self.product.uuid_ref, self.raw_lab).json()
        self.assertIn("origin", evos["data"][0])

    def test_validacion_y_permisos(self):
        pl12 = self.stage_pl12.uuid_ref
        url = "/products/%s/observations" % self.product.uuid_ref
        self.assertProblem(self.api("POST", url, self.raw_lab, body={"stage": pl12, "ph": 15}), 422)
        self.assertProblem(self.api("POST", url, self.raw_lab, body={"ph": 7}), 422)
        juvenil = self.env.ref("shrimp_marketplace.shrimp_stage_juvenil").uuid_ref
        self.assertProblem(self.api("POST", url, self.raw_lab, body={"stage": juvenil}), 422)
        # Otro socio: el producto no es suyo (404, no se confirma que exista).
        self.assertProblem(self.api("POST", url, self.raw_a, body={"stage": pl12}), 404)
