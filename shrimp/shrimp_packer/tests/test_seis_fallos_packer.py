"""Empacadora: «Mi ficha pública» (fallo 2) y Ajustes de reservas de cosecha
y del ranking de proveedores."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE

from .test_reserva_deshacer import cosecha_fuera_de_banda, montar_reserva


@tagged("post_install", "-at_install")
class TestFichaPublicaEmpacadora(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = montar_reserva(cls.env, "6fp", "0933396001001")

    def test_2_mi_ficha_publica_no_da_404(self):
        emp = self.s["emp"]
        tree = self.env["website"]._shrimp_nav_tree(emp)["sections"]
        enlace = [i for i in tree["account"]["items"] if i["key"] == "packer_profile"]
        self.assertTrue(enlace, "La empacadora tiene «Mi ficha pública»")
        url = enlace[0]["url"]
        self.assertEqual(url, "/marketplace/packers/%s" % emp.uuid_ref)
        self.authenticate("emp.6fp", CLAVE)
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn(emp.name, r.text)
        # La vitrina de vendedor no es su ficha: una empacadora pura no vende.
        self.assertEqual(self.url_open("/marketplace/sellers/%s" % emp.uuid_ref).status_code, 404)


@tagged("post_install", "-at_install")
class TestAjustesPacker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = montar_reserva(cls.env, "6fq", "0933396002001")
        cls.talla = cls.env.ref("shrimp_marketplace.size_entero_3040")

    def _ajustes(self, **vals):
        conf = self.env["res.config.settings"].create(vals)
        conf.execute()
        return conf

    def test_ventana_para_deshacer_rechazo(self):
        conf = self.env["res.config.settings"].create({})
        self.assertEqual(conf.shrimp_signoff_undo_minutes, 15)
        cm = cosecha_fuera_de_banda(self.env, self.s, self.talla, "6fq1")
        f_emp = cm.confirmation_ids.filtered(lambda c: c.role == "packer")
        f_emp.action_reject(motivo="Talla chica", actor=self.s["emp"])
        f_emp.sudo().write({"decided_at": fields.Datetime.now() - timedelta(minutes=20)})
        self.assertFalse(f_emp.signoff_can_undo(actor=self.s["emp"]))
        # Ampliada desde Ajustes, sí se puede deshacer.
        self._ajustes(shrimp_signoff_undo_minutes=30)
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param(
            "shrimp.signoff_undo_minutes"), "30")
        self.assertTrue(f_emp.signoff_can_undo(actor=self.s["emp"]))
        # 0 = no se puede deshacer (se guarda como 0, no vuelve a 15).
        self._ajustes(shrimp_signoff_undo_minutes=0)
        f_emp.sudo().write({"decided_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.assertFalse(f_emp.signoff_can_undo(actor=self.s["emp"]))
        with self.assertRaises(UserError):
            f_emp.action_signoff_undo(actor=self.s["emp"])

    def test_ranking_de_proveedores(self):
        R = self.env["shrimp.proveedor.ranking"]
        conf = self.env["res.config.settings"].create({})
        self.assertEqual((conf.shrimp_ranking_min_lots, conf.shrimp_ranking_weight_yield,
                          conf.shrimp_ranking_weight_class_a, conf.shrimp_ranking_weight_compliance),
                         (3, 45, 25, 30))
        r = R.ranking(self.s["emp"])
        self.assertEqual(r["umbral"], 3)
        self.assertEqual(r["pesos"], {"rendimiento": 45, "clase_a": 25, "cumplimiento": 30})
        # Los pesos tienen que sumar 100.
        with self.assertRaises(ValidationError):
            self._ajustes(shrimp_ranking_weight_yield=50)
        self._ajustes(shrimp_ranking_min_lots=5, shrimp_ranking_weight_yield=60,
                      shrimp_ranking_weight_class_a=10, shrimp_ranking_weight_compliance=30)
        r = R.ranking(self.s["emp"])
        self.assertEqual(r["umbral"], 5)
        self.assertEqual(r["pesos"], {"rendimiento": 60, "clase_a": 10, "cumplimiento": 30})
        self.assertEqual(R.historial(self.s["cam"])["faltan"], 5)
        # El puntaje usa los pesos configurados.
        self.assertEqual(R._pesos(), (0.6, 0.1, 0.3))
        # Un parámetro roto (no suman 100) vuelve a los de siempre.
        self.env["ir.config_parameter"].sudo().set_param("shrimp_packer.ranking_weight_yield", "90")
        self.assertEqual(R._pesos(), (0.45, 0.25, 0.30))
