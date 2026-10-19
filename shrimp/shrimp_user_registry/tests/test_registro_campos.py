"""Asistente de alta 19.0.1.9.0: sección única de certificados (catálogo por
perfil, obligatorios, una sola línea por certificado aunque se elijan varios
perfiles) y acordeón de documentos legales."""
import html as htmllib
import json
import re

from odoo.tests import HttpCase, tagged

from .legal_utils import signup_post

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


@tagged("post_install", "-at_install")
class TestRegistroCampos(HttpCase):

    def _cert(self, role, name):
        return self.env["shrimp.certificate"].sudo().create({
            "name": name, "role": role, "issuer": "Emisor %s" % name, "expires_required": True,
            "duration_value": 1, "duration_period": "years"})

    def _post(self, roles, correo, certs=(), **extra):
        html = self.url_open("/register").text
        datos = signup_post(self.env, roles, correo, csrf_token=_csrf(html))
        files = {}
        for i, (cert, nombre) in enumerate(certs):
            datos.update({"cert_line_%s_id" % i: cert.uuid_ref, "cert_line_%s_number" % i: "N-%s" % i,
                          "cert_line_%s_expiry_date" % i: "2027-12-31"})
            files["cert_line_%s_file" % i] = (nombre, PDF, "application/pdf")
        datos.update(extra)
        return self.url_open("/register/submit", data=datos, files=files or None, allow_redirects=False)

    def _socio(self, correo):
        return self.env["res.partner"].sudo().search([("email", "=", correo)], limit=1)

    def test_pagina_trae_catalogo_y_acordeon(self):
        cert = self._cert("camaronera", "Cert de prueba campos")
        html = self.url_open("/register").text
        m = re.search(r'id="shrimpCerts" data-catalog="([^"]*)"', html)
        self.assertTrue(m, "la sección única de certificados debe traer el catálogo")
        catalogo = json.loads(htmllib.unescape(m.group(1)))
        self.assertIn(cert.uuid_ref, [c["ref"] for c in catalogo.get("camaronera", [])])
        self.assertIn('id="shrimpCertModal"', html)
        # Acordeón: botón con aria-controls y la casilla fuera del botón.
        self.assertRegex(html, r'class="shrimp-legal-toggle"[^>]*aria-controls="legal_body_privacy"')
        self.assertRegex(html, r'<input[^>]*name="legal_accept_privacy"[^>]*required')
        self.assertIn('id="shrimpLegalAll"', html)
        self.assertIn("Abrir en una página nueva", html)

    def test_certificados_varios_perfiles_una_linea_cada_uno(self):
        cam = self._cert("camaronera", "Cert cam campos")
        lab = self._cert("laboratorio", "Cert lab campos")
        todos = self._cert("all", "Cert todos campos")
        r = self._post(["laboratorio", "camaronera"], "multi.cert@prueba.test",
                       certs=[(cam, "cam.pdf"), (lab, "lab.pdf"), (todos, "todos.pdf")])
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self._socio("multi.cert@prueba.test")
        lineas = self.env["shrimp.user.certificate.line"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(sorted(lineas.mapped("certificate_id.name")),
                         sorted(["Cert cam campos", "Cert lab campos", "Cert todos campos"]))
        self.assertEqual(set(lineas.mapped("status")), {"pending"})
        self.assertTrue(all(lineas.mapped("file_attachment_id")))

    def test_certificado_obligatorio(self):
        req = self._cert("semillero", "Cert obligatorio campos")
        req.required_at_signup = True
        r = self._post(["semillero"], "sin.req@prueba.test")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Adjunta los documentos obligatorios", r.text)
        self.assertFalse(self._socio("sin.req@prueba.test"))
        r = self._post(["semillero"], "con.req@prueba.test", certs=[(req, "req.pdf")])
        self.assertIn(r.status_code, (302, 303))
        self.assertTrue(self._socio("con.req@prueba.test"))

    def test_certificado_de_otro_perfil_rechazado(self):
        lab = self._cert("laboratorio", "Cert lab ajeno campos")
        r = self._post(["camaronera"], "ajeno@prueba.test", certs=[(lab, "lab.pdf")])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self._socio("ajeno@prueba.test"))

    # ---- 19.0.1.10.1: la camaronera ya no declara hectáreas al registrarse ----
    def test_bloque_camaronera_sin_hectareas_piscinas_ni_capacidad(self):
        html = self.url_open("/register").text
        m = re.search(r'id="section_camaronera".*?<div id="shrimp_prof_sections_end"', html, re.S)
        self.assertTrue(m, "debe estar el bloque de la camaronera")
        bloque = m.group(0)
        for nombre in ("farm_area_ha", "shrimp_num_piscinas", "farm_capacidad"):
            self.assertNotIn('name="%s"' % nombre, bloque)
        self.assertNotIn("Las hectáreas son obligatorias", html)
        self.assertIn('name="shrimp_registro_acuicola"', bloque)
        self.assertIn('name="shrimp_permiso_ambiental"', bloque)
        self.assertIn("Mis instalaciones", bloque)

    def test_camaronera_sin_hectareas_se_registra(self):
        r = self._post(["camaronera"], "sin.ha@prueba.test",
                       shrimp_registro_acuicola="GR-1104", shrimp_permiso_ambiental="MAATE-77")
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self._socio("sin.ha@prueba.test")
        self.assertTrue(socio)
        self.assertEqual(socio.shrimp_user_type, "camaronera")
        self.assertFalse(socio.farm_area_ha)
        self.assertFalse(socio.shrimp_num_piscinas)
        self.assertFalse(socio.shrimp_capacity_value)
        self.assertEqual(socio.shrimp_registro_acuicola, "GR-1104")
        self.assertEqual(socio.shrimp_permiso_ambiental, "MAATE-77")

    def test_camaronera_con_hectareas_legacy_se_guardan(self):
        # Integraciones viejas que aún mandan los tres datos: se guardan.
        r = self._post(["camaronera"], "con.ha@prueba.test", farm_area_ha="12.5",
                       shrimp_num_piscinas="8", farm_capacidad="120")
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self._socio("con.ha@prueba.test")
        self.assertAlmostEqual(socio.farm_area_ha, 12.5)
        self.assertEqual(socio.shrimp_num_piscinas, 8)
        self.assertEqual((socio.shrimp_capacity_value, socio.shrimp_capacity_unit), (120.0, "ton_year"))

    # ---- 19.0.1.10.1: tabla única de certificados con «Perfil al que aplica» ----
    def test_tabla_unica_en_el_asistente(self):
        html = self.url_open("/register").text
        self.assertIn('id="shrimpCertBody"', html)
        self.assertIn('id="shrimpCertAdd"', html)
        self.assertIn('id="shrimpCmRole"', html)
        self.assertIn("Perfil al que aplica", html)
        self.assertRegex(html, r'id="shrimpCerts"[^>]*data-profiles="\[')
        for col in ("Certificado", "Perfil", "N.º", "Vence", "Archivo", "Estado", "Acciones"):
            self.assertIn("<th scope=\"col\"%s>%s</th>" % (
                ' class="shrimp-tech-acts-h"' if col == "Acciones" else "", col), html)
        self.assertNotIn("shrimp-cert-group", html)

    def _post_roles(self, roles, correo, filas):
        """filas: [(cert, perfil, nombre_archivo)] -> cert_line_<n>_* con _role."""
        html = self.url_open("/register").text
        datos = signup_post(self.env, roles, correo, csrf_token=_csrf(html))
        files = {}
        for i, (cert, perfil, nombre) in enumerate(filas):
            datos.update({"cert_line_%s_id" % i: cert.uuid_ref, "cert_line_%s_role" % i: perfil,
                          "cert_line_%s_number" % i: "N-%s" % i,
                          "cert_line_%s_expiry_date" % i: "2027-12-31"})
            files["cert_line_%s_file" % i] = (nombre, PDF, "application/pdf")
        return self.url_open("/register/submit", data=datos, files=files or None, allow_redirects=False)

    def test_tabla_certificados_de_dos_perfiles(self):
        cam = self._cert("camaronera", "Cert cam tabla")
        lab = self._cert("laboratorio", "Cert lab tabla")
        todos = self._cert("all", "Cert todos tabla")
        r = self._post_roles(["laboratorio", "camaronera"], "tabla.ok@prueba.test",
                             [(cam, "camaronera", "cam.pdf"), (lab, "laboratorio", "lab.pdf"),
                              (todos, "laboratorio", "todos.pdf")])
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self._socio("tabla.ok@prueba.test")
        lineas = self.env["shrimp.user.certificate.line"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(sorted(lineas.mapped("certificate_id.name")),
                         ["Cert cam tabla", "Cert lab tabla", "Cert todos tabla"])
        self.assertEqual(set(lineas.mapped("status")), {"pending"})

    def test_tabla_certificado_de_otro_perfil_rechazado(self):
        lab = self._cert("laboratorio", "Cert lab cruzado")
        r = self._post_roles(["laboratorio", "camaronera"], "tabla.cruzado@prueba.test",
                             [(lab, "camaronera", "lab.pdf")])
        self.assertEqual(r.status_code, 200)
        self.assertIn("no corresponde al perfil", r.text)
        self.assertFalse(self._socio("tabla.cruzado@prueba.test"))

    def test_tabla_perfil_no_elegido_rechazado(self):
        todos = self._cert("all", "Cert todos no elegido")
        r = self._post_roles(["camaronera"], "tabla.noelegido@prueba.test",
                             [(todos, "laboratorio", "x.pdf")])
        self.assertEqual(r.status_code, 200)
        self.assertIn("un perfil que no elegiste", r.text)
        self.assertFalse(self._socio("tabla.noelegido@prueba.test"))
