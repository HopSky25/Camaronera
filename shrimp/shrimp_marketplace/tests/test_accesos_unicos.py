"""Barra superior y «Accesos» de Mi panel salen de UNA definición.

Antes cada panel por rol (_panel_semillero, _panel_laboratorio...) tenía su
lista fija de accesos, sin matriz de capacidades ni permisos de subusuario,
y se separó de la barra: el naupliero veía en Mi panel «Publicar nauplio»,
«Precios de referencia (nauplio)» (el mismo catálogo que «Comprar nauplio»)
y no veía «Comprar nauplio / Mis compras / Mi inventario», que la matriz sí
le da (compras al mismo nivel). Además la barra había perdido «Mi panel».

Aquí, para cada perfil (y multi-perfil y subusuarios):
* «Accesos» == website._shrimp_nav_actions(socio, "dashboard"), y las
  mismas entradas, con el MISMO texto por URL, que la barra (salvo «Mi
  panel», que en el panel sería un enlace a sí mismo);
* cada «Comprar X» / «Publicar» corresponde a la matriz de capacidades;
* en la página: los enlaces de «Accesos» son los de la barra.
"""
from lxml import html as lxml_html

from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios
from .test_subcuentas import subusuario

SOLO_BARRA = {"dashboard"}


@tagged("post_install", "-at_install")
class TestAccesosUnicos(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "acc")
        cls.W = cls.env["website"]
        Partner = cls.env["res.partner"]
        tipos = [v for v, _l in Partner._fields["shrimp_user_type"].selection]
        cls.emp = None
        if "empacadora" in tipos:
            cls.emp = Partner.create({
                "name": "Empacadora acc", "is_company": True, "email": "emp.acc@prueba.test",
                "vat_or_id": "0944400039001", "shrimp_user_type": "empacadora",
                "emp_razon_social": "Empacadora acc S.A.", "emp_capacidad_lb_dia": 50000})
            cls.env["res.users"].create({
                "name": cls.emp.name, "login": "emp.secacc", "partner_id": cls.emp.id,
                "password": CLAVE, "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        # Subusuarios del semillero: solo ver / ver + comprar.
        Member = cls.env["shrimp.account.member"].sudo()
        Member._shrimp_ensure(cls.s["u_sem"], cls.s["sem"], is_admin=True)
        cls.sub_ver = subusuario(cls.env, cls.s["sem"], "sub.ver.acc", {"semillero": {"view"}})
        cls.sub_compra = subusuario(cls.env, cls.s["sem"], "sub.compra.acc",
                                    {"semillero": {"view", "buy"}})
        sitio = cls.W._shrimp_platform_site("main")
        cls.sitio = sitio

    # ------------------------------------------------------------------
    def _socios(self):
        socios = [("semillero", self.s["sem"]), ("laboratorio", self.s["lab"]),
                  ("camaronera", self.s["cam"])]
        if self.emp:
            socios.append(("empacadora", self.emp))
        return socios

    def _barra(self, socio):
        """{url: (key, label)} de la barra (desplegables + Mi cuenta ▾)."""
        arbol = self.W._shrimp_nav_tree(socio)
        return {it["url"]: (it["key"], it["label"])
                for sec in arbol["sections"].values() for it in sec["items"]}

    def _panel(self, socio):
        data = self.env["shrimp.dashboard"]._panel_data(socio)
        return data, {sc["url"]: (sc["key"], sc["label"]) for sc in data["shortcuts"]}

    def _puede(self, socio, cap):
        return self.W._shrimp_nav_can(socio, cap)

    def _usar_sitio(self):
        if not self.sitio:
            self.skipTest("sin sitio principal")
        base = self.base_url().rstrip("/")
        for otra in self.W.search([("id", "!=", self.sitio.id)]):
            otra.domain = "http://otro-%s.invalid" % otra.id
        self.env.flush_all()
        self.sitio.domain = base
        self.env.flush_all()
        self.env.registry.clear_cache()

    # ------------------------------------------------------------------
    # Misma definición
    # ------------------------------------------------------------------
    def test_panel_y_barra_misma_definicion_por_perfil(self):
        for rol, socio in self._socios():
            with self.subTest(rol=rol):
                data, panel = self._panel(socio)
                self.assertEqual(data["role"], rol)
                # «Accesos» ES la lista de la tabla única.
                esperado = self.W._shrimp_nav_actions(socio, surface="dashboard")
                self.assertEqual([(s["key"], s["label"], s["url"]) for s in data["shortcuts"]],
                                 [(s["key"], s["label"], s["url"]) for s in esperado])
                barra = self._barra(socio)
                solo_barra = {u for u, (k, _l) in barra.items() if k not in SOLO_BARRA} - set(panel)
                solo_panel = set(panel) - set(barra)
                self.assertFalse(solo_barra, "en la barra y no en Mi panel: %s" % solo_barra)
                self.assertFalse(solo_panel, "en Mi panel y no en la barra: %s" % solo_panel)
                # Mismo texto para la misma URL.
                for url, (_k, label) in panel.items():
                    self.assertEqual(barra[url][1], label, url)
                # Sin URL repetida (antes «Precios de referencia (nauplio)»
                # y «Comprar nauplio» eran el mismo enlace).
                urls = [s["url"] for s in data["shortcuts"]]
                self.assertEqual(len(urls), len(set(urls)), urls)
                # La cabecera también sale de la tabla.
                for a in data["actions"]:
                    if a.get("key"):
                        self.assertIn(a["url"], panel)

    def test_matriz_de_capacidades(self):
        for rol, socio in self._socios():
            with self.subTest(rol=rol):
                _data, panel = self._panel(socio)
                claves = {k for k, _l in panel.values()}
                self.assertEqual("publish" in claves, self._puede(socio, "sell_products"))
                self.assertEqual("purchases" in claves, self._puede(socio, "buy_products"))
                for tipo in ("semillero", "laboratorio", "camaronera"):
                    esperado = self._puede(socio, "buy_from_%s" % tipo) and not (
                        tipo == "camaronera" and self._puede(socio, "issue_price_lists"))
                    self.assertEqual("buy_%s" % tipo in claves, esperado, tipo)
        # El caso del reporte: el naupliero compra nauplio a otro naupliero
        # (mismo nivel) y lo ve en Mi panel, con el texto de la barra.
        _d, panel = self._panel(self.s["sem"])
        etiquetas = {k: l for k, l in panel.values()}
        self.assertEqual(etiquetas.get("buy_semillero"), "Comprar nauplio")
        self.assertEqual(etiquetas["publish"], "Publicar lote de nauplio")
        self.assertTrue({"purchases", "inventory", "reports", "storefront"} <= set(etiquetas))
        self.assertNotIn("Precios de referencia (nauplio)", etiquetas.values())
        self.assertNotIn("Publicar nauplio", etiquetas.values())

    def test_instalaciones_en_accesos(self):
        """Laboratorio y naupliero (no solo la camaronera) ven sus
        instalaciones en «Accesos», con el texto de la barra."""
        casos = [("semillero", self.s["sem"], "Centros y tanques"),
                 ("laboratorio", self.s["lab"], "Laboratorios y tanques"),
                 ("camaronera", self.s["cam"], "Fincas y piscinas")]
        url = "/marketplace/my-account#instalaciones"
        for rol, socio, texto in casos:
            with self.subTest(rol=rol):
                _d, panel = self._panel(socio)
                self.assertEqual(panel.get(url), ("facilities", texto))
                self.assertEqual(self._barra(socio).get(url), ("facilities", texto))
        if self.emp:
            self.assertNotIn(url, self._panel(self.emp)[1])

    def test_multiperfil_sigue_al_perfil_activo(self):
        socio = self.s["lab2"]
        socio._shrimp_request_role("camaronera", {
            "shrimp_representante": "Rep acc", "shrimp_telefono": "04-3333333"})
        socio._shrimp_set_active_role("camaronera")
        _d, panel = self._panel(socio)
        claves_cam = {k for k, _l in self._panel(self.s["cam"])[1].values()}
        self.assertEqual({k for k, _l in panel.values()}, claves_cam)
        self.assertIn("Publicar cosecha", {l for _k, l in panel.values()})
        socio._shrimp_set_active_role("laboratorio")
        _d, panel = self._panel(socio)
        self.assertIn("Publicar lote de larva", {l for _k, l in panel.values()})

    # ------------------------------------------------------------------
    # Subusuarios
    # ------------------------------------------------------------------
    def test_subusuarios_en_la_pagina(self):
        self._usar_sitio()
        casos = [
            # login, debe tener, no debe tener
            ("sub.ver.acc", {"my_products", "sales", "purchases", "inventory", "facilities"},
             {"publish", "buy_semillero"}),
            ("sub.compra.acc", {"buy_semillero", "purchases", "inventory"}, {"publish"}),
            ("sem.secacc", {"publish", "buy_semillero", "purchases", "inventory",
                            "facilities"}, set()),
        ]
        for login, si, no in casos:
            with self.subTest(login=login):
                self.authenticate(login, CLAVE)
                r = self.url_open("/my/dashboard")
                self.assertEqual(r.status_code, 200)
                doc = lxml_html.fromstring(r.text)
                accesos = {a.get("data-shrimp-nav"): a.get("href")
                           for a in doc.xpath("//div[@id='sp_shortcuts']//a")}
                barra = {a.get("data-shrimp-nav"): a.get("href")
                         for a in doc.xpath("//header//*[@data-shrimp-nav]")}
                barra.pop("dashboard", None)
                self.assertTrue(si <= set(accesos), (si, accesos))
                self.assertFalse(no & set(accesos), accesos)
                self.assertEqual(accesos, barra)
                # Y cada acceso responde (sin 403 ni redirección).
                for key, href in accesos.items():
                    if href.startswith("/my/security"):
                        continue
                    resp = self.url_open(href.split("#")[0], allow_redirects=False)
                    self.assertEqual(resp.status_code, 200, "%s %s" % (key, href))

    # ------------------------------------------------------------------
    # «Mi panel» en la barra
    # ------------------------------------------------------------------
    def test_mi_panel_primero_y_activo(self):
        self._usar_sitio()
        self.authenticate("sem.secacc", CLAVE)
        doc = lxml_html.fromstring(self.url_open("/my/dashboard").text)
        primero = doc.xpath("//ul[@id='top_menu']/li/a")[0]
        self.assertEqual(primero.get("href"), "/my/dashboard")
        self.assertIn("active", primero.get("class"))
        movil = doc.xpath("//div[@id='top_menu_collapse_mobile']//ul[contains(@class,'top_menu')]/li/a")
        self.assertEqual(movil[0].get("href"), "/my/dashboard")
        # Respaldo dentro de «Mi cuenta ▾».
        self.assertTrue(doc.xpath("//div[contains(@class,'js_usermenu')]//a[@data-shrimp-nav='dashboard']"))
        # Sin el «Mi cuenta» nativo (/my/home): «Panel completo» lleva a /my.
        self.assertFalse(doc.xpath("//div[contains(@class,'js_usermenu')]//a[@href='/my/home']"))
        # En otra página no está activo.
        doc = lxml_html.fromstring(self.url_open("/marketplace").text)
        primero = doc.xpath("//ul[@id='top_menu']/li/a")[0]
        self.assertEqual(primero.get("href"), "/my/dashboard")
        self.assertNotIn("active", primero.get("class") or "")

    def test_mi_panel_se_recrea_si_falta(self):
        """La base Pruebas perdió el menú «Mi panel» y la marca
        navbar_v2_sites impedía recrearlo: ahora es obligatorio."""
        if not self.sitio:
            self.skipTest("sin sitio principal")
        M = self.env["website.menu"]
        M.search([("shrimp_key", "=", "panel")]).unlink()
        self.env["ir.config_parameter"].sudo().set_param(
            self.W._SHRIMP_NAV_PARAM, "%s,tpl" % ",".join(str(i) for i in self.W.search([]).ids))
        self.W._shrimp_alinear_menus_portal()
        menu = M.search([("website_id", "=", self.sitio.id), ("shrimp_key", "=", "panel")])
        self.assertEqual(len(menu), 1)
        self.assertEqual((menu.url, menu.sequence), ("/my/dashboard", 15))
        self.assertTrue(M.search([("website_id", "=", False), ("shrimp_key", "=", "panel")]))
        for otro in self.W.search([("shrimp_platform", "!=", "main")]):
            self.assertFalse(M.search([("website_id", "=", otro.id), ("shrimp_key", "=", "panel")]))
        # Idempotente.
        self.assertEqual(self.W._shrimp_alinear_menus_portal(), 0)
