"""Mi panel (/my/dashboard) de la empacadora y de la camaronera con lo que aporta
shrimp_packer (listas de precios, cosechas ofrecidas, reservas)."""
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios


@tagged("post_install", "-at_install")
class TestPanelPacker(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "ppk")
        cls.emp = cls.env["res.partner"].create({
            "name": "Empacadora ppk", "is_company": True, "email": "emp.ppk@prueba.test",
            "vat_or_id": "0944400019001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora ppk S.A.", "emp_capacidad_lb_dia": 50000})
        cls.u_emp = cls.env["res.users"].create({
            "name": cls.emp.name, "login": "emp.ppk", "partner_id": cls.emp.id, "password": CLAVE,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})

    def test_panel_empacadora(self):
        data = self.env["shrimp.dashboard"]._panel_data(self.emp)
        claves = {k["key"] for k in data["kpis"]}
        self.assertTrue({"purchases_open", "bought_month", "price_lists", "forecasts", "lot_alerts"} <= claves)
        self.assertIn("no_price_list", {a["key"] for a in data["alerts"]})
        urls = {s["url"] for s in data["shortcuts"]}
        self.assertIn("/marketplace/supply", urls)
        self.assertIn("/packer/reservations", urls)
        self.authenticate("emp.ppk", CLAVE)
        r = self.url_open("/my/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mi panel · Empacadora", r.text)
        self.assertIn("Listas de precios vigentes", r.text)
        self.assertNotIn("Publicar lote", r.text, "la empacadora no vende")

    def test_panel_camaronera_con_reservas(self):
        data = self.env["shrimp.dashboard"]._panel_data(self.s["cam"])
        self.assertIn("harvest_forecasts", {k["key"] for k in data["kpis"]})
        urls = [s["url"] for s in data["shortcuts"]]
        self.assertIn("/marketplace/reservations", urls)
        self.assertIn("/marketplace/simulator", urls)
