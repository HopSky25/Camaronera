"""Protección de datos (LOPDP): consentimiento en el alta, canal de derechos
del titular (/my/data/request) y aceptación de la versión vigente.

19.0.1.6.0: el consentimiento ya no es una casilla única con una versión fija
en el código, sino una aceptación por documento versionado
(shrimp.legal.*). Ver también test_legal.py."""
import re
from urllib.parse import parse_qs, urlsplit

from odoo.tests import HttpCase, new_test_user, tagged

from .legal_utils import legal_accept_fields, signup_post

CLAVE = "Clave-segura-123"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


@tagged("post_install", "-at_install")
class TestPrivacidad(HttpCase):

    def _portal(self, login, accept=False):
        user = new_test_user(self.env, login=login, password=CLAVE, groups="base.group_portal",
                             name="Socio %s" % login)
        if accept:
            docs = self.env["shrimp.legal.document"].sudo()._shrimp_general_documents()
            self.env["shrimp.legal.acceptance"].sudo()._shrimp_record(
                user, docs.mapped("current_version_id"), "signup", generate_receipt=False)
        return user

    # ------------------------------------------------------------ alta
    def test_alta_sin_aceptar_no_crea_usuario(self):
        token = _csrf(self.url_open("/register").text)
        datos = signup_post(self.env, ["semillero"], "sin.consent@prueba.test", csrf_token=token)
        for k in list(datos):
            if k.startswith("legal_accept_"):
                datos.pop(k)
        r = self.url_open("/register/submit", data=datos)
        self.assertEqual(r.status_code, 200)
        self.assertIn("tienes que leer y aceptar", r.text)
        self.assertFalse(self.env["res.users"].sudo().search([("login", "=", "sin.consent@prueba.test")]))

    def test_alta_guarda_version_e_ip(self):
        token = _csrf(self.url_open("/register").text)
        self.url_open("/register/submit", data=signup_post(
            self.env, ["semillero"], "con.consent@prueba.test", csrf_token=token))
        socio = self.env["res.partner"].sudo().search([("email", "=", "con.consent@prueba.test")])
        self.assertTrue(socio)
        vigente = self.env.ref("shrimp_user_registry.legal_doc_privacy").current_version_id
        self.assertEqual(socio.shrimp_privacy_version, vigente.name)
        self.assertTrue(socio.shrimp_privacy_accepted_at)
        self.assertTrue(socio.shrimp_privacy_ip)

    def _usar_sitio(self, plataforma):
        """Apunta el sitio de `plataforma` a la URL de los tests (si el campo
        de plataforma existe, es decir, con shrimp_marketplace)."""
        W = self.env["website"].sudo()
        if "shrimp_platform" not in W._fields:
            return
        sitio = W.search([("shrimp_platform", "=", plataforma)], order="id", limit=1)
        if not sitio:
            return
        for otra in W.search([("id", "!=", sitio.id)]):
            otra.domain = "http://otro-%s.invalid" % otra.id
        self.env.flush_all()
        sitio.domain = self.base_url().rstrip("/")
        self.env.flush_all()
        self.env.registry.clear_cache()

    def test_los_formularios_piden_el_consentimiento(self):
        formularios = [("shrimp_user_registry", "/register"),
                       ("shrimp_packer", "/register/packer"),
                       ("shrimp_copacking", "/register/copacker"),
                       ("shrimp_verification", "/register/verifier")]
        Module = self.env["ir.module.module"].sudo()
        probados = 0
        for modulo, ruta in formularios:
            if not Module.search_count([("name", "=", modulo), ("state", "=", "installed")]):
                continue
            with self.subTest(ruta=ruta):
                # Perfiles por sitio: el alta de la verificadora y la del
                # maquilador se sirven en su propio sitio (desde el principal
                # redirigen a él).
                self._usar_sitio({"/register/copacker": "copacker",
                                  "/register/verifier": "verifier"}.get(ruta, "main"))
                r = self.url_open(ruta, allow_redirects=False)
                self.assertEqual(r.status_code, 200)
                self.assertRegex(r.text, r'<input[^>]*name="legal_accept_privacy"[^>]*required')
                self.assertRegex(r.text, r'<input[^>]*name="legal_accept_contract"[^>]*required')
                self.assertIn('href="/privacy"', r.text)
                self.assertIn('href="/legal/terms"', r.text)
                self.assertIn('href="/legal/cookies"', r.text)
                probados += 1
        self.assertGreaterEqual(probados, 1)

    # ------------------------------------------------------------ páginas
    def test_paginas_legales_y_dpo(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("shrimp.dpo_email", False)
        r = self.url_open("/privacy")
        self.assertEqual(r.status_code, 200)
        self.assertIn("contacta al administrador de la plataforma", r.text)
        self.assertNotIn("datos@camaronmarket.com", r.text)
        ICP.set_param("shrimp.dpo_email", "dpo@empresa.test")
        self.assertIn("mailto:dpo@empresa.test", self.url_open("/privacy").text)
        self.assertEqual(self.url_open("/legal/terms").status_code, 200)

    # ------------------------------------------------------------ derechos
    def test_solicitud_escapa_html_y_va_en_cola(self):
        self.env["ir.config_parameter"].sudo().set_param("shrimp.dpo_email", "dpo@empresa.test")
        user = self._portal("titular.datos", accept=True)
        self.authenticate("titular.datos", CLAVE)
        token = _csrf(self.url_open("/my/data").text)
        self.assertTrue(token)
        carga = '<script>alert(1)</script><img src=x onerror=alert(2)><a href="javascript:x">y</a>'
        r = self.url_open("/my/data/request", data={
            "csrf_token": token, "tipo": "acceso", "detalle": carga}, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertIn("/my/data?ok=1", r.headers["Location"])
        msg = self.env["mail.message"].sudo().search([
            ("model", "=", "res.partner"), ("res_id", "=", user.partner_id.id),
            ("subject", "like", "Solicitud de derechos")], limit=1)
        self.assertTrue(msg)
        for cuerpo in (str(msg.body),):
            self.assertNotIn("<script>", cuerpo)
            self.assertNotIn("<img", cuerpo)
            self.assertNotIn('<a href="javascript', cuerpo)
            self.assertIn("&lt;script&gt;", cuerpo)
        correo = self.env["mail.mail"].sudo().search([("email_to", "=", "dpo@empresa.test")],
                                                     order="id desc", limit=1)
        self.assertTrue(correo)
        self.assertEqual(correo.state, "outgoing", "el correo queda en cola, no se envía en la petición")
        self.assertNotIn("<script>", correo.body_html or "")
        self.assertNotIn("<img", correo.body_html or "")
        self.assertIn("&lt;script&gt;", correo.body_html or "")

    def test_solicitudes_limitadas_por_usuario(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.data_request_rate_limit", "2")
        user = self._portal("titular.limite", accept=True)
        self.authenticate("titular.limite", CLAVE)
        token = _csrf(self.url_open("/my/data").text)
        destinos = []
        for _i in range(3):
            r = self.url_open("/my/data/request", data={
                "csrf_token": token, "tipo": "acceso", "detalle": "x"}, allow_redirects=False)
            destinos.append(r.headers.get("Location", ""))
        self.assertTrue(destinos[0].endswith("ok=1") and destinos[1].endswith("ok=1"))
        self.assertTrue(destinos[2].endswith("ok=limit"))
        self.assertEqual(self.env["mail.message"].sudo().search_count([
            ("model", "=", "res.partner"), ("res_id", "=", user.partner_id.id),
            ("subject", "like", "Solicitud de derechos")]), 2)

    # ------------------------------------------------------------ re-consentimiento
    def test_aceptacion_de_la_version_vigente(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        user = self._portal("socio.antiguo")
        self.authenticate("socio.antiguo", CLAVE)
        r = self.url_open("/my/account?x=1", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        destino = urlsplit(r.headers["Location"])
        self.assertEqual(destino.path, "/my/legal/accept")
        self.assertEqual(parse_qs(destino.query)["redirect"], ["/my/account?x=1"])
        # Las páginas legales, los derechos y la salida no se bloquean.
        for libre in ("/privacy", "/legal/terms", "/legal/cookies", "/legal/contract", "/my/data"):
            with self.subTest(libre=libre):
                self.assertEqual(self.url_open(libre, allow_redirects=False).status_code, 200)
        # La ruta anterior queda como alias.
        r = self.url_open("/my/privacy/accept?redirect=%2Fmy%2Faccount", allow_redirects=False)
        self.assertEqual(urlsplit(r.headers["Location"]).path, "/my/legal/accept")
        pagina = self.url_open("/my/legal/accept?redirect=%2Fmy%2Faccount")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn('name="legal_accept_privacy"', pagina.text)
        token = _csrf(pagina.text)
        # Sin marcar las casillas obligatorias no se guarda nada.
        self.url_open("/my/legal/accept", data={"csrf_token": token, "redirect": "/my/account"})
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        self.assertFalse(Acc.search([("user_id", "=", user.id)]))
        datos = {"csrf_token": token, "redirect": "/my/account"}
        datos.update(legal_accept_fields(self.env, []))
        r = self.url_open("/my/legal/accept", data=datos, allow_redirects=False)
        self.assertEqual(urlsplit(r.headers["Location"]).path, "/my/account")
        user.partner_id.invalidate_recordset()
        vigente = self.env.ref("shrimp_user_registry.legal_doc_privacy").current_version_id
        self.assertEqual(user.partner_id.shrimp_privacy_version, vigente.name)
        self.assertTrue(user.partner_id.shrimp_privacy_accepted_at)
        self.assertTrue(user.partner_id.shrimp_privacy_ip)
        self.assertEqual(self.url_open("/my/account", allow_redirects=False).status_code, 200)
        self.assertEqual(set(Acc.search([("user_id", "=", user.id)]).mapped("channel")), {"login_gate"})
        # Un redirect a otro dominio no se sigue.
        r = self.url_open("/my/legal/accept?redirect=https://malo.test/x", allow_redirects=False)
        self.assertEqual(urlsplit(r.headers["Location"]).netloc in ("", urlsplit(self.base_url()).netloc), True)

    def test_internos_no_pasan_por_la_aceptacion(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        new_test_user(self.env, login="interno.priv", password=CLAVE, groups="base.group_user")
        self.authenticate("interno.priv", CLAVE)
        r = self.url_open("/my/account", allow_redirects=False)
        self.assertEqual(r.status_code, 200)
