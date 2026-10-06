"""La barra superior de las tres plataformas.

* Sin sesión: solo logo, «Iniciar sesión» y el botón de alta de la
  plataforma; ningún menú de opciones (tampoco en el menú del móvil). En el
  marketplace, además, «Marketplace».
* Con sesión (marketplace): «Mi panel · Marketplace · Vender ▾ · Comprar ▾ ·
  Servicios ▾» + «Perfil: X ▾» + «Mi cuenta ▾». Los desplegables se
  reconocen por su CLAVE de menú (website.menu.shrimp_key), su contenido sale
  de website._shrimp_nav_entries() filtrado por la matriz de capacidades y el
  perfil ACTIVO, y un desplegable vacío no se pinta.
* Los sitios de verificadores y de empaque no cambian.
"""
from lxml import html as lxml_html

from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios
from .test_coherencia import cargar_migracion

# Lo que la barra NO debe volver a tener (duplicados y entradas retiradas o
# movidas a Mi panel).
RETIRADAS = ("/marketplace/planning", "/my", "/marketplace/purchase-history",
             "/marketplace/check-requests", "/marketplace/my-track-record")
PRIMER_NIVEL = ["Mi panel", "Marketplace", "Vender", "Comprar", "Servicios"]


@tagged("post_install", "-at_install")
class TestNavbarPlataformas(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "nav")
        W = cls.env["website"]
        cls.sitios = {p: W._shrimp_platform_site(p) for p in ("main", "verifier", "copacker")}
        Partner = cls.env["res.partner"]
        tipos = [v for v, _l in Partner._fields["shrimp_user_type"].selection]
        cls.emp = None
        if "empacadora" in tipos:
            cls.emp = Partner.create({
                "name": "Empacadora nav", "is_company": True, "email": "emp.nav@prueba.test",
                "vat_or_id": "0944400029001", "shrimp_user_type": "empacadora",
                "emp_razon_social": "Empacadora nav S.A.", "emp_capacidad_lb_dia": 50000})
            cls.env["res.users"].create({
                "name": cls.emp.name, "login": "emp.secnav", "partner_id": cls.emp.id,
                "password": CLAVE, "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        # Cuenta con dos perfiles: laboratorio (activo) + camaronera.
        cls.multi = cls.s["lab2"]
        cls.multi._shrimp_request_role("camaronera", {
            "shrimp_representante": "Rep nav", "shrimp_telefono": "04-3333333"})

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------
    def _usar_sitio(self, plataforma):
        sitio = self.sitios.get(plataforma)
        if not sitio:
            self.skipTest("No hay sitio de la plataforma %s" % plataforma)
        base = self.base_url().rstrip("/")
        # El sitio de la petición se resuelve por dominio: se apunta el de la
        # plataforma a la URL de los tests y los demás a otra parte.
        for otra in self.env["website"].search([("id", "!=", sitio.id)]):
            otra.domain = "http://otro-%s.invalid" % otra.id
        self.env.flush_all()
        sitio.domain = base
        self.env.flush_all()
        self.env.registry.clear_cache()
        return sitio

    def _pagina(self, ruta="/marketplace"):
        resp = self.url_open(ruta)
        self.assertEqual(resp.status_code, 200, ruta)
        return resp.text

    def _cabecera(self, ruta="/marketplace"):
        return self._pagina(ruta).split("</header>")[0]

    def _barra(self, texto):
        """(enlaces de la barra de escritorio, del menú del móvil, claves de
        «Mi cuenta ▾», textos de primer nivel)."""
        doc = lxml_html.fromstring(texto)
        escritorio = [h for h in doc.xpath("//ul[@id='top_menu']//a/@href") if h != "#"]
        movil = [h for h in doc.xpath(
            "//div[@id='top_menu_collapse_mobile']//ul[contains(@class, 'top_menu')]//a/@href")
            if h != "#"]
        cuenta = doc.xpath("//nav[not(contains(@class, 'o_header_mobile'))]"
                           "//div[contains(@class, 'js_usermenu')]//a/@data-shrimp-nav")
        primer_nivel = [" ".join(t.strip() for t in li.xpath("./a//text()")).strip()
                        for li in doc.xpath("//ul[@id='top_menu']/li")]
        return escritorio, movil, cuenta, [p for p in primer_nivel if p]

    def _links(self, socio, sections=("sell", "buy", "services")):
        return self.env["website"]._shrimp_nav_links(socio, sections=sections)

    def _claves(self, socio):
        return {k for _s, k, _l, _u in self._links(socio)}

    def _etiquetas(self, socio):
        return {k: l for _s, k, l, _u in self._links(socio)}

    def _secciones(self, socio):
        return {s: [i["key"] for i in sec["items"]]
                for s, sec in self.env["website"]._shrimp_nav_tree(socio)["sections"].items()}

    def _puede(self, socio, cap):
        return self.env["website"]._shrimp_nav_can(socio, cap)

    def _compras_esperadas(self, socio):
        """Las «Comprar X» que corresponden a la matriz (compras al mismo
        nivel incluidas): se leen de la matriz, no se fijan aquí."""
        esperadas = set()
        for tipo in ("semillero", "laboratorio", "camaronera"):
            if self._puede(socio, "buy_from_%s" % tipo):
                if tipo == "camaronera" and self._puede(socio, "issue_price_lists"):
                    continue
                esperadas.add("buy_%s" % tipo)
        return esperadas

    def _ruta(self, ruta):
        return self.env["website"]._shrimp_nav_route_exists(ruta)

    # ------------------------------------------------------------------
    # Visitante
    # ------------------------------------------------------------------
    def test_visitante_sin_menu_en_las_tres_plataformas(self):
        esperado = {
            "main": ("Registrarse", "/register"),
            "verifier": ("Ser verificador", "/register/verifier"),
            "copacker": ("Registrar mi planta", "/register/copacker"),
        }
        menus = {
            "main": ("/marketplace/products", "/marketplace/purchases", "/marketplace/my-account",
                     "/my/dashboard", "/marketplace/copacking"),
            "verifier": ("/verifier/inbox", "/verifier/profile"),
            "copacker": ("/copacker/inbox", "/copacker/tariffs", "/copacker/profile"),
        }
        for plataforma, (texto, url) in esperado.items():
            with self.subTest(plataforma=plataforma):
                self._usar_sitio(plataforma)
                cabecera = self._cabecera("/")
                for ruta in menus[plataforma]:
                    self.assertNotIn('href="%s' % ruta, cabecera, plataforma)
                self.assertNotIn("shrimp-dd-", cabecera)
                self.assertNotIn("shrimp-account-toggle", cabecera)
                self.assertIn("Iniciar sesión", cabecera)
                self.assertNotIn(">Sign in<", cabecera)
                self.assertIn(texto, cabecera)
                self.assertIn('href="%s"' % url, cabecera)
                # Sin buscador para el visitante.
                self.assertNotIn("o_search_modal", cabecera)
                # El catálogo público sí: «Marketplace» solo en el marketplace.
                if plataforma == "main":
                    self.assertIn('href="/marketplace"', cabecera)
                    escritorio, movil, _c, _p = self._barra(cabecera + "</header>")
                    self.assertEqual(escritorio, ["/marketplace"], "anónimo: solo Marketplace")
                    self.assertEqual(movil, ["/marketplace"])
                else:
                    self.assertNotIn('href="/marketplace"', cabecera)

    def test_portada_visitante_y_socio(self):
        """«/»: visitante y socio con sesión ven la portada (sin redirigir)."""
        self._usar_sitio("main")
        self.assertEqual(self.url_open("/").status_code, 200)
        self.authenticate("lab.secnav", CLAVE)
        r = self.url_open("/", allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("sl-hero", r.text)

    # ------------------------------------------------------------------
    # Con sesión: estructura
    # ------------------------------------------------------------------
    def test_usuario_con_sesion_ve_la_barra_nueva(self):
        self._usar_sitio("main")
        self.authenticate("lab.secnav", CLAVE)
        texto = self._pagina()
        cabecera = texto.split("</header>")[0]
        self.assertIn("shrimp-dd-sell", cabecera)
        self.assertIn("shrimp-dd-buy", cabecera)
        self.assertNotIn("shrimp-dd-products", cabecera)
        self.assertNotIn("shrimp-dd-operations", cabecera)
        self.assertIn('href="/marketplace/purchases"', cabecera)
        # Quien tiene cuenta no ve el botón de alta.
        self.assertNotIn("shrimp-header-cta", cabecera)
        escritorio, movil, cuenta, primer_nivel = self._barra(texto)
        # Orden fijo; sin Inicio ni Contáctanos.
        orden = [p for p in primer_nivel if p in PRIMER_NIVEL]
        self.assertEqual(orden[:2], ["Mi panel", "Marketplace"])
        self.assertEqual(orden, sorted(orden, key=PRIMER_NIVEL.index))
        self.assertEqual(escritorio[:2], ["/my/dashboard", "/marketplace"])
        self.assertNotIn("/", escritorio)
        self.assertNotIn("/contactus", escritorio)
        # Sin duplicados, y el móvil con la misma estructura.
        self.assertEqual(len(escritorio), len(set(escritorio)), escritorio)
        self.assertEqual(escritorio, movil)
        for ruta in RETIRADAS:
            self.assertNotIn(ruta, escritorio)
        # «Mi cuenta ▾» a la derecha.
        self.assertIn("shrimp-account-toggle", cabecera)
        self.assertTrue({"account_data", "certificates", "storefront"} <= set(cuenta), cuenta)

    def test_desplegable_por_clave_aunque_se_renombre(self):
        sitio = self._usar_sitio("main")
        menu = self.env["website.menu"].search([
            ("website_id", "=", sitio.id), ("shrimp_key", "=", "sell")], limit=1)
        self.assertTrue(menu, "el menú Vender tiene su clave")
        menu.name = "Mis lotes y publicaciones"
        self.authenticate("lab.secnav", CLAVE)
        cabecera = self._cabecera()
        self.assertIn("shrimp-dd-sell", cabecera)
        self.assertIn("Mis lotes y publicaciones", cabecera)
        self.assertIn('href="/marketplace/products/new"', cabecera)

    def test_desplegable_vacio_no_se_pinta(self):
        """La empacadora no vende: sin «Vender»."""
        if not self.emp:
            self.skipTest("sin shrimp_packer")
        self._usar_sitio("main")
        self.authenticate("emp.secnav", CLAVE)
        cabecera = self._cabecera()
        self.assertNotIn("shrimp-dd-sell", cabecera)
        self.assertIn("shrimp-dd-buy", cabecera)
        self.assertNotIn('href="/marketplace/products/new"', cabecera)

    # ------------------------------------------------------------------
    # Con sesión: entradas por rol
    # ------------------------------------------------------------------
    def test_semillero(self):
        socio = self.s["sem"]
        sec = self._secciones(socio)
        self.assertEqual(sec["sell"][:2], ["publish", "my_products"])
        self.assertEqual(sec["sell"][-1], "sales")
        self.assertNotIn("services", sec)
        claves = self._claves(socio)
        self.assertTrue({"publish", "my_products", "sales"} <= claves)
        self.assertEqual({k for k in claves if k.startswith("buy_")}, self._compras_esperadas(socio))
        self.assertEqual("purchases" in claves, self._puede(socio, "buy_products"))
        self.assertFalse({"facilities", "harvest_reservations", "offer", "copack"} & claves)
        self.assertEqual("preorders_in" in claves, self._ruta("/marketplace/pre-reservations"))
        self.assertEqual("production_calendar" in claves, self._ruta("/marketplace/production-calendar"))
        etiquetas = self._etiquetas(socio)
        self.assertEqual(etiquetas["publish"], "Publicar lote de nauplio")
        self.assertEqual(etiquetas["sales"], "Ventas y despachos")

    def test_laboratorio(self):
        socio = self.s["lab"]
        claves = self._claves(socio)
        self.assertTrue({"publish", "my_products", "sales", "buy_semillero",
                         "purchases", "inventory"} <= claves)
        self.assertEqual({k for k in claves if k.startswith("buy_")}, self._compras_esperadas(socio))
        self.assertFalse({"facilities", "harvest_reservations", "packers", "offer", "copack"} & claves)
        etiquetas = self._etiquetas(socio)
        self.assertEqual(etiquetas["publish"], "Publicar lote de larva")
        self.assertEqual(etiquetas["my_products"], "Mis lotes y corridas")
        self.assertNotIn("services", self._secciones(socio))

    def test_camaronera(self):
        socio = self.s["cam"]
        sec = self._secciones(socio)
        claves = self._claves(socio)
        self.assertTrue({"publish", "my_products", "sales", "buy_laboratorio",
                         "purchases", "inventory"} <= claves)
        self.assertEqual({k for k in claves if k.startswith("buy_")}, self._compras_esperadas(socio))
        if self._puede(socio, "declare_harvest"):     # shrimp_packer
            self.assertTrue({"harvest_reservations", "price_lists_received", "packers",
                             "harvest_simulator"} <= set(sec["sell"]))
            self.assertNotIn("harvests_offered", claves)
        if self._puede(socio, "request_copack"):      # shrimp_copacking
            self.assertEqual(sec["services"], ["copack"])
        # Máximo 7 por desplegable.
        self.assertTrue(all(len(v) <= 7 for v in sec.values()), sec)
        etiquetas = self._etiquetas(socio)
        self.assertEqual(etiquetas["publish"], "Publicar cosecha")
        self.assertEqual(etiquetas["inventory"], "Inventario y siembra")

    def test_empacadora(self):
        if not self.emp:
            self.skipTest("sin shrimp_packer")
        sec = self._secciones(self.emp)
        claves = self._claves(self.emp)
        self.assertNotIn("sell", sec, "la empacadora no vende")
        esperado = ["offer", "price_lists", "harvests_offered", "purchases"]
        self.assertEqual([k for k in sec["buy"] if k in esperado], esperado)
        self.assertIn("supplier_ranking", sec["buy"])
        self.assertFalse({"publish", "inventory", "buy_camaronera"} & claves)
        self.assertEqual("exports" in claves, self._ruta("/marketplace/exports"))
        if self._puede(self.emp, "request_copack"):
            self.assertEqual(sec["services"], ["copack"])
        self.assertIn("packer_profile", sec["account"])
        self.assertNotIn("storefront", sec["account"])

    def test_multirol_solo_el_perfil_activo(self):
        socio = self.multi
        socio._shrimp_set_active_role("laboratorio")
        como_lab = self._claves(socio)
        socio._shrimp_set_active_role("camaronera")
        como_cam = self._claves(socio)
        self.assertEqual(como_cam, self._claves(self.s["cam"]))
        self.assertEqual(como_lab, self._claves(self.s["lab"]))
        self.assertEqual(self._etiquetas(socio)["publish"], "Publicar cosecha")
        # Y en la barra renderizada.
        self._usar_sitio("main")
        self.authenticate("lab2.secnav", CLAVE)
        cabecera = self._cabecera()
        self.assertIn("Perfil: Camaronera", cabecera)
        self.assertIn("Publicar cosecha", cabecera)
        self.assertNotIn("Publicar lote de larva", cabecera)
        socio._shrimp_set_active_role("laboratorio")

    def test_sin_duplicados_para_ningun_rol(self):
        socios = [self.s["sem"], self.s["lab"], self.s["cam"]] + ([self.emp] if self.emp else [])
        for socio in socios:
            with self.subTest(rol=socio.shrimp_user_type):
                urls = [u for _s, _k, _l, u in self._links(
                    socio, sections=("sell", "buy", "services", "account"))]
                self.assertEqual(len(urls), len(set(urls)), urls)
                textos = [l for _s, _k, l, _u in self._links(socio)]
                self.assertEqual(len(textos), len(set(textos)), textos)
                for ruta in RETIRADAS + ("/marketplace", "/marketplace/calendar"):
                    self.assertNotIn(ruta, urls)

    def test_my_por_rol(self):
        """/my: tarjetas del perfil; sin «Solicitudes de chequeo»; «Mi panel»
        una sola vez."""
        self._usar_sitio("main")
        casos = [("sem.secnav", True)] + ([("emp.secnav", False)] if self.emp else [])
        for login, vende in casos:
            with self.subTest(login=login):
                self.authenticate(login, CLAVE)
                cuerpo = self._pagina("/my").split("</header>", 1)[1]
                self.assertNotIn('href="/marketplace/check-requests"', cuerpo)
                self.assertEqual('href="/marketplace/products/new"' in cuerpo, vende)
                self.assertLessEqual(cuerpo.count('href="/my/dashboard"'), 1)

    # ------------------------------------------------------------------
    # Las otras plataformas no cambian
    # ------------------------------------------------------------------
    def test_sitios_de_verificadores_y_empaque_intactos(self):
        M = self.env["website.menu"]

        def foto(sitio):
            return sorted(M.search([("website_id", "=", sitio.id)]).mapped(
                lambda m: (m.name, m.url, m.shrimp_key or "", m.sequence)))

        antes = {p: foto(self.sitios[p]) for p in ("verifier", "copacker") if self.sitios.get(p)}
        self.env["website"]._shrimp_alinear_menus_portal()
        for p, menus in antes.items():
            despues = foto(self.sitios[p])
            self.assertEqual(menus, despues, p)
            self.assertFalse({k for _n, _u, k, _s in despues} & {"panel", "sell", "buy", "services"}, p)
        # Con sesión, ninguno pinta la barra del marketplace ni «Mi cuenta ▾».
        for p in antes:
            with self.subTest(plataforma=p):
                self._usar_sitio(p)
                self.authenticate("cam.secnav", CLAVE)
                cabecera = self.url_open("/my").text.split("</header>")[0]
                for clase in ("shrimp-dd-sell", "shrimp-dd-buy", "shrimp-dd-services",
                              "shrimp-account-toggle"):
                    self.assertNotIn(clase, cabecera, p)
                self.assertNotIn('href="/my/dashboard"', cabecera)

    # ------------------------------------------------------------------
    # Migración de los menús
    # ------------------------------------------------------------------
    def test_migracion_de_la_barra_idempotente(self):
        sitio = self.sitios.get("main")
        if not sitio:
            self.skipTest("sin sitio principal")
        M = self.env["website.menu"]
        W = self.env["website"]
        raiz = sitio.menu_id
        # Estado de la barra vieja.
        M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", "panel")]).unlink()
        viejos = {"sell": ("Productos", "products", "/marketplace/products", 25),
                  "buy": ("Operaciones", "operations", "/marketplace/purchases", 26),
                  "services": ("Mi cuenta", "account", "/marketplace/my-account", 27)}
        for nueva, (nombre, clave, url, seq) in viejos.items():
            menu = M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", nueva)], limit=1)
            if not menu:
                menu = M.create({"name": nombre, "url": url, "parent_id": raiz.id,
                                 "website_id": sitio.id, "sequence": seq})
            menu.write({"name": nombre, "shrimp_key": clave, "url": url})
        M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", "marketplace")]).shrimp_key = False
        self.env["ir.config_parameter"].sudo().set_param(W._SHRIMP_NAV_PARAM, "")

        mig = cargar_migracion("shrimp_marketplace", "19.0.1.10.0", "post")
        mig.migrate(self.env.cr, "19.0.1.9.0")

        def foto():
            return sorted(M.search([("website_id", "=", sitio.id)]).mapped(
                lambda m: (m.id, m.name, m.url, m.shrimp_key or "", m.sequence)))

        primera = foto()
        porclave = {k: (n, u, s) for _i, n, u, k, s in primera if k}
        self.assertEqual(porclave["panel"], ("Mi panel", "/my/dashboard", 15))
        self.assertEqual(porclave["marketplace"][1], "/marketplace")
        self.assertEqual(porclave["sell"], ("Vender", "/marketplace/products", 25))
        self.assertEqual(porclave["buy"], ("Comprar", "/marketplace/purchases", 26))
        self.assertEqual(porclave["services"], ("Servicios", "/marketplace/copacking", 27))
        self.assertFalse({"products", "operations", "account"} & set(porclave))
        # Idempotente: la segunda pasada (y la de cada -u) no cambia nada.
        mig.migrate(self.env.cr, "19.0.1.9.0")
        self.assertEqual(W._shrimp_alinear_menus_portal(), 0)
        self.assertEqual(foto(), primera)
        # Lo que el cliente borra después no se vuelve a crear, y lo que
        # renombra se respeta.
        M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", "panel")]).unlink()
        M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", "buy")]).name = "Compras"
        W._shrimp_alinear_menus_portal()
        self.assertFalse(M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", "panel")]))
        self.assertEqual(M.search([("website_id", "=", sitio.id), ("shrimp_key", "=", "buy")]).name,
                         "Compras")
