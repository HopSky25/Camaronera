"""Ajustes › CamaronMarket › Empaque: cuota de la plataforma por libra,
tolerancia por defecto del acta y «Exigir habilitación vigente de la planta
para empacar» (apagado por defecto)."""
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CopackCommon
from .test_copack_empaque_propio import _cosecha, _dar_perfil_maquilador


@tagged("post_install", "-at_install")
class TestAjustesCopacking(CopackCommon):

    def _ajustes(self, **vals):
        conf = self.env["res.config.settings"].create(vals)
        conf.execute()
        return conf

    def test_cuota_y_tolerancia_por_defecto(self):
        conf = self.env["res.config.settings"].create({})
        self.assertAlmostEqual(conf.shrimp_copack_platform_rate, 0.01)
        self.assertAlmostEqual(conf.shrimp_copack_default_tolerance, 0.5)
        self.assertFalse(conf.shrimp_copack_require_license)
        _sol, orden = self.adjudicar(self.solicitud())
        self.assertAlmostEqual(orden.platform_rate_per_lb, 0.01)
        self.assertAlmostEqual(orden.tolerance_pct, 0.5)
        # Cambiadas en Ajustes: las órdenes NUEVAS toman los valores nuevos;
        # la ya creada conserva los suyos.
        self._ajustes(shrimp_copack_platform_rate=0.02, shrimp_copack_default_tolerance=1.5)
        _sol2, nueva = self.adjudicar(self.solicitud())
        self.assertAlmostEqual(nueva.platform_rate_per_lb, 0.02)
        self.assertAlmostEqual(nueva.tolerance_pct, 1.5)
        self.assertAlmostEqual(orden.platform_rate_per_lb, 0.01)
        # La cuota se cobra con la tarifa de la orden.
        nueva.write({"received_lb": 40000})
        nueva.action_register_reception()
        nueva.write({"packed_lb": 39880})
        nueva.action_register_packing()
        self.assertAlmostEqual(nueva.platform_amount, 39880 * 0.02, places=2)
        # Cuota 0 = sin cuota (se guarda como 0, no vuelve a 0,01).
        self._ajustes(shrimp_copack_platform_rate=0.0)
        _sol3, gratis = self.adjudicar(self.solicitud())
        self.assertEqual(gratis.platform_rate_per_lb, 0.0)

    def test_habilitacion_vigente(self):
        ayer = date.today() - timedelta(days=1)
        self.maq.write({"pack_habilitacion_desde": ayer - timedelta(days=365),
                        "pack_habilitacion_hasta": ayer})
        # Apagado (por defecto): como siempre, aunque esté vencida.
        _sol, orden = self.adjudicar(self.solicitud())
        orden.write({"received_lb": 40000})
        orden.action_register_reception()
        self.assertEqual(orden.state, "received")
        # Encendido: vencida bloquea la recepción y el empaque, con el motivo.
        self._ajustes(shrimp_copack_require_license=True)
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param(
            "shrimp_copacking.require_valid_license"), "True")
        orden.write({"packed_lb": 39880})
        with self.assertRaisesRegex(ValidationError, "habilitación venció"):
            orden.action_register_packing()
        _sol2, otra = self.adjudicar(self.solicitud())
        otra.write({"received_lb": 40000})
        with self.assertRaisesRegex(ValidationError, "habilitación"):
            otra.action_register_reception()
        # Sin fecha: también bloquea.
        self.maq.write({"pack_habilitacion_desde": False, "pack_habilitacion_hasta": False})
        with self.assertRaisesRegex(ValidationError, "no tiene registrada"):
            otra.action_register_reception()
        # Vigente: pasa.
        self.maq.write({"pack_habilitacion_desde": ayer,
                        "pack_habilitacion_hasta": date.today() + timedelta(days=180)})
        otra.action_register_reception()
        orden.action_register_packing()
        self.assertEqual(orden.state, "packed")
        # Apagarlo de nuevo vuelve al comportamiento de siempre.
        self._ajustes(shrimp_copack_require_license=False)
        self.maq.write({"pack_habilitacion_hasta": ayer})
        otra.write({"packed_lb": 39000})
        otra.action_register_packing()
        self.assertEqual(otra.state, "packed")

    def test_habilitacion_en_empaque_propio(self):
        _dar_perfil_maquilador(self.cli)
        cosecha = _cosecha(self.env, self.cli)
        self._ajustes(shrimp_copack_require_license=True)
        orden = self.env["shrimp.copack.order"].shrimp_create_self_packing(
            self.cli, "p:%s" % cosecha.uuid_ref, 1000.0)
        orden.write({"received_lb": 1000.0})
        with self.assertRaisesRegex(ValidationError, "habilitación"):
            orden.action_register_reception()
        self.cli.write({"pack_habilitacion_desde": date.today() - timedelta(days=10),
                        "pack_habilitacion_hasta": date.today() + timedelta(days=100)})
        orden.action_register_reception()
        self.assertEqual(orden.state, "received")
