"""Demo pendiente de dependencias shrimp (bases reinstaladas)."""
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_user_registry import demo_utils


@tagged("post_install", "-at_install")
class TestDemoUtils(TransactionCase):

    def test_dependencias_en_orden_de_carga(self):
        deps = demo_utils._shrimp_deps("shrimp_packer")
        self.assertEqual(deps[:2], ["shrimp_user_registry", "shrimp_marketplace"])
        self.assertIn("shrimp_verification", deps)
        self.assertNotIn("shrimp_packer", deps)

    def test_centinela(self):
        c = demo_utils._centinela("shrimp_marketplace")
        self.assertTrue(c and c.startswith("shrimp_marketplace."))

    def test_sin_nada_pendiente_no_hace_nada(self):
        """Con la demo cargada (o una base sin demo) no carga nada."""
        Module = self.env["ir.module.module"]
        antes = Module.search([("name", "=like", "shrimp_%")]).mapped(lambda m: (m.name, m.demo))
        self.assertTrue(demo_utils.cargar_demo_pendiente(self.env, "shrimp_packer"))
        despues = Module.search([("name", "=like", "shrimp_%")]).mapped(lambda m: (m.name, m.demo))
        self.assertEqual(antes, despues)
