"""Bloqueo de scroll con modales abiertos (static/src/js/shrimp_scroll_lock.js).

Sin navegador en el servidor de pruebas, se comprueba lo que un cambio
podría romper sin que nadie lo note:
  - la utilidad (JS + CSS) está en web.assets_frontend, antes que los scripts
    que la usan, y no en el backend;
  - se engancha a Bootstrap (modal y offcanvas) y expone la API;
  - el cajón de filtros y la capa «Enviando…» la usan;
  - ninguna hoja vuelve a poner overflow:hidden en <html> Y <body> a la vez
    (con html/body al 100% del viewport eso subía la página arriba del todo
    al abrir el cajón).
La prueba de comportamiento (rueda y dedo sobre el fondo, salto al abrir,
posición al cerrar) está en CamaronMarket/auditoria_modales (Chrome por CDP).
"""
import re

from odoo.tests import HttpCase, tagged
from odoo.tools.misc import file_open

JS = "shrimp_marketplace/static/src/js/shrimp_scroll_lock.js"
CSS = "shrimp_marketplace/static/src/css/shrimp_scroll_lock.css"


@tagged("post_install", "-at_install")
class TestScrollLock(HttpCase):

    def _paths(self, bundle):
        return [p[0].lstrip("/") for p in self.env["ir.asset"]._get_asset_paths(bundle, {})]

    def _read(self, path):
        with file_open(path) as f:
            return f.read()

    def test_en_el_bundle_del_sitio(self):
        paths = self._paths("web.assets_frontend")
        self.assertIn(JS, paths)
        self.assertIn(CSS, paths)
        # Antes que los scripts del marketplace que la usan.
        self.assertLess(paths.index(JS), paths.index("shrimp_marketplace/static/src/js/shrimp_filter_drawer.js"))
        self.assertLess(paths.index(JS), paths.index("shrimp_marketplace/static/src/js/shrimp_product_form.js"))
        backend = self._paths("web.assets_backend")
        self.assertNotIn(JS, backend)
        self.assertNotIn(CSS, backend)

    def test_se_sirve_en_el_sitio(self):
        res = self.url_open("/marketplace")
        self.assertEqual(res.status_code, 200)
        bundle = self.env["ir.qweb"]._get_asset_bundle("web.assets_frontend", css=False, js=True)
        js = "\n".join(a.content for a in bundle.javascripts if a.url and "shrimp_scroll_lock" in a.url)
        self.assertIn("window.ShrimpScrollLock", js)

    def test_api_y_enganches(self):
        js = self._read(JS)
        for ev in ("show.bs.modal", "hidden.bs.modal", "show.bs.offcanvas", "hidden.bs.offcanvas"):
            self.assertIn('"%s"' % ev, js)
        for name in ("lock:", "unlock:", "isLocked:", "refresh:"):
            self.assertIn(name, js)
        self.assertIn("MutationObserver", js)
        self.assertIn('"touchmove", onTouchMove, {passive: false', js)
        # Overlays propios que se bloquean solos.
        for sel in (".sf-modal", ".buy-modal", ".cv-modal", ".sv-info-modal", ".pl-modal",
                    ".s-pay-overlay", ".shrimp-sg", ".s-drawer", "[data-shrimp-scroll-lock]"):
            self.assertIn('"%s"' % sel, js)
        # Sin position:fixed en body (movería el header fijo y los sticky).
        self.assertNotIn("position = \"fixed\"", js)

    def test_cajon_y_capa_de_envio_usan_la_api(self):
        drawer = self._read("shrimp_marketplace/static/src/js/shrimp_filter_drawer.js")
        self.assertIn("window.ShrimpScrollLock", drawer)
        self.assertIn("SL.lock(drawer)", drawer)
        self.assertIn("SL.unlock(drawer)", drawer)
        guard = self._read("shrimp_user_registry/static/src/js/submit_guard.js")
        self.assertIn("ShrimpScrollLock.lock(", guard)
        self.assertIn("ShrimpScrollLock.unlock(", guard)

    def test_nunca_html_y_body_a_la_vez(self):
        hojas = [
            "shrimp_marketplace/static/src/css/shrimp_marketplace.css",
            "shrimp_user_registry/static/src/css/shrimp_signup.css",
            CSS,
        ]
        malo = re.compile(r"html\.[\w-]+\s*,\s*html\.[\w-]+\s+body\s*\{[^}]*overflow\s*:\s*hidden")
        for hoja in hojas:
            self.assertFalse(malo.search(self._read(hoja)), hoja)
        # Los respaldos propios se apagan cuando carga la utilidad (si no, el
        # body se bloquearía antes de medir la barra y el header saltaría).
        self.assertIn('html.classList.add("shrimp-sl-ready")', self._read(JS))
        self.assertIn("html.s-drawer-lock:not(.shrimp-sl-ready) body", self._read(hojas[0]))
        self.assertIn("html.shrimp-sg-open:not(.shrimp-sl-ready) body", self._read(hojas[1]))
        css = self._read(CSS)
        self.assertIn("html.shrimp-sl-on:not(.shrimp-sl-root) body", css)
        self.assertIn("overscroll-behavior: contain", css)
