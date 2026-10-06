import datetime

from odoo import fields
from odoo.tests import tagged

from .common import ApiCase, int_ids


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiAguaje(ApiCase):
    """La lista de precios rige para un aguaje del calendario: obligatorio al
    crear y al publicar, y nunca uno que ya pasó."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Aguaje = cls.env["shrimp.aguaje"]
        hoy = fields.Date.context_today(Aguaje)
        # Uno claramente pasado, fuera de cualquier año sembrado.
        cls.pasado = Aguaje.create({
            "name": "Aguaje viejo API", "year": 2001, "numero": 1, "fase": "nueva",
            "date_from": datetime.date(2001, 1, 1), "date_to": datetime.date(2001, 1, 4)})
        cls.proximo = Aguaje.seleccionables()[:1]
        assert cls.proximo and cls.proximo.date_to >= hoy
        cls.grade = cls.env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)

    def _body(self, **extra):
        body = {"name": "Lista aguaje API", "recipients": [self.farm_a.uuid_ref],
                "lines": [{"size_grade": self.grade.uuid_ref, "price": 2.5}]}
        body.update(extra)
        return body

    def test_aguaje_obligatorio_al_crear(self):
        resp = self.api("POST", "/price-lists", raw=self.raw_packer, body=self._body())
        body = self.assertProblem(resp, 422)
        self.assertIn("aguaje", resp.text)
        self.assertFalse(int_ids(body))

    def test_aguaje_pasado_rechazado(self):
        resp = self.api("POST", "/price-lists", raw=self.raw_packer,
                        body=self._body(aguaje=self.pasado.uuid_ref))
        self.assertProblem(resp, 422, "business-rule")
        self.assertIn("ya pasó", resp.json()["detail"])

    def test_aguaje_proximo_y_cambios(self):
        resp = self.api("POST", "/price-lists", raw=self.raw_packer,
                        body=self._body(aguaje=self.proximo.uuid_ref))
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual(data["aguaje"]["id"], self.proximo.uuid_ref)
        # Sin despacho, se toma del aguaje.
        self.assertTrue(data["dispatch_from"])
        lid = data["id"]
        # Cambiarlo a uno pasado: no.
        resp = self.api("PUT", "/price-lists/%s" % lid, raw=self.raw_packer,
                        body=self._body(aguaje=self.pasado.uuid_ref))
        self.assertProblem(resp, 422, "business-rule")
        self.assertIn("ya pasó", resp.json()["detail"])
        # Un PUT sin aguaje conserva el suyo.
        resp = self.api("PUT", "/price-lists/%s" % lid, raw=self.raw_packer,
                        body=self._body(name="Renombrada"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["aguaje"]["id"], self.proximo.uuid_ref)
        resp = self.api("POST", "/price-lists/%s:publish" % lid, raw=self.raw_packer)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "published")

    def test_publicar_lista_historica_con_aguaje_pasado(self):
        # Una lista vieja (cargada como historia) sigue existiendo, pero
        # volver a publicarla para un aguaje que ya pasó no.
        lista = self.env["shrimp.price.list"].create({
            "name": "Histórica API", "issuer_partner_id": self.packer.id,
            "aguaje_id": self.pasado.id, "recipient_ids": [(6, 0, self.farm_a.ids)],
            "line_ids": [(0, 0, {"size_grade_id": self.grade.id, "uom": "kg",
                                 "quality": "ab", "price": 2.0})]})
        resp = self.api("POST", "/price-lists/%s:publish" % lista.uuid_ref, raw=self.raw_packer)
        self.assertProblem(resp, 422, "business-rule")
        self.assertIn("ya pasó", resp.json()["detail"])

    def test_catalogo_proximos(self):
        resp = self.url_open("/api/v1/public/catalogs/aguajes?upcoming=true")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertTrue(data)
        self.assertFalse(int_ids(data))
        hoy = fields.Date.context_today(self.env["shrimp.aguaje"]).isoformat()
        self.assertTrue(all(a["date_to"] >= hoy for a in data))
        self.assertNotIn(self.pasado.uuid_ref, [a["id"] for a in data])
        self.assertTrue(all(a["label"] and a["status"] in ("actual", "proximo") for a in data))
