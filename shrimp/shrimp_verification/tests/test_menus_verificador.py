"""El menú del sitio de verificadores, su panel (/my) y «Servicios ▾ >
Verificación» del marketplace salen de UNA lista
(website._shrimp_verifier_jobs / _shrimp_verifier_company_links).

Antes había tres: «Por iniciar» (menú), «Compras por verificar»
?state=assigned (panel y marketplace) y «Verificaciones abiertas»
?state=open (hijos guardados del menú).
"""
from lxml import html as lxml_html

from odoo.tests import HttpCase, tagged

CLAVE = "Clave-de-prueba-123"


@tagged("post_install", "-at_install")
class TestMenusVerificador(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        portal = cls.env.ref("base.group_portal")
        cls.empresa = cls.env["res.partner"].create({
            "name": "Verificadora menús", "is_company": True,
            "shrimp_user_type": "verificador", "vat_or_id": "0955700091001",
            "email": "verif.menus@prueba.test", "ver_bank_name": "Banco",
            "ver_bank_account_number": "2100548837"})
        cls.tecnico = cls.env["res.partner"].create({
            "name": "Técnico menús", "parent_id": cls.empresa.id,
            "shrimp_is_field_tech": True, "email": "tec.menus@prueba.test"})
        for socio, login in ((cls.empresa, "verif.menus"), (cls.tecnico, "tec.menus")):
            cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        cls.W = cls.env["website"]

    def _usar_sitio_verificadores(self):
        sitio = self.W._shrimp_platform_site("verifier")
        if not sitio:
            self.skipTest("sin sitio de verificadores")
        for otra in self.W.search([("id", "!=", sitio.id)]):
            otra.domain = "http://otro-%s.invalid" % otra.id
        self.env.flush_all()
        sitio.domain = self.base_url().rstrip("/")
        self.env.flush_all()
        self.env.registry.clear_cache()

    def test_una_sola_lista(self):
        jobs = [(j["label"], j["url"]) for j in self.W._shrimp_verifier_jobs()]
        # Marketplace: Servicios ▾ > Verificación.
        barra = [(e["label"], e["url"]) for e in self.W._shrimp_nav_entries(self.empresa)
                 if e["key"].startswith("verify_")]
        self.assertEqual(barra, jobs)
        visibles = [(i["label"], i["url"])
                    for i in self.W._shrimp_nav_tree(self.empresa)["sections"]["services"]["items"]
                    if i["key"].startswith("verify_")]
        self.assertEqual(visibles, jobs)
        # Hijos guardados del menú del sitio.
        hijos = dict((c, h) for _n, _u, _s, c, h in self.W._SHRIMP_MENU_VERIFICADOR)
        self.assertEqual(hijos["verifier_jobs"], jobs)
        self.assertNotIn("/marketplace/my-certificates", [u for _n, u in hijos["verifier_company"]])

    def test_menu_y_panel_del_sitio(self):
        self._usar_sitio_verificadores()
        jobs = [j["url"] for j in self.W._shrimp_verifier_jobs()]
        for login, admin in (("verif.menus", True), ("tec.menus", False)):
            with self.subTest(login=login):
                self.authenticate(login, CLAVE)
                r = self.url_open("/my")
                self.assertEqual(r.status_code, 200)
                doc = lxml_html.fromstring(r.text)
                menu = [a.get("href") for a in doc.xpath(
                    "//ul[@id='top_menu']//li[contains(@class,'shrimp-dd-verifier-jobs')]//a[@data-shrimp-nav]")]
                panel = [a.get("href") for a in doc.xpath(
                    "//div[contains(@class,'mp-grid')]//a[starts-with(@data-shrimp-nav,'verify_')]")]
                self.assertEqual(menu, jobs)
                self.assertEqual(panel, jobs)
                empresa_menu = [a.get("data-shrimp-nav") for a in doc.xpath(
                    "//ul[@id='top_menu']//li[contains(@class,'shrimp-dd-verifier-company')]//a[@data-shrimp-nav]")]
                empresa_panel = [a.get("data-shrimp-nav") for a in doc.xpath(
                    "//div[contains(@class,'mp-grid')]//a[starts-with(@data-shrimp-nav,'verifier_')]")]
                self.assertEqual(empresa_menu, empresa_panel)
                self.assertEqual("verifier_team" in empresa_menu, admin)
                for href in jobs:
                    self.assertEqual(self.url_open(href, allow_redirects=False).status_code, 200, href)
