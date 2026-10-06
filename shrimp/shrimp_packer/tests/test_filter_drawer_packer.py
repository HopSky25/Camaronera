"""Cajón de filtros en el directorio de empacadoras y en el comparador.

Directorio (/marketplace/packers, público): búsqueda + certificaciones,
mercados y ubicación; conteo en vivo; etiquetas que conservan el resto.
Comparador (/marketplace/price-lists/compare, camaronera): «Qué
comparar» en la barra, empacadoras/cantidad/fecha en el cajón («Aplicar»).
"""
import json

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios


@tagged("post_install", "-at_install")
class TestFilterDrawerPacker(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "fdpk")
        Socio = env["res.partner"]
        cls.e1 = Socio.create({
            "name": "FDPK Empacadora Uno", "is_company": True, "email": "e1.fdpk@prueba.test",
            "vat_or_id": "0922700001001", "shrimp_user_type": "empacadora",
            "shrimp_ubicacion": "Durán", "emp_cert_bap": True, "emp_mercado_asia": True,
            "shrimp_capacity_value": 1000})
        cls.e2 = Socio.create({
            "name": "FDPK Empacadora Dos", "is_company": True, "email": "e2.fdpk@prueba.test",
            "vat_or_id": "0922700002001", "shrimp_user_type": "empacadora",
            "shrimp_ubicacion": "Machala", "emp_cert_haccp": True,
            "shrimp_capacity_value": 5000})
        talla = env.ref("shrimp_marketplace.size_entero_3040")
        for emp, precio in ((cls.e1, 4.40), (cls.e2, 4.60)):
            lista = env["shrimp.price.list"].create({
                "name": "Lista %s" % emp.name, "issuer_partner_id": emp.id,
                "recipient_ids": [(6, 0, cls.s["cam"].ids)],
                "line_ids": [(0, 0, {"size_grade_id": talla.id, "uom": "kg",
                                     "quality": "ab", "price": precio})],
            })
            lista.action_publish()

    def _count(self, url):
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200, url)
        return json.loads(r.text)["count"]

    def test_directorio_publico_con_cajon(self):
        r = self.url_open("/marketplace/packers?q=FDPK")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn('id="sFilterDrawer"', html)
        self.assertIn('id="sf_sec_cert"', html)
        self.assertIn('id="sf_sec_location"', html)
        self.assertIn('data-count-url="/marketplace/packers/count"', html)
        self.assertIn("FDPK Empacadora Uno", html)
        self.assertIn("FDPK Empacadora Dos", html)

    def test_directorio_conteo(self):
        self.assertEqual(self._count("/marketplace/packers/count?q=FDPK"), 2)
        self.assertEqual(self._count("/marketplace/packers/count?q=FDPK&cert=bap"), 1)
        self.assertEqual(self._count("/marketplace/packers/count?q=FDPK&cert=bap&cert=haccp"), 0)
        self.assertEqual(self._count("/marketplace/packers/count?q=FDPK&mercado=asia"), 1)
        self.assertEqual(self._count("/marketplace/packers/count?q=FDPK&ubicacion=Machala"), 1)
        # Un valor desconocido se ignora.
        self.assertEqual(self._count("/marketplace/packers/count?q=FDPK&cert=iso"), 2)

    def test_directorio_etiquetas_y_orden(self):
        html = self.url_open(
            "/marketplace/packers?q=FDPK&cert=bap&ubicacion=Dur%C3%A1n&orden=capacidad").text
        self.assertIn("Certificación: BAP", html)
        self.assertIn("Ubicación: Durán", html)
        self.assertIn('aria-label="Filtros, 2 activos"', html)
        # Quitar la certificación conserva ubicación, búsqueda y orden.
        self.assertIn('href="/marketplace/packers?ubicacion=Dur%C3%A1n&amp;q=FDPK&amp;orden=capacidad#listado"', html)
        self.assertNotIn("FDPK Empacadora Dos", html)
        # Orden por capacidad: la de 5000 lb/día primero.
        html = self.url_open("/marketplace/packers?q=FDPK&orden=capacidad").text
        self.assertLess(html.index("FDPK Empacadora Dos"), html.index("FDPK Empacadora Uno"))

    def test_comparador_con_cajon(self):
        self.authenticate(self.s["u_cam"].login, CLAVE)
        r = self.url_open("/marketplace/price-lists/compare?cantidad=5000")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn('id="sFilterDrawer"', html)
        self.assertIn('id="pkCombo"', html)
        self.assertIn('id="sf_sec_emp"', html)
        self.assertIn('id="sf_sec_cantidad"', html)
        self.assertIn("Cantidad: 5,000", html)
        # Sin conteo en vivo: el resultado es una tabla, el botón dice «Aplicar».
        self.assertNotIn("data-count-url", html)
        self.assertIn(">Aplicar</button>", html)
        # Elegir una sola empacadora sigue funcionando por la URL de siempre.
        r = self.url_open("/marketplace/price-lists/compare?filtrar=1&emp=%s" % self.e2.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Empacadora: FDPK Empacadora Dos", r.text)

    def test_comparador_requiere_sesion(self):
        r = self.url_open("/marketplace/price-lists/compare", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
