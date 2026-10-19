"""La verificadora como perfil del asistente común (19.0.1.17.0): se registra
con la misma validación que el formulario propio que reemplaza (acreditación
obligatoria, ver_*, equipo opcional).

Perfiles por sitio: la verificadora se registra SOLO en la plataforma de
verificadores (donde es el único perfil); el marketplace no la ofrece y
enlaza a su alta. Las pruebas entran por el sitio de verificadores (su
dominio apunta a la URL de los tests)."""
import html
import json
import re

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_user_registry.tests.legal_utils import signup_post

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


@tagged("post_install", "-at_install")
class TestRegistroVerificadora(HttpCase):

    def setUp(self):
        super().setUp()
        self._usar_sitio("verifier")

    def _usar_sitio(self, plataforma):
        W = self.env["website"].sudo()
        sitio = W._shrimp_platform_site(plataforma)
        if not sitio:
            self.skipTest("sin sitio de la plataforma %s" % plataforma)
        for otra in W.search([("id", "!=", sitio.id)]):
            otra.domain = "http://otro-%s.invalid" % otra.id
        self.env.flush_all()
        sitio.domain = self.base_url().rstrip("/")
        self.env.flush_all()
        self.env.registry.clear_cache()
        return sitio

    def _post(self, roles, correo, acreditacion=True, **extra):
        html = self.url_open("/register/verifier").text
        datos = signup_post(self.env, roles, correo, csrf_token=_csrf(html))
        datos.update({
            "ver_representante": "Ing. Rosa Vera", "ver_telefono": "0987654321",
            "ver_entidad_acredita": "SAE", "ver_registro_num": "LIC-77",
            "ver_provincias": "El Oro, Guayas", "ver_radio_km": "120", "ver_equipo_propio": "1",
            "ver_fee_base": "45", "ver_fee_por_lb": "0.002",
            "ver_bank_name": "Banco del Pacífico", "ver_bank_account_type": "ahorros",
            "ver_bank_account_number": "1234567890",
            "team_0_name": "Técnico Uno", "team_0_email": "tec.%s" % correo, "team_0_password": "Clave-tecnico-1",
        })
        files = None
        if acreditacion:
            cert = self.env.ref("shrimp_verification.cert_acreditacion_verificador")
            datos.update({"cert_line_0_id": cert.uuid_ref, "cert_line_0_number": "ACR-1",
                          "cert_line_0_expiry_date": "2028-01-31"})
            files = {"cert_line_0_file": ("acreditacion.pdf", PDF, "application/pdf")}
        datos.update(extra)
        return self.url_open("/register/submit", data=datos, files=files, allow_redirects=False)

    def _socio(self, correo):
        return self.env["res.partner"].sudo().search([("email", "=", correo)], limit=1)

    def test_tarjeta_en_el_asistente_del_sitio(self):
        html = self.url_open("/register").text  # → /register/verifier
        self.assertRegex(html, r'name="shrimp_user_types"[^>]*value="verificador"')
        self.assertIn('id="section_verificador"', html)
        self.assertIn('name="ver_representante"', html)

    def test_el_marketplace_no_la_ofrece(self):
        verif = self.env["website"].sudo()._shrimp_platform_site("verifier")
        self._usar_sitio("main")
        verif.domain = "http://verificadores.example.test"
        self.env.flush_all()
        html = self.url_open("/register").text
        self.assertNotRegex(html, r'name="shrimp_user_types"[^>]*value="verificador"')
        self.assertNotIn('id="section_verificador"', html)
        self.assertIn("http://verificadores.example.test/register/verifier", html)
        r = self.url_open("/register/verifier", allow_redirects=False)
        self.assertEqual(r.headers.get("Location"), "http://verificadores.example.test/register/verifier")

    def test_register_verifier_es_el_asistente(self):
        html = self.url_open("/register/verifier").text
        self.assertIn('id="shrimpRegisterForm"', html)
        self.assertRegex(html, r'value="verificador"[^>]*checked')
        self.assertRegex(html, r'<input[^>]*name="legal_accept_annex_verificador"')

    def test_alta_verificadora_con_acreditacion(self):
        r = self._post(["verificador"], "verif.asist@prueba.test")
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self._socio("verif.asist@prueba.test")
        self.assertEqual(socio.shrimp_user_type, "verificador")
        self.assertEqual(socio.shrimp_representante, "Ing. Rosa Vera")
        self.assertEqual(socio.ver_registro_num, "LIC-77")
        self.assertEqual(socio.ver_radio_km, 120)
        self.assertTrue(socio.ver_equipo_propio)
        self.assertEqual(socio.ver_bank_account_type, "ahorros")
        linea = self.env["shrimp.user.certificate.line"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(linea.certificate_id, self.env.ref("shrimp_verification.cert_acreditacion_verificador"))
        self.assertEqual(linea.status, "pending")
        # El técnico del equipo opcional, como en el formulario anterior.
        self.assertTrue(self.env["res.users"].sudo().search([("login", "=", "tec.verif.asist@prueba.test")]))
        # Anexo del verificador aceptado.
        annex = self.env["shrimp.legal.document"].sudo()._shrimp_annex("verificador")
        self.assertTrue(self.env["shrimp.legal.acceptance"].sudo().search_count([
            ("document_id", "in", annex.ids)]))

    def test_sin_acreditacion_rechazada(self):
        r = self._post(["verificador"], "verif.sinacr@prueba.test", acreditacion=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("acreditación", r.text)
        self.assertFalse(self._socio("verif.sinacr@prueba.test"))

    def test_camaronera_y_verificadora(self):
        # En la plataforma de verificadores la camaronera no se registra (se
        # usa en el marketplace); la cuenta puede agregarla luego allí.
        r = self._post(["camaronera", "verificador"], "cam.verif@prueba.test")
        self.assertEqual(r.status_code, 200)
        self.assertIn("se usa en su propio sitio", r.text)
        self.assertFalse(self._socio("cam.verif@prueba.test"))
        # Y en el marketplace la verificadora tampoco.
        self._usar_sitio("main")
        html = self.url_open("/register").text
        datos = signup_post(self.env, ["camaronera", "verificador"], "cam.verif2@prueba.test",
                            csrf_token=_csrf(html))
        r = self.url_open("/register/submit", data=datos, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("se usa en su propio sitio", r.text)
        self.assertFalse(self._socio("cam.verif2@prueba.test"))

    # ---- Equipo de campo con modal + tabla (19.0.1.18.0) ----
    def test_equipo_modal_en_el_asistente(self):
        html = self.url_open("/register/verifier").text
        self.assertIn('id="shrimpTeamModal"', html)
        self.assertIn('id="teamBody"', html)
        self.assertIn("/shrimp_verification/static/src/js/signup_team.js", html)
        self.assertIn('data-initial="[]"', html)
        # El repetidor en línea anterior ya no está.
        self.assertNotIn('id="teamRoleTpl"', html)
        # Los cargos del catálogo están en el modal.
        rol = self.env.ref("shrimp_verification.tech_role_supervisor")
        self.assertIn('value="%s"' % rol.uuid_ref, html)

    def test_dos_tecnicos_con_indices_salteados(self):
        # La tabla renumera, pero el servidor acepta cualquier índice; el cargo
        # va por su código y un correo repetido se ignora sin romper el alta.
        rol = self.env.ref("shrimp_verification.tech_role_supervisor")
        r = self._post(["verificador"], "verif.equipo@prueba.test", **{
            "team_0_name": "Ana  Campo", "team_0_email": "Ana.Campo@prueba.test",
            "team_0_password": "Clave-ana-123", "team_0_role": rol.uuid_ref,
            "team_3_name": "Luis Lab", "team_3_email": "luis.lab@prueba.test",
            "team_3_password": "Clave-luis-123", "team_3_role": "",
            "team_7_name": "Repetido", "team_7_email": "luis.lab@prueba.test",
            "team_7_password": "Clave-otro-123",
        })
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self._socio("verif.equipo@prueba.test")
        tecnicos = self.env["res.partner"].sudo().search(
            [("parent_id", "=", socio.id), ("shrimp_is_field_tech", "=", True)], order="name")
        self.assertEqual(tecnicos.mapped("name"), ["Ana Campo", "Luis Lab"])
        ana, luis = tecnicos
        self.assertEqual(ana.email, "ana.campo@prueba.test")
        self.assertEqual(ana.tech_role_id, rol)
        self.assertEqual(luis.function, "Técnico de campo")
        Member = self.env["shrimp.account.member"].sudo()
        for t in tecnicos:
            usuario = self.env["res.users"].sudo().search([("partner_id", "=", t.id)])
            self.assertTrue(usuario.has_group("base.group_portal"))
            self.assertTrue(Member.search_count([("user_id", "=", usuario.id)]))

    def test_tecnicos_vuelven_tras_un_error(self):
        # Con un error del servidor las filas vuelven a la tabla, SIN contraseña.
        r = self._post(["verificador"], "verif.error@prueba.test", acreditacion=False)
        self.assertEqual(r.status_code, 200)
        m = re.search(r'id="shrimpTeam"[^>]*data-initial="([^"]*)"', r.text)
        self.assertTrue(m, "falta data-initial en el bloque del equipo")
        inicial = json.loads(html.unescape(m.group(1)))
        self.assertEqual(inicial, [{"name": "Técnico Uno", "email": "tec.verif.error@prueba.test", "role": ""}])
        self.assertNotIn("Clave-tecnico-1", r.text)

    # ---- Tabla única de certificados (shrimp_user_registry 19.0.1.10.1) ----
    def test_tabla_unica_y_aviso_de_acreditacion(self):
        html = self.url_open("/register/verifier").text
        self.assertIn('id="shrimpCertBody"', html)
        self.assertIn('id="shrimpCmRole"', html)
        self.assertIn("Debes adjuntar la acreditación que te autoriza a verificar "
                      "(certificado con rol Verificador).", html)
        self.assertNotIn("shrimp-cert-group", html)

    def test_sin_acreditacion_mismo_mensaje_con_tabla(self):
        # Otro certificado (de «Todos») con su perfil, pero sin acreditación.
        otro = self.env["shrimp.certificate"].sudo().create({
            "name": "Cert general tabla", "role": "all", "issuer": "Emisor", "expires_required": False})
        r = self._post(["verificador"], "verif.tabla.sin@prueba.test", acreditacion=False,
                       cert_line_0_id=otro.uuid_ref, cert_line_0_role="verificador")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Debes adjuntar la acreditación que te autoriza a verificar", r.text)
        self.assertFalse(self._socio("verif.tabla.sin@prueba.test"))

    def test_acreditacion_con_perfil_en_la_fila(self):
        r = self._post(["verificador"], "verif.tabla.ok@prueba.test",
                       cert_line_0_role="verificador")
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        # La acreditación asignada a otro perfil no vale: no es de ese perfil.
        r = self._post(["verificador"], "verif.tabla.mal@prueba.test",
                       cert_line_0_role="camaronera")
        self.assertEqual(r.status_code, 200)
        self.assertIn("se asignó a un perfil que no elegiste", r.text)
        self.assertFalse(self._socio("verif.tabla.mal@prueba.test"))
