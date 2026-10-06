"""Rutas web en inglés y compatibilidad con las viejas en español.

Vale para lo que esté instalado: la tabla de rutas viejas la extiende cada
módulo shrimp (ir.http._shrimp_legacy_routes), así que con toda la cadena
instalada (post_install) se comprueban las ~190 rutas renombradas.
"""
import importlib.util
import os
import re
from urllib.parse import urlsplit

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_user_registry.models import ir_http as shrimp_ir_http

PARAM = re.compile(r"<(?:[a-z_]+:)?([a-z_]+)>")
VALOR = "ABC123"

# Tramos literales permitidos en las rutas web de los módulos shrimp. Una ruta
# nueva con un tramo que no esté aquí hace fallar el test: o es inglés y se
# añade, o es español y hay que traducirla (y sumar el par a la tabla legacy).
TRAMOS_EN = {
    "accept", "acceptance", "accreditation", "activate", "add", "allocate", "api",
    "api-keys", "approve", "archive", "arrival", "assign", "assign-technician",
    "avatar", "bonuses", "buy", "calendar", "cancel", "cards", "certificate",
    "certificates", "check-request", "check-requests", "close", "commit",
    "commitments", "compare", "complete", "confirm", "confirmations", "copacker",
    "copacking", "correct", "count", "counter-offer", "cover", "create",
    "dashboard", "deactivate", "declare", "delete", "desist", "detail", "discard",
    "dispatch", "duplicate", "edit", "events", "exports", "facilities", "facility",
    "file", "harvest", "image", "inbox", "invoice", "lines", "list-price",
    "marketplace", "move-profile", "my", "my-account", "my-certificate",
    "my-certificates", "my-facilities", "my-lots", "my-track-record", "new",
    "notify-me", "offer", "offers", "orders", "packer", "packers", "packing", "pdf",
    "pending", "photo", "photos", "ping", "planning", "plants", "pond", "ponds",
    "pre-reservations", "preference", "price-lists", "product",
    "product-certificates", "production", "production-calendar", "products",
    "profile", "publish", "purchase-history", "purchases", "quality", "rate",
    "rate-buyer", "reactivate", "receive", "reception", "register", "reject",
    "renew", "reopen", "report", "reports", "request-check", "requests",
    "reservations", "revoke", "sales", "sampling", "save", "self", "seller-invoice",
    "sellers", "settlements", "sign", "simulator", "sowings", "start", "submit",
    "suggested-price", "suppliers", "supply", "t", "tariffs", "technicians",
    "template", "thanks", "toggle", "traceability", "undo", "undo-signature",
    "unpublish", "update", "upload", "user-certificate", "v1", "verdict",
    "verifications", "verifier", "verifiers", "verify", "webhooks", "withdraw",
}


def _llenar(ruta):
    return PARAM.sub(VALOR, ruta)


def _tramos(ruta):
    return [t for t in ruta.strip("/").split("/") if t and not t.startswith("<")]


@tagged("post_install", "-at_install")
class TestRutasLegacy(HttpCase):

    def setUp(self):
        super().setUp()
        self.IrHttp = self.env["ir.http"]
        self.pares = self.IrHttp._shrimp_legacy_routes()
        self.mapa = self.IrHttp.routing_map().bind("")

    def _es_ruta(self, path):
        try:
            self.mapa.match(path, method="GET")
        except MethodNotAllowed:
            return True     # existe, pero solo por POST
        except RequestRedirect:
            return True
        except NotFound:
            return False
        return True

    # ------------------------------------------------------------------
    def test_tabla_coherente(self):
        viejas = [v for v, _n in self.pares]
        self.assertEqual(len(viejas), len(set(viejas)), "ruta vieja repetida en la tabla")
        self.assertGreaterEqual(len(self.pares), 5)
        for vieja, nueva in self.pares:
            with self.subTest(vieja=vieja):
                self.assertNotEqual(vieja, nueva)
                self.assertEqual(sorted(PARAM.findall(vieja)), sorted(PARAM.findall(nueva)),
                                 "los <param> de la vieja y la nueva deben coincidir")
                self.assertFalse(set(_tramos(nueva)) - TRAMOS_EN,
                                 "la ruta nueva %s tiene tramos que no son inglés" % nueva)

    def test_ruta_nueva_existe_y_la_vieja_no(self):
        """La nueva es una ruta real; la vieja NO (si lo fuera taparía al
        redirector y la barra creería que existe)."""
        for vieja, nueva in self.pares:
            with self.subTest(vieja=vieja):
                self.assertTrue(self._es_ruta(_llenar(nueva)), "%s no es una ruta instalada" % nueva)
                self.assertFalse(self._es_ruta(_llenar(vieja)), "%s sigue siendo una ruta" % vieja)

    def test_vieja_redirige_301_con_query(self):
        for vieja, nueva in self.pares:
            with self.subTest(vieja=vieja):
                r = self.url_open(_llenar(vieja) + "?a=1&b=x%20y", allow_redirects=False)
                self.assertEqual(r.status_code, 301)
                destino = urlsplit(r.headers["Location"])
                self.assertEqual(destino.path, _llenar(nueva))
                self.assertEqual(destino.query, "a=1&b=x%20y")

    def test_vieja_sin_query_y_head(self):
        for vieja, nueva in self.pares[:20]:
            with self.subTest(vieja=vieja):
                r = self.url_open(_llenar(vieja), allow_redirects=False, method="HEAD")
                self.assertEqual(r.status_code, 301)
                destino = urlsplit(r.headers["Location"])
                self.assertEqual((destino.path, destino.query), (_llenar(nueva), ""))
        # Barra final: el núcleo la quita (301 a /registro) y luego el
        # redirector lleva a /register.
        r = self.url_open("/registro/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(urlsplit(r.url).path, "/register")

    def test_post_viejo_308(self):
        """POST (formulario o JSON con la página vieja en caché): 308, que
        conserva método y cuerpo; la query sigue, el cuerpo no va a la URL."""
        for vieja, nueva in self.pares:
            with self.subTest(vieja=vieja):
                r = self.url_open(_llenar(vieja) + "?x=1", data={"campo": "secreto"},
                                  allow_redirects=False)
                self.assertEqual(r.status_code, 308)
                destino = urlsplit(r.headers["Location"])
                self.assertEqual(destino.path, _llenar(nueva))
                self.assertEqual(destino.query, "x=1")

    def test_parametros_se_conservan_y_escapan(self):
        if not any(v == "/registro" for v, _n in self.pares):
            self.skipTest("sin tabla base")
        target = self.IrHttp._shrimp_legacy_target
        self.assertEqual(target("/registro"), "/register")
        self.assertIsNone(target("/register"))
        self.assertIsNone(target("/no-existe"))
        con_param = [(v, n) for v, n in self.pares if "<" in v]
        for vieja, nueva in con_param[:10]:
            with self.subTest(vieja=vieja):
                self.assertEqual(target(PARAM.sub("a b", vieja)), PARAM.sub("a%20b", nueva))

    def test_reescribir_url_guardada(self):
        rw = self.IrHttp._shrimp_legacy_rewrite_url
        self.assertEqual(rw("/registro?tipo=x#paso2"), "/register?tipo=x#paso2")
        self.assertEqual(rw("/register?tipo=x"), "/register?tipo=x")
        self.assertEqual(rw("/marketplace"), "/marketplace")
        self.assertEqual(rw("#"), "#")
        self.assertEqual(rw("https://otro.test/registro"), "https://otro.test/registro")
        for vieja, nueva in self.pares:
            with self.subTest(vieja=vieja):
                self.assertEqual(rw(_llenar(vieja) + "?f=1"), _llenar(nueva) + "?f=1")
                # idempotente
                self.assertEqual(rw(_llenar(nueva) + "?f=1"), _llenar(nueva) + "?f=1")

    # ------------------------------------------------------------------
    def _es_ruta_del_nucleo(self, endpoint, ruta):
        """True si `ruta` la declara una clase que no es de shrimp (p. ej.
        «/», /web/signup: shrimp solo la sobreescribe)."""
        func = getattr(endpoint, "func", None)
        nombre = getattr(func, "__name__", None)
        duenio = getattr(func, "__self__", None)
        if not nombre or duenio is None:
            return False
        for cls in type(duenio).mro():
            if cls.__module__.startswith("odoo.addons.shrimp"):
                continue
            metodo = cls.__dict__.get(nombre)
            routing = getattr(metodo, "original_routing", None) or {}
            if ruta in (routing.get("routes") or []):
                return True
        return False

    def test_rutas_shrimp_en_ingles(self):
        """Toda ruta web de un módulo shrimp está en inglés (salvo /t/<token>,
        que también está en la lista)."""
        revisadas = 0
        for regla in self.IrHttp.routing_map().iter_rules():
            ep = regla.endpoint
            if not (getattr(ep, "__module__", "") or "").startswith("odoo.addons.shrimp"):
                continue
            if self._es_ruta_del_nucleo(ep, regla.rule):
                continue
            revisadas += 1
            with self.subTest(ruta=regla.rule):
                self.assertFalse(set(_tramos(regla.rule)) - TRAMOS_EN,
                                 "ruta con tramos fuera de la lista en inglés: %s" % regla.rule)
                self.assertIsNone(self.IrHttp._shrimp_legacy_target(_llenar(regla.rule)))
        self.assertGreater(revisadas, 4)

    # ------------------------------------------------------------------
    def test_prefijos_del_saneo(self):
        """El saneo de ?message=/?error= cubre las rutas nuevas y las viejas."""
        prefijos = shrimp_ir_http._PREFIJOS
        for p in ("/register", "/verifier", "/copacker", "/packer", "/my/dashboard",
                  "/my/profile", "/marketplace",
                  "/registro", "/verificador", "/maquilador", "/empacadora", "/mi-panel"):
            self.assertIn(p, prefijos)
        for vieja, nueva in self.pares:
            with self.subTest(nueva=nueva):
                self.assertTrue(nueva.startswith(prefijos), "%s sin saneo de mensajes" % nueva)
                self.assertTrue(vieja.startswith(prefijos), "%s sin saneo de mensajes" % vieja)

    def test_registro_no_refleja_texto_de_la_url(self):
        r = self.url_open("/register?error=Texto+inyectado+en+registro&message=Otro+texto+libre")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Texto inyectado en registro", r.text)
        self.assertNotIn("Otro texto libre", r.text)

    # ------------------------------------------------------------------
    def test_migracion_reescribe_menus(self):
        """end-migrate 19.0.1.4.0: los website.menu con rutas viejas pasan a
        las nuevas (con su query) y es idempotente."""
        ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "migrations", "19.0.1.4.0", "end-migrate.py")
        spec = importlib.util.spec_from_file_location("shrimp_ur_end_migrate_1940", ruta)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        Menu = self.env["website.menu"]
        raiz = self.env.ref("website.main_menu")
        viejo = Menu.create({"name": "Viejo", "url": "/registro?tipo=x", "parent_id": raiz.id})
        ingles = Menu.create({"name": "Nuevo", "url": "/register", "parent_id": raiz.id})
        ancla = Menu.create({"name": "Ancla", "url": "#", "parent_id": raiz.id})
        extra = []
        for vieja, nueva in self.pares:
            if "<" not in vieja:
                extra.append((Menu.create({"name": vieja, "url": vieja + "?f=1",
                                           "parent_id": raiz.id}), nueva + "?f=1"))
        sitemap = self.env["ir.attachment"].create({
            "name": "sitemap", "url": "/sitemap-1.xml", "raw": b"<x/>", "type": "binary"})
        for _vez in range(2):
            mod.migrate(self.env.cr, "19.0.1.3.0")
            self.env.invalidate_all()
            self.assertEqual(viejo.url, "/register?tipo=x")
            self.assertEqual(ingles.url, "/register")
            self.assertEqual(ancla.url, "#")
            for menu, esperado in extra:
                self.assertEqual(menu.url, esperado)
        self.assertFalse(sitemap.exists())
        for menu in Menu.search([("url", "=like", "/%")]):
            with self.subTest(menu=menu.url):
                self.assertEqual(self.IrHttp._shrimp_legacy_rewrite_url(menu.url), menu.url,
                                 "menú con ruta vieja: %s" % menu.url)
