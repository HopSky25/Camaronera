"""Tablero del mejor proveedor (portada y «Mi panel») y cálculo del ranking."""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios


def _fila(proveedor, lotes=4, suficiente=True, rendimiento=70.0, clase_a=80.0, puntaje=75.0):
    return {
        "proveedor": proveedor, "lotes": lotes, "suficiente": suficiente,
        "rendimiento": rendimiento, "rendimiento_n": lotes if rendimiento is not None else 0,
        "rendimiento_min": rendimiento, "rendimiento_max": rendimiento,
        "clase_a": clase_a, "clase_a_n": lotes if clase_a is not None else 0,
        "metabisulfito": None, "metabisulfito_n": 0, "metabisulfito_fail": 0,
        "incumplidos": 0, "cumplimiento": 100.0, "lb_verificadas": 1000.0 * lotes,
        "ultima": False, "puntaje": puntaje, "ofertas": 0, "detalles": [],
    }


@tagged("post_install", "-at_install")
class TestDashboardProveedores(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "dbp")
        Socio = cls.env["res.partner"]
        cls.emp = Socio.create({
            "name": "Empacadora dbp", "is_company": True, "email": "emp.dbp@prueba.test",
            "vat_or_id": "0944400029001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora dbp S.A.", "emp_capacidad_lb_dia": 50000,
            "shrimp_account_state": "approved"})
        cls.env["res.users"].create({
            "name": cls.emp.name, "login": "emp.dbp", "partner_id": cls.emp.id, "password": CLAVE,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        cls.prov_sin_rend = Socio.create({"name": "Prov sin rendimiento dbp", "is_company": True})
        cls.prov_bueno = Socio.create({"name": "Prov bueno dbp", "is_company": True})
        cls.R = cls.env["shrimp.proveedor.ranking"]

    def _ranking_falso(self, filas):
        return {"filas": filas, "orden": "puntaje", "umbral": 3, "proveedores": len(filas),
                "con_muestra": len([f for f in filas if f["suficiente"]]), "lotes": 10,
                "lb": 0.0, "incumplidos": 0, "en_curso": 0,
                "pesos": {"rendimiento": 45, "clase_a": 25, "cumplimiento": 30}}

    # ------------------------------------------------------------ M2
    def test_lider_necesita_puntaje(self):
        """Un proveedor con muestra pero sin rendimiento medido no encabeza."""
        filas = [_fila(self.prov_sin_rend, rendimiento=None, clase_a=None, puntaje=None),
                 _fila(self.prov_bueno)]
        cls_r = type(self.R)
        with patch.object(cls_r, "ranking", lambda s, e, orden="puntaje": self._ranking_falso(filas)), \
                patch.object(cls_r, "por_talla", lambda s, e, p: {"filas": [], "total_lb": 0.0,
                                                                   "mejor": None, "lotes": 0}):
            dash = self.R.dashboard_empacadora(self.emp)
            self.assertEqual(dash["lider"]["proveedor"], self.prov_bueno)
            self.assertNotIn(dash["lider"], dash["resto"])
            # Solo el que no tiene puntaje: no hay tablero.
            filas[:] = [_fila(self.prov_sin_rend, rendimiento=None, clase_a=None, puntaje=None)]
            self.assertIsNone(self.R.dashboard_empacadora(self.emp))

    def test_clase_a_sin_medir_no_cuenta_como_cero(self):
        pesos = (0.45, 0.25, 0.30)
        con_cero = self.R._puntaje(pesos, 70.0, 0.0, 100.0)
        sin_medir = self.R._puntaje(pesos, 70.0, None, 100.0)
        self.assertAlmostEqual(sin_medir, (0.45 * 70 + 0.30 * 100) / 0.75)
        self.assertGreater(sin_medir, con_cero)
        self.assertIsNone(self.R._puntaje(pesos, None, 80.0, 100.0))

    def test_ofertas_en_una_consulta(self):
        ofertas = self.R._ofertas_por_proveedor(self.prov_bueno | self.prov_sin_rend)
        self.assertEqual(ofertas.get(self.prov_bueno.id, 0), 0)
        self.assertEqual(self.R._ofertas_por_proveedor(self.env["res.partner"]), {})

    # ------------------------------------------------------------ M3
    def test_regla_de_acceso(self):
        cls_r = type(self.R)
        with patch.object(cls_r, "dashboard_empacadora", lambda s, e: {"ok": e.id}):
            self.assertEqual(self.R.dashboard_para(self.emp), {"ok": self.emp.id})
            self.assertIsNone(self.R.dashboard_para(self.s["cam"]))
            self.emp.shrimp_account_state = "pending"
            self.assertIsNone(self.R.dashboard_para(self.emp))
            self.emp.shrimp_account_state = "approved"

    def test_portada_y_panel_con_proveedor_sin_rendimiento(self):
        """La portada y «Mi panel» pintan el líder con rutas en inglés y no
        revientan con un proveedor sin rendimiento ni clase A en el resto."""
        lider = _fila(self.prov_bueno, clase_a=None)
        resto = [_fila(self.prov_sin_rend, suficiente=False, rendimiento=65.0, clase_a=None,
                       puntaje=None)]
        dash = {"r": self._ranking_falso([lider] + resto), "lider": lider, "resto": resto,
                "tallas": {"filas": [], "total_lb": 0.0, "mejor": None, "lotes": 0}}
        cls_r = type(self.R)
        with patch.object(cls_r, "dashboard_empacadora", lambda s, e: dash):
            self.authenticate("emp.dbp", CLAVE)
            portada = self.url_open("/")
            self.assertEqual(portada.status_code, 200)
            self.assertIn("Tu mejor proveedor", portada.text)
            self.assertIn("/marketplace/sellers/%s" % self.prov_bueno.uuid_ref, portada.text)
            self.assertIn('href="/marketplace/suppliers"', portada.text)
            self.assertNotIn("/marketplace/vendedor/", portada.text)
            self.assertNotIn("/marketplace/proveedores", portada.text)
            panel = self.url_open("/my/dashboard")
            self.assertEqual(panel.status_code, 200)
            self.assertIn("Tu mejor proveedor verificado", panel.text)
            self.assertIn("Prov bueno dbp", panel.text)
            self.assertIn("muestra corta", panel.text)
        # Una camaronera no ve el bloque.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        self.assertNotIn("Tu mejor proveedor", self.url_open("/").text)

    def test_aquagold_demo(self):
        """Con la demo, AQUAGOLD tiene historial con varios proveedores."""
        aquagold = self.env.ref("shrimp_packer.demo_pk_aquagold", raise_if_not_found=False)
        if not aquagold or not self.env.ref("shrimp_packer.demo_ag_ver_0001", raise_if_not_found=False):
            self.skipTest("sin la demo de AQUAGOLD")
        r = self.R.ranking(aquagold)
        self.assertGreaterEqual(r["proveedores"], 4)
        self.assertGreaterEqual(r["con_muestra"], 3)
        dash = self.R.dashboard_para(aquagold)
        self.assertTrue(dash and dash["lider"]["puntaje"] is not None)
