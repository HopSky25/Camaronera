"""Rutas en inglés con TODA la cadena instalada (shrimp_api depende de todos).

* Una URL vieja (español) lleva al mismo sitio que la nueva: mismo código
  final y misma URL final, para el visitante y para cada rol.
* Barra, menús de las tres plataformas, botón de alta y llegada tras el login
  apuntan a rutas nuevas que existen.
* Las reglas que comparan prefijos (menús solo-maquilador, solo-admin del
  verificador) valen con la URL nueva y con la vieja.
"""
import re
from urllib.parse import urlsplit

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios

PARAM = re.compile(r"<(?:[a-z_]+:)?([a-z_]+)>")


def _llenar(ruta, valor="ABC123"):
    return PARAM.sub(valor, ruta)


def _ruta(url):
    return (url or "").split("?", 1)[0].split("#", 1)[0]


@tagged("post_install", "-at_install")
class TestRutasIngles(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "ri")
        Socio = cls.env["res.partner"]
        portal = cls.env.ref("base.group_portal")

        def usuario(socio, login):
            return cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})

        cls.verif = Socio.create({
            "name": "Verificadora rutas", "is_company": True, "shrimp_user_type": "verificador",
            "vat_or_id": "0977700777001", "email": "verif.rutas@prueba.test"})
        cls.maq = Socio.create({
            "name": "Maquiladora rutas", "is_company": True, "shrimp_user_type": "maquilador",
            "vat_or_id": "0955500777001", "email": "maq.rutas@prueba.test",
            "pack_razon_social": "Maq rutas S.A.", "pack_ubicacion": "Guayaquil"})
        cls.emp = Socio.create({
            "name": "Empacadora rutas", "is_company": True, "shrimp_user_type": "empacadora",
            "vat_or_id": "0922200777001", "email": "emp.rutas@prueba.test"})
        cls.usuarios = {
            "camaronera": cls.s["u_cam"],
            "laboratorio": cls.s["u_lab"],
            "verificador": usuario(cls.verif, "verif.rutas"),
            "maquilador": usuario(cls.maq, "maq.rutas"),
            "empacadora": usuario(cls.emp, "emp.rutas"),
        }
        cls.pares = cls.env["ir.http"]._shrimp_legacy_routes()

    def _igual(self, vieja, nueva):
        """La vieja da 301 a la nueva y, desde ahí, la respuesta es la misma
        que pidiendo la nueva directamente (código y destino). Sin seguir
        más saltos: las rutas de verificadores/empaque redirigen al dominio
        de su plataforma, que en el test no existe."""
        rv = self.url_open(vieja, allow_redirects=False)
        self.assertEqual(rv.status_code, 301, vieja)
        self.assertEqual(urlsplit(rv.headers["Location"]).path, urlsplit(nueva).path)
        rv = self.url_open(rv.headers["Location"], allow_redirects=False)
        rn = self.url_open(nueva, allow_redirects=False)
        self.assertEqual(rv.status_code, rn.status_code,
                         "%s -> %s / %s -> %s" % (vieja, rv.status_code, nueva, rn.status_code))
        self.assertEqual(rv.headers.get("Location"), rn.headers.get("Location"))
        self.assertLess(rn.status_code, 500)
        return rn.status_code

    # ------------------------------------------------------------------
    def test_tabla_completa(self):
        """Con toda la cadena, la tabla tiene las rutas de los 5 módulos."""
        viejas = {v for v, _n in self.pares}
        for v in ("/registro", "/mi-panel", "/marketplace/compras", "/verificador/bandeja",
                  "/empacadora/reservas", "/maquilador/bandeja", "/marketplace/empaque/count",
                  "/marketplace/producto/<product_ref>"):
            self.assertIn(v, viejas)
        self.assertGreaterEqual(len(self.pares), 190)

    def test_mismo_resultado_anonimo(self):
        for vieja, nueva in self.pares:
            with self.subTest(vieja=vieja):
                self._igual(_llenar(vieja), _llenar(nueva))

    def test_mismo_resultado_por_rol(self):
        """GET de cada ruta sin parámetros con cada rol: mismo código y misma
        URL final por la vieja que por la nueva (200, 303 al login, 403...)."""
        sin_param = [(v, n) for v, n in self.pares if "<" not in v]
        for rol, user in self.usuarios.items():
            self.authenticate(user.login, CLAVE)
            for vieja, nueva in sin_param:
                with self.subTest(rol=rol, vieja=vieja):
                    self._igual(vieja, nueva)

    def test_mismo_resultado_con_referencia_real(self):
        """Con una referencia real (no inventada) de una compra y un producto."""
        from odoo.addons.shrimp_marketplace.tests.common import producto
        lote = producto(self.env, self.s["sem"], nombre="Lote rutas")
        tx = lote.execute_purchase_flow(self.s["lab"], 2.0)["transaction"]
        self.authenticate(self.s["u_lab"].login, CLAVE)
        valores = {"tx_ref": tx.uuid_ref, "product_ref": lote.uuid_ref,
                   "partner_ref": self.s["sem"].uuid_ref}
        for vieja, nueva in self.pares:
            nombres = set(PARAM.findall(vieja))
            if not nombres or not nombres <= set(valores):
                continue
            fv = PARAM.sub(lambda m: valores[m.group(1)], vieja)
            fn = PARAM.sub(lambda m: valores[m.group(1)], nueva)
            with self.subTest(vieja=fv):
                self._igual(fv, fn)
        # Ejemplos concretos con su código esperado.
        r = self.url_open("/marketplace/compras/%s/trazabilidad" % tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.url.endswith("/marketplace/purchases/%s/traceability" % tx.uuid_ref))
        r = self.url_open("/marketplace/producto/%s" % lote.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.url.endswith("/marketplace/product/%s" % lote.uuid_ref))

    # ------------------------------------------------------------------
    def _existe(self, url):
        return self.env["website"]._shrimp_nav_route_exists(_ruta(url))

    def _nueva_y_viva(self, url, donde):
        IrHttp = self.env["ir.http"]
        with self.subTest(donde=donde, url=url):
            self.assertIsNone(IrHttp._shrimp_legacy_target(_ruta(url)),
                              "%s usa una ruta vieja: %s" % (donde, url))
            self.assertTrue(self._existe(url), "%s apunta a una ruta inexistente: %s" % (donde, url))

    def test_barra_y_menus_apuntan_a_rutas_nuevas(self):
        W = self.env["website"]
        for rol, user in self.usuarios.items():
            for e in W._shrimp_nav_entries(user.partner_id):
                if not e.get("url") or not e["url"].startswith("/"):
                    continue
                if e.get("route"):
                    # Entradas de pantallas futuras: renombradas ya al inglés.
                    self.assertIsNone(self.env["ir.http"]._shrimp_legacy_target(_ruta(e["url"])))
                    self.assertEqual(_ruta(e["route"]), _ruta(e["url"]))
                    continue
                self._nueva_y_viva(e["url"], "barra (%s) %s" % (rol, e.get("key")))
        for clave, _nombre, url, *_resto in W._SHRIMP_NAV_MENUS:
            self._nueva_y_viva(url, "menú %s" % clave)
        for arbol in (W._SHRIMP_MENU_VERIFICADOR, W._SHRIMP_MENU_MAQUILADOR):
            for _nombre, url, _seq, _clave, hijos in arbol:
                if url and url.startswith("/"):
                    self._nueva_y_viva(url, "menú de plataforma")
                for _n, u in hijos:
                    self._nueva_y_viva(u, "submenú de plataforma")
        for plataforma, cta in W._SHRIMP_HEADER_CTA.items():
            self._nueva_y_viva(cta["url"], "alta %s" % plataforma)
        for menu in self.env["website.menu"].sudo().search([("url", "=like", "/%")]):
            with self.subTest(menu=menu.url):
                self.assertIsNone(self.env["ir.http"]._shrimp_legacy_target(_ruta(menu.url)))

    def test_pantallas_futuras_no_existen_por_el_redirector(self):
        W = self.env["website"]
        for ruta in ("/marketplace/pre-reservations", "/marketplace/pre-reservations/new",
                     "/marketplace/production-calendar", "/marketplace/pre-reservas"):
            self.assertFalse(W._shrimp_nav_route_exists(ruta))
        # Una ruta vieja no «existe» para la barra (el redirector no es ruta).
        self.assertFalse(W._shrimp_nav_route_exists("/marketplace/compras"))
        self.assertTrue(W._shrimp_nav_route_exists("/marketplace/purchases"))

    def test_llegada_tras_login(self):
        W = self.env["website"]
        principal = W._shrimp_platform_site("main")
        llegada = principal._shrimp_login_landing(self.usuarios["camaronera"])
        self.assertIn(llegada, (None, "/my/dashboard"))
        verificadores = W._shrimp_platform_site("verifier")
        if verificadores:
            self.assertEqual(verificadores._shrimp_login_landing(self.usuarios["verificador"]),
                             "/verifier/inbox")
        empaque = W._shrimp_platform_site("copacker")
        if empaque:
            self.assertEqual(empaque._shrimp_login_landing(self.usuarios["maquilador"]),
                             "/copacker/inbox")

    def test_menus_solo_maquilador_y_solo_admin(self):
        empaque = self.env["website"]._shrimp_platform_site("copacker")
        if not empaque:
            self.skipTest("sin sitio de empaque")
        raiz = empaque.menu_id
        Menu = self.env["website.menu"].sudo()
        nuevo = Menu.create({"name": "Órdenes", "url": "/copacker/orders", "parent_id": raiz.id,
                             "website_id": empaque.id})
        viejo = Menu.create({"name": "Órdenes viejo", "url": "/maquilador/ordenes",
                             "parent_id": raiz.id, "website_id": empaque.id})
        for rol, visible in (("camaronera", False), ("maquilador", True)):
            for menu in (nuevo, viejo):
                with self.subTest(rol=rol, url=menu.url):
                    menu.invalidate_recordset(["is_visible"])
                    m = menu.with_user(self.usuarios[rol])
                    self.assertEqual(m.is_visible, visible)
        solo_admin = self.env["website.menu"]._SHRIMP_URLS_SOLO_ADMIN_VERIFICADOR
        self.assertIn("/verifier/technicians", solo_admin)
        self.assertIn("/verificador/tecnicos", solo_admin)
