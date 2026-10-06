"""API: mover producto propio entre los perfiles de la misma cuenta.

POST /lots/{id}:transfer-profile (lots:write), POST /products/{id}:change-profile
(products:write), historial GET /profile-transfers, webhook
lot.profile_transferred y el paso interno en la trazabilidad pública.
"""
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import FORBIDDEN_KEYS, ApiCase, int_ids, walk_keys

TEXTO = "Transferencia interna: Laboratorio → Camaronera (misma empresa)"


@tagged("post_install", "-at_install")
class TestApiProfileTransfer(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lab._shrimp_request_role("camaronera", {
            "shrimp_representante": "Rep API", "shrimp_telefono": "04-1111111"})
        cls.lot = cls.product.stock_lot_ids.filtered(lambda l: l.owner_id == cls.lab)[:1]
        cls.sub = cls.env["shrimp.webhook.subscription"].create({
            "name": "Lab", "partner_id": cls.lab.id, "user_id": cls.u_lab.id,
            "url": "https://hooks.example.com/lab", "event_types": "lot.profile_transferred",
            "secret": "whsec_test_lab"})

    def _transfer(self, lot, body, raw=None):
        return self.api("POST", "/lots/%s:transfer-profile" % lot.uuid_ref,
                        raw=raw or self.raw_lab, body=body)

    def test_mover_lote_por_api(self):
        resp = self._transfer(self.lot, {"to_role": "camaronera", "qty": 100, "reason": "Siembra"})
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual((data["from_role"], data["to_role"], data["kind"]),
                         ("laboratorio", "camaronera", "lot"))
        self.assertEqual(data["label"], TEXTO)
        self.assertEqual(data["source_lot"], self.lot.uuid_ref)
        self.assertEqual(len(data["new_lots"]), 1)
        nuevo = data["new_lots"][0]
        self.assertEqual((nuevo["held_role"], nuevo["parent_lot"], nuevo["available_qty"]),
                         ("camaronera", self.lot.uuid_ref, 100.0))
        self.assertEqual(data["moves"][0]["type"], "profile_transfer")
        self.assertEqual(data["moves"][0]["to_role"], "camaronera")
        self.assertFalse(int_ids(data))
        self.assertFalse(FORBIDDEN_KEYS & walk_keys(data))
        self.assertAlmostEqual(self.lot.available_qty, 900.0)
        # El lote sale con su perfil en GET /lots.
        lote = self.api("GET", "/lots/%s" % nuevo["id"], raw=self.raw_lab).json()
        self.assertEqual(lote["held_role"], "camaronera")
        # Historial.
        hist = self.api("GET", "/profile-transfers", raw=self.raw_lab).json()["data"]
        self.assertEqual([h["id"] for h in hist], [data["id"]])
        self.assertEqual(self.api("GET", "/profile-transfers/%s" % data["id"],
                                  raw=self.raw_lab).status_code, 200)
        self.assertProblem(self.api("GET", "/profile-transfers/%s" % data["id"], raw=self.raw_a), 404)
        # Webhook para la propia cuenta.
        self.assertTrue(self.env["shrimp.webhook.delivery"].search([
            ("subscription_id", "=", self.sub.id), ("event_type", "=", "lot.profile_transferred"),
            ("resource_uuid", "=", data["id"])]))

    def test_reglas_y_permisos(self):
        # Perfil que la cuenta no tiene -> 422 con el motivo.
        body = self.assertProblem(self._transfer(self.lot, {"to_role": "semillero"}), 422)
        self.assertIn("no tiene aprobado", body["detail"])
        # Más de lo disponible -> 422.
        self.assertProblem(self._transfer(self.lot, {"to_role": "camaronera", "qty": 5000}), 422)
        # Lote ajeno -> 404 (no se confirma que exista).
        self.assertProblem(self._transfer(self.lot, {"to_role": "camaronera"}, raw=self.raw_a), 404)
        # Sin el scope lots:write -> 403.
        scopes = [s for s in SCOPE_CODES if s != "lots:write"]
        _k, raw = self.env["shrimp.api.key"].api_create_key(self.u_lab, "sin lots", scopes)
        self.assertProblem(self._transfer(self.lot, {"to_role": "camaronera"}, raw=raw), 403)
        # Campo desconocido -> 422 de validación.
        self.assertProblem(self._transfer(self.lot, {"to_role": "camaronera", "precio": 1}), 422)
        self.assertAlmostEqual(self.lot.available_qty, 1000.0)

    def test_cambiar_perfil_de_producto_por_api(self):
        # La larva no la vende una camaronera: el producto entero no cambia.
        resp = self.api("POST", "/products/%s:change-profile" % self.product.uuid_ref,
                        raw=self.raw_lab, body={"to_role": "camaronera"})
        body = self.assertProblem(resp, 422)
        self.assertIn("no puede vender larva", body["detail"])
        # Con qty: solo esa cantidad, a un producto propio del perfil camaronera.
        resp = self.api("POST", "/products/%s:change-profile" % self.product.uuid_ref,
                        raw=self.raw_lab, body={"to_role": "camaronera", "qty": 10})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertNotEqual(resp.json()["target_product"]["id"], self.product.uuid_ref)
        # Nauplio de una cuenta laboratorio + semillero: sí cambia entero.
        self.lab._shrimp_request_role("semillero")
        nauplio = self.env["shrimp.product"].create({
            "name": "Nauplio API", "seller_partner_id": self.lab.id, "seller_role": "laboratorio",
            "initial_qty": 50.0, "price": 1.0, "uom_id": self.uom_millar.id,
            "stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_nauplio").id,
            "species_id": self.species.id, "state": "published"})
        resp = self.api("POST", "/products/%s:change-profile" % nauplio.uuid_ref,
                        raw=self.raw_lab, body={"to_role": "semillero", "reason": "Lo vende el semillero"})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["kind"], "product")
        self.assertEqual(nauplio.seller_role, "semillero")
        self.assertEqual(self.api("GET", "/products/%s" % nauplio.uuid_ref,
                                  raw=self.raw_lab).json()["seller_type"], "semillero")
        # Producto ajeno -> 404.
        self.assertProblem(self.api("POST", "/products/%s:change-profile" % nauplio.uuid_ref,
                                    raw=self.raw_a, body={"to_role": "laboratorio"}), 404)

    def test_trazabilidad_publica_muestra_el_paso_interno(self):
        ida = self.lot.sudo().action_transfer_profile("camaronera")
        vuelta = ida.new_lot_ids.action_transfer_profile("laboratorio")
        self.assertEqual(vuelta.target_product_id, self.product)
        self.assertEqual(self.product.state, "published")
        tx = self.product.execute_purchase_flow(self.farm_a, 10.0)["transaction"]
        resp = self.url_open("/api/v1/public/traceability/%s" % tx.trace_token)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        pasos = data["profile_transfers"]
        self.assertEqual([(p["from_role"], p["to_role"]) for p in pasos],
                         [("laboratorio", "camaronera"), ("camaronera", "laboratorio")])
        self.assertEqual(pasos[0]["label"], TEXTO)
        self.assertTrue(all(p["company"] == self.lab.name for p in pasos))
        self.assertEqual([c["role_code"] for c in data["chain"]],
                         ["laboratorio", "camaronera", "laboratorio", "camaronera"])
        self.assertFalse(int_ids(data))
        self.assertFalse(FORBIDDEN_KEYS & walk_keys(data))
        # Página pública (QR).
        html = self.url_open("/t/%s" % tx.trace_token).text
        self.assertIn("Transferencias internas (misma empresa)", html)
