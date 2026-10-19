"""Perfiles por sitio.

Desde el marketplace NO se registra, no se agrega, no se activa ni se usa el
perfil Verificadora ni el de Maquilador (planta de maquila): cada uno vive en
su sitio (CamaronMarket Verificadores / CamaronMarket Empaque). La
empacadora sigue en el marketplace. Las redirecciones entre sitios solo se
comprueban por la cabecera Location (nunca se siguen).
"""
import re

from lxml import html as lxml_html

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_user_registry.tests.legal_utils import signup_post

CLAVE = "Clave-sitios-2026"
DOM_VERIF = "http://verif-sitios.example.test"
DOM_EMPAQUE = "http://empaque-sitios.example.test"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto) or \
        re.search(r'value="([^"]+)"\s+name="csrf_token"', texto)
    return m.group(1) if m else ""


@tagged("post_install", "-at_install")
class TestPerfilesPorSitio(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        W = cls.env["website"].sudo()
        cls.principal = W._shrimp_main_site()
        cls.sitio_verif = W._shrimp_platform_site("verifier")
        cls.sitio_emp = W._shrimp_platform_site("copacker")
        portal = cls.env.ref("base.group_portal")
        Socio = cls.env["res.partner"]

        def usuario(socio, login):
            return cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})

        # Camaronera + maquilador aprobado (activo: maquilador, como quien
        # viene del sitio de empaque).
        cls.multi = Socio.create({
            "name": "Camaronera multisitio", "is_company": True,
            "email": "multi.sitios@prueba.test", "vat_or_id": "0955800011001",
            "shrimp_user_type": "camaronera",
            "farm_razon_social": "Camaronera multisitio S.A.",
            "farm_representante": "Rep", "farm_telefono": "04-0000000",
            "farm_ubicacion": "Guayas", "shrimp_ubicacion": "Guayas",
            "pack_codigo_establecimiento": "EST-SIT-1"})
        cls.env["shrimp.partner.role"].sudo().create({
            "partner_id": cls.multi.id, "role": "maquilador", "state": "approved"})
        cls.u_multi = usuario(cls.multi, "multi.sitios")
        # Solo verificadora.
        cls.verif = Socio.create({
            "name": "Verificadora multisitio", "is_company": True,
            "shrimp_user_type": "verificador", "vat_or_id": "0955800021001",
            "email": "verif.sitios@prueba.test", "ver_bank_name": "Banco",
            "ver_bank_account_number": "2100548837"})
        cls.u_verif = usuario(cls.verif, "verif.sitios")
        # Solo camaronera.
        cls.cam = Socio.create({
            "name": "Camaronera sola sitios", "is_company": True,
            "email": "cam.sitios@prueba.test", "vat_or_id": "0955800031001",
            "shrimp_user_type": "camaronera",
            "farm_razon_social": "Camaronera sola S.A.",
            "farm_representante": "Rep", "farm_telefono": "04-0000000",
            "farm_ubicacion": "Guayas"})
        cls.u_cam = usuario(cls.cam, "cam.sitios")

    # ------------------------------------------------------------------
    def _usar(self, sitio):
        """Apunta `sitio` a la URL de los tests; los otros dos, a dominios
        ficticios (verificadores y empaque con dominio conocido)."""
        if not sitio:
            self.skipTest("falta el sitio")
        for otra in self.env["website"].sudo().search([("id", "!=", sitio.id)]):
            if otra == self.sitio_verif:
                otra.domain = DOM_VERIF
            elif otra == self.sitio_emp:
                otra.domain = DOM_EMPAQUE
            else:
                otra.domain = "http://otro-%s.invalid" % otra.id
        self.env.flush_all()
        sitio.domain = self.base_url().rstrip("/")
        self.env.flush_all()
        self.env.registry.clear_cache()
        return sitio

    def _activo(self, socio):
        """Perfil activo del USUARIO de la cuenta (por usuario desde CR-12)."""
        self.env.invalidate_all()
        user = self.env["res.users"].sudo().search([("partner_id", "=", socio.id)], limit=1)
        return user.shrimp_active_role

    # ------------------------------------------------------------------
    # 1) Alta
    # ------------------------------------------------------------------
    def test_alta_principal_sin_verificadora_ni_maquilador(self):
        self._usar(self.principal)
        html = self.url_open("/register").text
        codigos = re.findall(r'name="shrimp_user_types"[^>]*value="([a-z_]+)"', html)
        self.assertIn("camaronera", codigos)
        self.assertIn("empacadora", codigos)
        self.assertNotIn("verificador", codigos)
        self.assertNotIn("maquilador", codigos)
        self.assertNotIn('id="section_verificador"', html)
        # Aviso con el enlace al alta de cada sitio.
        self.assertIn(DOM_VERIF + "/register/verifier", html)
        self.assertIn(DOM_EMPAQUE + "/register/copacker", html)
        # ?profile=verificador no lo preselecciona (no existe aquí).
        html = self.url_open("/register?profile=verificador").text
        self.assertNotRegex(html, r'value="verificador"')

    def test_alta_principal_rechaza_verificadora_y_maquilador(self):
        self._usar(self.principal)
        for roles, correo in ((["verificador"], "x.ver.sitios@prueba.test"),
                              (["camaronera", "maquilador"], "x.maq.sitios@prueba.test")):
            with self.subTest(roles=roles):
                html = self.url_open("/register").text
                datos = signup_post(self.env, roles, correo, csrf_token=_csrf(html))
                r = self.url_open("/register/submit", data=datos, allow_redirects=False)
                self.assertEqual(r.status_code, 200)
                self.assertIn("se usa en su propio sitio", r.text)
                self.assertFalse(self.env["res.partner"].sudo().search([("email", "=", correo)]))

    def test_register_verifier_y_copacker_desde_el_principal_redirigen(self):
        self._usar(self.principal)
        r = self.url_open("/register/verifier", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(r.headers["Location"], DOM_VERIF + "/register/verifier")
        r = self.url_open("/register/copacker", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(r.headers["Location"], DOM_EMPAQUE + "/register/copacker")

    def test_register_sin_dominio_vuelve_al_alta_del_sitio(self):
        self._usar(self.principal)
        self.sitio_verif.domain = False
        self.env.flush_all()
        r = self.url_open("/register/verifier", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertTrue(r.headers["Location"].endswith("/register"), r.headers["Location"])

    def test_alta_en_cada_sitio_sigue_igual(self):
        self._usar(self.sitio_emp)
        html = self.url_open("/register/copacker").text
        self.assertEqual(re.findall(r'name="shrimp_user_types"[^>]*value="([a-z_]+)"', html), ["maquilador"])
        self.assertRegex(html, r'value="maquilador"[^>]*checked')
        self.assertNotIn("shrimp-signup-other-sites", html)
        self._usar(self.sitio_verif)
        html = self.url_open("/register/verifier").text
        self.assertEqual(re.findall(r'name="shrimp_user_types"[^>]*value="([a-z_]+)"', html), ["verificador"])
        self.assertRegex(html, r'value="verificador"[^>]*checked')
        self.assertNotIn("shrimp-signup-other-sites", html)

    # ------------------------------------------------------------------
    # 2) Agregar perfil (Mi cuenta)
    # ------------------------------------------------------------------
    def test_mi_cuenta_principal_enlaza_en_vez_de_agregar(self):
        self._usar(self.principal)
        self.authenticate("cam.sitios", CLAVE)
        r = self.url_open("/marketplace/my-account")
        self.assertEqual(r.status_code, 200)
        doc = lxml_html.fromstring(r.text)
        opciones = doc.xpath("//select[@id='shrimpAddRole']/option/@value")
        self.assertIn("empacadora", opciones)
        self.assertNotIn("verificador", opciones)
        self.assertNotIn("maquilador", opciones)
        enlaces = {li.get("data-role"): li.xpath(".//a/@href")[0]
                   for li in doc.xpath("//div[contains(@class,'shrimp-offsite-add')]//li")}
        self.assertTrue(enlaces["verificador"].startswith(DOM_VERIF + "/web/login"))
        self.assertTrue(enlaces["maquilador"].startswith(DOM_EMPAQUE + "/web/login"))
        # El servidor tampoco lo agrega aunque se falsifique el POST.
        for rol in ("maquilador", "verificador"):
            r = self.url_open("/my/profile/add", data={
                "csrf_token": _csrf(r.text) or _csrf(self.url_open("/marketplace/my-account").text),
                "role": rol, "next": "/marketplace/my-account",
                "shrimp_razon_social": "X S.A.", "shrimp_ubicacion": "Guayas"},
                allow_redirects=False)
            self.assertIn(r.status_code, (302, 303))
            self.assertIn("error=", r.headers["Location"])
            self.assertNotIn(rol, self.cam._shrimp_roles(include_pending=True))

    def test_mi_cuenta_sitio_de_empaque_agrega_maquilador(self):
        self._usar(self.sitio_emp)
        self.authenticate("cam.sitios", CLAVE)
        r = self.url_open("/marketplace/my-account")
        self.assertEqual(r.status_code, 200)
        opciones = lxml_html.fromstring(r.text).xpath("//select[@id='shrimpAddRole']/option/@value")
        self.assertIn("maquilador", opciones)
        self.assertNotIn("empacadora", opciones)

    # ------------------------------------------------------------------
    # 3) Selector de perfil y perfil activo
    # ------------------------------------------------------------------
    def test_principal_vuelve_a_un_perfil_del_marketplace(self):
        self.u_multi.sudo().shrimp_active_profile = "maquilador"
        self._usar(self.principal)
        self.authenticate("multi.sitios", CLAVE)
        r = self.url_open("/marketplace")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._activo(self.multi), "camaronera")
        doc = lxml_html.fromstring(r.text)
        # (la barra se pinta dos veces: escritorio y móvil)
        opciones = set(doc.xpath("//li[contains(@class,'shrimp-profile-switch')]//button/@data-role"))
        self.assertEqual(opciones, {"camaronera"})
        fuera = doc.xpath("//li[contains(@class,'shrimp-profile-switch')]//a[contains(@class,'shrimp-profile-offsite')]")
        self.assertEqual({a.get("data-role") for a in fuera}, {"maquilador"})
        self.assertTrue(fuera[0].get("href").startswith(DOM_EMPAQUE))
        # Activarlo aquí a mano (POST falsificado) tampoco vale.
        r = self.url_open("/my/profile/activate", data={
            "csrf_token": _csrf(r.text), "role": "maquilador", "next": "/marketplace"},
            allow_redirects=False)
        self.assertIn("error=", r.headers["Location"])
        self.assertEqual(self._activo(self.multi), "camaronera")

    def test_sitio_de_empaque_activa_el_maquilador(self):
        self.u_multi.sudo().shrimp_active_profile = "camaronera"
        self._usar(self.sitio_emp)
        self.authenticate("multi.sitios", CLAVE)
        r = self.url_open("/my")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._activo(self.multi), "maquilador")
        doc = lxml_html.fromstring(r.text)
        opciones = set(doc.xpath("//li[contains(@class,'shrimp-profile-switch')]//button/@data-role"))
        self.assertEqual(opciones, {"maquilador"})
        # Al volver al marketplace, su último perfil del marketplace.
        self._usar(self.principal)
        self.url_open("/marketplace")
        self.assertEqual(self._activo(self.multi), "camaronera")

    def test_solo_verificadora_en_el_principal(self):
        self._usar(self.principal)
        self.assertEqual(self.principal._shrimp_login_landing(self.u_verif), "/my/site-profile")
        self.assertEqual(self.principal._shrimp_login_landing(self.u_cam), "/my/dashboard")
        self.authenticate("verif.sitios", CLAVE)
        r = self.url_open("/my/dashboard", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertTrue(r.headers["Location"].endswith("/my/site-profile"))
        r = self.url_open("/my/site-profile")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Este perfil se usa en su sitio", r.text)
        self.assertIn(DOM_VERIF + "/web/login", r.text)
        # El perfil activo no cambia (no tiene otro).
        self.assertEqual(self._activo(self.verif), "verificador")
        # La barra no ofrece «Actuar como verificadora».
        doc = lxml_html.fromstring(r.text)
        self.assertFalse(doc.xpath("//li[contains(@class,'shrimp-profile-switch')]//button[@data-role='verificador']"))

    def test_llegada_del_maquilador_con_dos_perfiles(self):
        self.u_multi.sudo().shrimp_active_profile = "maquilador"
        self.assertEqual(self.principal._shrimp_login_landing(self.u_multi), "/my/dashboard")
        self.assertEqual(self._activo(self.multi), "camaronera")
        if self.sitio_emp:
            self.assertEqual(self.sitio_emp._shrimp_login_landing(self.u_multi), "/copacker/inbox")
            self.assertEqual(self._activo(self.multi), "maquilador")
