"""API con varios perfiles por cuenta: /me devuelve los perfiles y el activo
("type" sigue siendo el activo), POST /me/active-role lo cambia y la
cabecera X-Shrimp-Role (o ?acting_role=) actúa con otro perfil aprobado en
una sola petición."""
from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install")
class TestApiMultiPerfil(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # El laboratorio también es camaronera.
        cls.lab._shrimp_request_role("camaronera", {
            "shrimp_representante": "Rep API", "shrimp_telefono": "04-1111111"})

    def test_me_devuelve_perfiles_y_activo(self):
        resp = self.api("GET", "/me", raw=self.raw_lab)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["type"], "laboratorio")
        self.assertEqual(data["active_role"], "laboratorio")
        roles = {r["role"]: r for r in data["roles"]}
        self.assertEqual(set(roles), {"laboratorio", "camaronera"})
        self.assertTrue(roles["laboratorio"]["active"])
        self.assertEqual(roles["camaronera"]["state"], "approved")
        # Las preferencias de todos sus perfiles.
        self.assertIn("publish_verified_history", data["preferences"])

    def test_cambiar_perfil_activo(self):
        resp = self.api("POST", "/me/active-role", raw=self.raw_lab, body={"role": "camaronera"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["type"], "camaronera")
        self.assertEqual(self.lab.shrimp_user_type, "camaronera")
        # A un perfil que no tiene, no.
        self.assertProblem(self.api("POST", "/me/active-role", raw=self.raw_lab,
                                    body={"role": "empacadora"}), 403)
        self.assertEqual(self.lab.shrimp_user_type, "camaronera")

    def _crear_producto(self, headers=None, path="/products", extra=None):
        body = {"name": "Lote multiperfil", "initial_qty": 10, "price": 1.5,
                "stage": self.stage_pl12.uuid_ref}
        body.update(extra or {})
        return self.api("POST", path, raw=self.raw_lab, body=body, headers=headers)

    def test_actuar_con_otro_perfil_en_una_peticion(self):
        resp = self._crear_producto()
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["seller_type"], "laboratorio")
        resp = self._crear_producto(headers={"X-Shrimp-Role": "camaronera"})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["seller_type"], "camaronera")
        # En el cuerpo también (no rompe la validación estricta de campos).
        resp = self._crear_producto(extra={"acting_role": "camaronera"})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["seller_type"], "camaronera")
        # La petición no cambia el perfil activo de la cuenta.
        self.assertEqual(self.lab.shrimp_user_type, "laboratorio")
        # Un perfil que no tiene aprobado: 403.
        self.assertProblem(self._crear_producto(headers={"X-Shrimp-Role": "empacadora"}), 403)

    def test_transaccion_expone_los_roles(self):
        res = self.product.sudo().execute_purchase_flow(self.farm_a, 5.0)
        tx = res["transaction"]
        resp = self.api("GET", "/transactions/%s" % tx.uuid_ref, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["seller_role"], "laboratorio")
        self.assertEqual(data["buyer_role"], "camaronera")
