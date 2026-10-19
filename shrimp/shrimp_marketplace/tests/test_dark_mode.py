"""Modo oscuro del sitio y del portal (views/dark_mode_templates.xml).

Se comprueba: el botón luna/sol sale en la cabecera a la izquierda de
«Iniciar sesión» (y, con sesión, del menú del usuario), accesible; la cookie
shrimp_theme hace que el SERVIDOR pinte data-bs-theme en <html> (sin destello);
sin cookie, el script del <head> decide; un valor raro se ignora; y nada de
esto llega al backend ni a los PDF (otros bundles).
"""
import re

from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios


@tagged("post_install", "-at_install")
class TestDarkMode(HttpCase):

    def _html(self, url="/", theme=None):
        for c in list(self.opener.cookies):
            if c.name == "shrimp_theme":
                self.opener.cookies.clear(c.domain, c.path, c.name)
        if theme is not None:
            self.opener.cookies.set("shrimp_theme", theme)
        res = self.url_open(url)
        self.assertEqual(res.status_code, 200, url)
        return res.text

    @staticmethod
    def _html_tag(text):
        return re.search(r"<html[^>]*>", text).group(0)

    def test_boton_y_script_sin_cookie(self):
        text = self._html("/")
        self.assertNotIn("data-bs-theme", self._html_tag(text),
                         "sin cookie el servidor no fija el modo (lo decide el script del head)")
        self.assertIn('id="shrimp_theme_boot"', text)
        # El script va en el <head>, antes del CSS del sitio.
        self.assertLess(text.index('id="shrimp_theme_boot"'), text.index("</head>"))
        self.assertIn("data-shrimp-theme-toggle", text)
        self.assertIn('aria-label="Activar modo oscuro"', text)
        # Escritorio: a la izquierda de «Iniciar sesión».
        self.assertLess(text.index("shrimp-theme-item o_no_autohide_item"), text.index("shrimp-sign-in"))
        # Móvil: junto a la hamburguesa.
        movil = re.search(r'<ul class="o_header_mobile_buttons_wrap[^"]*">(.*?)</ul>', text, re.S)
        self.assertTrue(movil and "data-shrimp-theme-toggle" in movil.group(1))

    def test_cookie_pinta_el_modo_en_el_servidor(self):
        text = self._html("/", "dark")
        self.assertIn('data-bs-theme="dark"', self._html_tag(text))
        self.assertIn('aria-label="Activar modo claro"', text)
        self.assertIn('aria-pressed="true"', text)

        text = self._html("/marketplace", "light")
        self.assertIn('data-bs-theme="light"', self._html_tag(text))
        self.assertIn('aria-label="Activar modo oscuro"', text)

        text = self._html("/", "purple")
        self.assertNotIn("data-bs-theme", self._html_tag(text), "valor desconocido: se ignora")

    def test_con_sesion_antes_del_menu_de_usuario(self):
        s = crear_socios(self.env, "dkm")
        self.authenticate(s["u_cam"].login, CLAVE)
        text = self._html("/my/home", "dark")
        self.assertIn('data-bs-theme="dark"', self._html_tag(text))
        i_boton = text.index("shrimp-theme-item o_no_autohide_item")
        self.assertLess(i_boton, text.index("js_usermenu"))

    def test_fuera_del_backend_y_de_los_pdf(self):
        ruta = "shrimp_marketplace/static/src/css/shrimp_dark"
        asset = self.env["ir.asset"]
        for bundle in ("web.assets_backend", "web.report_assets_common", "web.report_assets_pdf"):
            paths = [p[0] for p in asset._get_asset_paths(bundle, {})]
            self.assertFalse([p for p in paths if ruta in p], bundle)
        front = [p[0] for p in asset._get_asset_paths("web.assets_frontend", {})]
        self.assertTrue([p for p in front if ruta in p])
        # El cliente web (backend) no lleva el atributo ni el script aunque haya cookie.
        self.authenticate("admin", "admin")
        text = self._html("/odoo", "dark")
        self.assertNotIn('data-bs-theme="dark"', self._html_tag(text))
        self.assertNotIn("shrimp_theme_boot", text)
