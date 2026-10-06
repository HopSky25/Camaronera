"""Ajustes › CamaronMarket › Verificación: cada parámetro cambia el
comportamiento (plazo de aceptación, fórmula del honorario, tolerancia de
peso, margen de la plataforma y margen al deshacer)."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .common_seguridad import abrir_ronda, montar_verificacion


@tagged("post_install", "-at_install")
class TestAjustesVerificacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "6fv")

    def _ajustes(self, **vals):
        conf = self.env["res.config.settings"].create(vals)
        conf.execute()
        return conf

    def test_valores_de_siempre(self):
        conf = self.env["res.config.settings"].create({})
        self.assertEqual(conf.shrimp_verification_acceptance_hours, 48)
        self.assertAlmostEqual(conf.shrimp_verification_weight_tolerance_pct, 2.0)
        self.assertAlmostEqual(conf.shrimp_verification_margin_pct, 15.0)
        self.assertAlmostEqual(conf.shrimp_verification_fee_base, 250.0)
        self.assertAlmostEqual(conf.shrimp_verification_fee_min, 300.0)
        self.assertAlmostEqual(conf.shrimp_verification_fee_max, 800.0)
        self.assertEqual(conf.shrimp_signoff_undo_margin_minutes, 60)

    def test_plazo_de_aceptacion(self):
        ahora = fields.Datetime.now()
        abrir_ronda(self.v)
        self.v._abrir_plazo()
        self.assertAlmostEqual((self.v.acceptance_deadline - ahora).total_seconds() / 3600, 48, delta=0.1)
        self._ajustes(shrimp_verification_acceptance_hours=72)
        self.v._abrir_plazo()
        self.assertAlmostEqual((self.v.acceptance_deadline - ahora).total_seconds() / 3600, 72, delta=0.1)
        with self.assertRaises(ValidationError):
            self._ajustes(shrimp_verification_acceptance_hours=0)

    def test_formula_del_honorario(self):
        Fee = self.env["shrimp.verification.fee"]
        self.assertEqual(Fee.compute(10000), 450.0)      # 250 + 10000 × 2 ¢
        self.assertEqual(Fee.compute(100), 300.0)        # mínimo
        self.assertEqual(Fee.compute(100000), 800.0)     # tope
        # Sin mínimo ni tope (0 se guarda como 0, no vuelve a 300 / 800).
        self._ajustes(shrimp_verification_fee_base=100.0, shrimp_verification_fee_cents=1.0,
                      shrimp_verification_fee_min=0.0, shrimp_verification_fee_max=0.0)
        self.assertEqual(Fee.compute(100), 101.0)
        self.assertEqual(Fee.compute(100000), 1100.0)
        self.assertEqual(Fee.desglose(1000)["base"], 100.0)
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param(
            "shrimp_verification.fee_min"), "0.0")
        with self.assertRaises(ValidationError):
            self._ajustes(shrimp_verification_fee_min=500.0, shrimp_verification_fee_max=400.0)

    def test_tolerancia_de_peso(self):
        v = self.env["shrimp.verification"].new({
            "scope": "adult", "state": "approved",
            "weight_sent_lb": 100.0, "weight_plant_lb": 97.0})
        cumple, motivos = v.cumple_lo_publicado()
        self.assertFalse(cumple, "Falta 3 % con tolerancia de 2 %")
        self._ajustes(shrimp_verification_weight_tolerance_pct=5.0)
        cumple, motivos = v.cumple_lo_publicado()
        self.assertTrue(cumple, motivos)

    def test_margen_y_deshacer(self):
        Acc = self.env["shrimp.verification.acceptance"]
        self.assertEqual(Acc._signoff_margin_minutes(), 60)
        self._ajustes(shrimp_verification_margin_pct=20.0, shrimp_signoff_undo_margin_minutes=120)
        self.assertAlmostEqual(self.v._margen_configurado(), 20.0)
        self.assertEqual(Acc._signoff_margin_minutes(), 120)
        # El margen al deshacer corre el plazo hasta ahora + 120 min.
        abrir_ronda(self.v)
        comprador = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        comprador.sudo().action_reject(reason="No coincide", actor=self.s["cam"])
        self.v.sudo().acceptance_deadline = fields.Datetime.now() + timedelta(minutes=5)
        comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        self.assertGreaterEqual(self.v.acceptance_deadline,
                                fields.Datetime.now() + timedelta(minutes=119))
