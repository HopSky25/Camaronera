"""La API respeta la matriz de verificación (shrimp.verification.rule):
laboratorio → camaronera = «no aplica» → POST /verifications se rechaza; si
el administrador la cambia a «opcional», la misma petición se acepta."""

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_verification.tests.common_seguridad import montar_verificacion

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiMatrizVerificacion(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, _v = montar_verificacion(cls.env, "apimtz")
        # montar_verificacion archiva la matriz; aquí se prueba con ella.
        cls.env["shrimp.verification.rule"].with_context(active_test=False).search(
            []).write({"active": True})
        _k, cls.raw_cam = cls.env["shrimp.api.key"].api_create_key(
            cls.s["u_cam"], "cam", SCOPE_CODES)
        cls.lote = cls.env["shrimp.product"].create({
            "name": "Larva API matriz", "seller_partner_id": cls.s["lab"].id,
            "seller_role": "laboratorio", "initial_qty": 100.0, "price": 10.0,
            "uom_id": cls.env.ref("shrimp_marketplace.uom_millar").id,
            "stage_id": cls.env.ref("shrimp_marketplace.shrimp_stage_pl12").id,
            "state": "published"})

    def _comprar(self, modo, **extra):
        body = dict({"product": self.lote.uuid_ref, "qty": 2.0,
                     "verification_mode": modo}, **extra)
        return self.api("POST", "/verifications", raw=self.raw_cam, body=body)

    def test_no_aplica_y_cambio_de_matriz(self):
        V = self.env["shrimp.verification"]
        self.assertProblem(self._comprar("declared"), 422)
        self.assertProblem(self._comprar("platform", verifier=self.s["verif"].uuid_ref), 422)
        self.assertFalse(V.search([("product_id", "=", self.lote.id)]))
        self.env.ref("shrimp_verification.verification_rule_laboratorio_camaronera").policy = "optional"
        resp = self._comprar("declared")
        self.assertIn(resp.status_code, (200, 201), resp.text)
        self.assertEqual(len(V.search([("product_id", "=", self.lote.id)])), 1)
