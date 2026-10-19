"""D-40: el honorario es la tarifa publicada por la verificadora elegida
(ver_fee_base + ver_fee_por_lb × cantidad), tal cual; la tarifa global (con su
mínimo y su tope) solo si la verificadora no publicó tarifa. Al cambiar de
verificadora se recalcula. D-39: la autodeclaración vale aunque la
verificación sea obligatoria.
"""
import re

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, producto

from .common_seguridad import montar_verificacion


@tagged("post_install", "-at_install")
class TestTarifaVerificadora(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "tvf")
        cls.Fee = cls.env["shrimp.verification.fee"]
        ICP = cls.env["ir.config_parameter"].sudo()
        for k, val in (("fee_base", "250"), ("fee_cents", "2"), ("fee_min", "300"), ("fee_max", "800")):
            ICP.set_param("shrimp_verification.%s" % k, val)

    def test_tarifa_propia_tal_cual(self):
        ver = self.s["verif"]
        ver.write({"ver_fee_base": 100.0, "ver_fee_por_lb": 0.01})
        t = self.Fee.tarifa(ver)
        self.assertEqual(t["fuente"], "verifier")
        # Sin mínimo ni tope globales: 100 + 1000 × 0,01 = 110 (el mínimo global es 300).
        self.assertEqual(self.Fee.compute(1000, ver), 110.0)
        self.assertEqual(self.Fee.compute(1000000, ver), 10100.0, "sin tope global")
        d = self.Fee.desglose(1000, ver)
        self.assertEqual((d["base"], d["variable"], d["total"]), (100.0, 10.0, 110.0))

    def test_sin_tarifa_usa_la_global(self):
        ver = self.s["verif2"]
        ver.write({"ver_fee_base": 0.0, "ver_fee_por_lb": 0.0})
        self.assertEqual(self.Fee.tarifa(ver)["fuente"], "platform")
        self.assertEqual(self.Fee.compute(10000, ver), 450.0)
        self.assertEqual(self.Fee.compute(10000), 450.0)
        self.assertEqual(self.Fee.compute(100, ver), 300.0, "mínimo global")

    def test_reasignar_recalcula_el_honorario(self):
        self.s["verif2"].write({"ver_fee_base": 70.0, "ver_fee_por_lb": 0.5})
        lote = producto(self.env, self.s["lab"], nombre="Larva tarifa tvf")
        r = lote.start_verified_purchase(self.s["cam"], 10.0, self.s["verif"], fee=50.0)
        v = r["verification"]
        cobro = v.fee_charge_id
        v._shrimp_reassign_verifier(self.s["verif2"], "prueba", "plataforma")
        self.assertEqual(v.fee, 75.0, "70 + 10 × 0,5")
        self.assertIn(cobro.state, ("cancelled", "to_credit", "credited"))
        self.assertEqual(v.fee_charge_id.amount, 75.0)
        self.assertTrue(v.message_ids.filtered(lambda m: "Honorario recalculado" in (m.body or "")))

    def test_autodeclaracion_con_verificacion_obligatoria(self):
        """D-39: la parte puede verificar ella misma aunque sea obligatoria."""
        Regla = self.env["shrimp.verification.rule"].sudo().with_context(active_test=False)
        regla = Regla.search([("seller_role", "=", "laboratorio"), ("buyer_role", "=", "camaronera")])
        if regla:
            regla.write({"policy": "mandatory", "active": True})
        else:
            Regla.create({"seller_role": "laboratorio", "buyer_role": "camaronera",
                          "policy": "mandatory", "active": True})
        lote = producto(self.env, self.s["lab"], nombre="Larva autodeclarada tvf")
        self.assertEqual(lote._shrimp_verification_policy(self.s["cam"]), "mandatory")
        r = lote.start_verified_purchase(self.s["cam"], 5.0, None, mode="declared",
                                         declared_vals={"declared_source": "self"})
        v = r["verification"]
        self.assertEqual((v.verification_mode, v.declared_source, v.fee), ("declared", "self", 0.0))
        self.assertNotIn("nombre de la verificadora externa", " ".join(v._missing_declared_fields()))


@tagged("post_install", "-at_install")
class TestCompraConTarifa(HttpCase):
    """El POST de la página de compra cobra la tarifa de la verificadora elegida."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "tvh")
        cls.s["verif2"].write({"ver_fee_base": 40.0, "ver_fee_por_lb": 1.5})

    def test_post_cobra_la_tarifa_elegida(self):
        lote = producto(self.env, self.s["lab"], nombre="Larva post tvh")
        self.authenticate(self.s["u_cam"].login, CLAVE)
        html = self.url_open("/marketplace/buy/%s" % lote.uuid_ref).text
        self.assertIn('id="svb_toggle"', html, "fila con interruptor")
        self.assertIn('data-base="40.0"', html, "la tarjeta trae la tarifa propia")
        tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        self.url_open("/marketplace/buy/%s/verify" % lote.uuid_ref, data={
            "csrf_token": tok, "qty": "10", "verification_mode": "platform",
            "verifier_ref": self.s["verif2"].uuid_ref})
        v = self.env["shrimp.verification"].search(
            [("product_id", "=", lote.id)], limit=1)
        self.assertTrue(v)
        self.assertEqual(v.fee, 55.0, "40 + 10 × 1,5")
        self.assertEqual(v.fee_charge_id.amount, 55.0)


@tagged("post_install", "-at_install")
class TestTarifaSensatez(HttpCase):
    """Tarifas por libra que parecen centavos: el perfil las rechaza salvo
    confirmación y la migración 19.0.1.22.0 las divide entre 100."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "tvs")
        sitio = cls.env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False

    def _guardar(self, **extra):
        html = self.url_open("/verifier/profile").text
        tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        data = {"csrf_token": tok, "ver_fee_base": "200", "ver_fee_por_lb": "2"}
        data.update(extra)
        return self.url_open("/verifier/profile/save", data=data)

    def test_perfil_pide_confirmar(self):
        self.authenticate(self.s["u_verif"].login, CLAVE)
        r = self._guardar()
        self.assertIn("error=1", r.url)
        self.assertNotEqual(self.s["verif"].ver_fee_por_lb, 2.0)
        self._guardar(ver_fee_por_lb_confirm="1")
        self.s["verif"].invalidate_recordset()
        self.assertEqual(self.s["verif"].ver_fee_por_lb, 2.0)

    def test_migracion_divide_centavos(self):
        import importlib.util
        import os
        ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "migrations", "19.0.1.22.0", "post-migrate.py")
        spec = importlib.util.spec_from_file_location("mig_tarifa", ruta)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.s["verif"].write({"ver_fee_por_lb": 3.0})
        self.s["verif2"].write({"ver_fee_por_lb": 0.02})
        mod.migrate(self.env.cr, "19.0.1.21.0")
        self.env.invalidate_all()
        self.assertAlmostEqual(self.s["verif"].ver_fee_por_lb, 0.03)
        self.assertAlmostEqual(self.s["verif2"].ver_fee_por_lb, 0.02, msg="la buena no se toca")
        self.assertTrue(self.s["verif"].message_ids.filtered(lambda m: "corregida" in (m.body or "")))
