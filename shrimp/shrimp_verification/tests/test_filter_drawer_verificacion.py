"""Cajón de filtros en la bandeja del verificador.

El admin de la empresa ve «Estado» y «Técnico» (reparte el trabajo); el
técnico solo «Estado» (su bandeja ya es solo suya y ?tech= se ignora). El
conteo en vivo usa el mismo dominio que la bandeja.
"""
import json

from odoo.tests import HttpCase, tagged

from .common_seguridad import CLAVE, montar_verificacion


@tagged("post_install", "-at_install")
class TestFilterDrawerVerificacion(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s, cls.verif = montar_verificacion(env, "fdv")
        cls.tec2 = env["res.partner"].create({
            "name": "Técnico dos fdv", "parent_id": cls.s["verif"].id,
            "shrimp_is_field_tech": True, "email": "tec2.fdv@prueba.test"})
        cls.verif.sudo().write({"technician_partner_id": cls.s["tec"].id})
        # Sin dominio propio el sitio de verificadores no redirige.
        sitio = env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False

    def _count(self, url):
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200, url)
        return json.loads(r.text)["count"]

    def test_admin_ve_estado_y_tecnico(self):
        self.authenticate(self.s["u_verif"].login, CLAVE)
        r = self.url_open("/verifier/inbox")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn('id="sFilterDrawer"', html)
        self.assertIn('id="sf_sec_tech"', html)
        self.assertNotIn("s-filters-card", html)
        self.assertIn('data-count-url="/verifier/inbox/count"', html)
        self.assertEqual(self._count("/verifier/inbox/count"), 1)
        self.assertEqual(self._count("/verifier/inbox/count?tech=%s" % self.s["tec"].uuid_ref), 1)
        self.assertEqual(self._count("/verifier/inbox/count?tech=%s" % self.tec2.uuid_ref), 0)
        # Un técnico de otra empresa no filtra nada propio.
        self.assertEqual(self._count("/verifier/inbox/count?tech=%s" % self.s["tec_ajeno"].uuid_ref), 0)
        html = self.url_open("/verifier/inbox?q=VER&tech=%s" % self.tec2.uuid_ref).text
        self.assertIn("Técnico: Técnico dos fdv", html)
        self.assertIn('href="/verifier/inbox?q=VER#listado"', html)

    def test_tecnico_sin_filtro_de_tecnico(self):
        self.authenticate(self.s["u_tec"].login, CLAVE)
        r = self.url_open("/verifier/inbox")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('id="sf_sec_tech"', r.text)
        # ?tech= se ignora: sigue viendo lo suyo.
        self.assertEqual(self._count("/verifier/inbox/count?tech=%s" % self.tec2.uuid_ref), 1)

    def test_bandeja_estados_y_acceso(self):
        self.authenticate(self.s["u_verif"].login, CLAVE)
        estado = self.verif.state
        self.assertEqual(self._count("/verifier/inbox/count?state=%s" % estado), 1)
        self.assertEqual(self._count("/verifier/inbox/count?state=rejected"), 0)
        # Un estado inventado se ignora.
        self.assertEqual(self._count("/verifier/inbox/count?state=xyz"), 1)
        # Quien no es verificador no tiene bandeja ni conteo.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        self.assertEqual(self.url_open("/verifier/inbox/count").status_code, 403)
        self.authenticate(None, None)
        r = self.url_open("/verifier/inbox/count", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
