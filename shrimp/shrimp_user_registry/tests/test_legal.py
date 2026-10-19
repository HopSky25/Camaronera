"""Documentos legales versionados ("contrato virtual") y alta por perfiles.

- Versiones: hash SHA-256 al publicar, inmutabilidad, publicar sustituye a la
  anterior, «Nueva versión» copia el texto.
- Aceptaciones: inmutables (ni sudo escribe ni borra), comprobante PDF (y su
  reintento por cron si falla), migración de la aceptación anterior.
- Puerta de acceso: MAYOR bloquea, MENOR solo avisa.
- Alta: cada perfil (también la empacadora, que faltaba) registra una
  aceptación por documento general y el anexo de su perfil.
- Anexo obligatorio al activar o agregar un perfil.
- /my/data: solo los comprobantes propios.
- Páginas públicas, historial y /cookie-policy.
"""
import re
from unittest.mock import patch
from urllib.parse import urlsplit

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from .legal_utils import cedula_valida, legal_accept_fields, ruc_natural_valido, signup_post

CLAVE = "Clave-segura-123"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


def _socio(env, login, roles=("camaronera",)):
    """Cuenta portal con perfiles (dueña y administradora de la cuenta)."""
    partner = env["res.partner"].sudo().create({
        "name": "Socio %s" % login, "email": "%s@prueba.test" % login,
        "vat_or_id": ruc_natural_valido(), "shrimp_user_type": roles[0],
        "shrimp_razon_social": "RS %s" % login, "shrimp_representante": "Rep",
        "shrimp_telefono": "04-1", "shrimp_ubicacion": "El Oro"})
    for r in roles[1:]:
        partner._shrimp_request_role(r)
    user = new_test_user(env, login=login, password=CLAVE, groups="base.group_portal",
                         partner_id=partner.id, name=partner.name, email=partner.email)
    env["shrimp.account.member"].sudo()._shrimp_ensure(user, partner, is_admin=True)
    return user


@tagged("post_install", "-at_install")
class TestLegalModelos(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Doc = cls.env["shrimp.legal.document"]
        cls.Version = cls.env["shrimp.legal.document.version"]
        cls.Acc = cls.env["shrimp.legal.acceptance"]
        cls.terms = cls.env.ref("shrimp_user_registry.legal_doc_terms")
        cls.cookies = cls.env.ref("shrimp_user_registry.legal_doc_cookies")

    def test_hash_y_publicacion(self):
        v = self.terms.current_version_id
        self.assertEqual(v.state, "published")
        self.assertEqual(len(v.content_hash), 64)
        self.assertTrue(v.check_integrity())
        # Sin borradores previos (shrimp_marketplace crea uno con las reglas de
        # entregas y retrasos): esta prueba es la de «copiar la vigente».
        self.terms.version_ids.filtered(lambda x: x.state == "draft").unlink()
        accion = self.terms.action_new_version()
        nueva = self.Version.browse(accion["res_id"])
        self.assertEqual(nueva.state, "draft")
        self.assertEqual(nueva.content, v.content, "la nueva versión copia el texto vigente")
        self.assertNotEqual(nueva.name, v.name)
        # Pedirla otra vez abre el mismo borrador.
        self.assertEqual(self.terms.action_new_version()["res_id"], nueva.id)
        with self.assertRaises(UserError):
            nueva.action_publish()  # falta el resumen de cambios
        nueva.write({"content": "<p>Términos nuevos</p>", "change_summary": "Cambio de prueba",
                     "change_type": "major"})
        nueva.action_publish()
        self.assertEqual(nueva.state, "published")
        self.assertEqual(v.state, "superseded")
        self.assertTrue(v.superseded_date)
        self.assertEqual(self.terms.current_version_id, nueva)
        import hashlib
        self.assertEqual(nueva.content_hash,
                         hashlib.sha256((nueva.content or "").encode()).hexdigest())

    def test_version_publicada_inmutable(self):
        v = self.terms.current_version_id
        for vals in ({"content": "<p>otro</p>"}, {"name": "1999-01"}, {"change_type": "minor"},
                     {"state": "draft"}, {"content_hash": "x"}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                v.sudo().write(vals)
        with self.assertRaises(UserError):
            v.sudo().unlink()
        borrador = self.Version.create({"document_id": self.terms.id, "name": "borrador-x",
                                        "content": "<p>x</p>"})
        with self.assertRaises(UserError):
            borrador.write({"state": "published"})
        borrador.unlink()  # un borrador sí se borra

    def test_aceptacion_inmutable_y_comprobante(self):
        user = _socio(self.env, "legal.inmut")
        vers = self.terms.current_version_id | self.cookies.current_version_id
        acc = self.Acc._shrimp_record(user, vers, "signup", ip="10.0.0.1", user_agent="UA test")
        self.assertEqual(len(acc), 2)
        a = acc[0]
        self.assertEqual(a.user_id, user)
        self.assertEqual(a.commercial_partner_id, user.partner_id)
        self.assertEqual(a.content_hash, a.version_id.content_hash)
        self.assertEqual(len(a.receipt_hash), 64)
        self.assertEqual(a.receipt_state, "done")
        self.assertTrue(a.receipt_attachment_id)
        self.assertEqual(a.receipt_attachment_id.res_model, "shrimp.legal.acceptance")
        # Inmutable para todos, también en sudo.
        with self.assertRaises(AccessError):
            a.sudo().write({"ip": "1.1.1.1"})
        with self.assertRaises(AccessError):
            a.sudo().unlink()
        with self.assertRaises(AccessError):
            a.receipt_attachment_id.sudo().unlink()
        # Nadie la crea sin pasar por _shrimp_record.
        with self.assertRaises(AccessError):
            self.Acc.sudo().create({"document_id": self.terms.id,
                                    "version_id": self.terms.current_version_id.id})
        # Un portal no la lee por RPC.
        with self.assertRaises(AccessError):
            self.Acc.with_user(user).search([])

    def test_comprobante_que_falla_se_reintenta(self):
        user = _socio(self.env, "legal.retry")
        Report = type(self.env["ir.actions.report"])
        with patch.object(Report, "_render_qweb_pdf", side_effect=Exception("wkhtmltopdf caído")):
            acc = self.Acc._shrimp_record(user, self.cookies.current_version_id, "signup")
        self.assertTrue(acc.exists(), "la aceptación se guarda aunque falle el PDF")
        self.assertEqual(acc.receipt_state, "error")
        self.assertEqual(acc.receipt_attempts, 1)
        self.Acc._cron_generate_receipts()
        self.assertEqual(acc.receipt_state, "done")
        self.assertTrue(acc.receipt_attachment_id)

    def test_estado_mayor_y_menor(self):
        user = _socio(self.env, "legal.estado")
        empresa = user.partner_id
        # Nunca aceptó: privacidad (mayor) bloquea; cookies (menor) solo avisa.
        priv = self.env.ref("shrimp_user_registry.legal_doc_privacy")
        self.assertEqual(priv._shrimp_status(user, empresa)["status"], "major")
        self.assertEqual(self.cookies._shrimp_status(user, empresa)["status"], "none")
        pend = self.Doc._shrimp_pending(user)
        self.assertIn(priv, [st["document"] for st in pend["major"]])
        self.assertIn(self.cookies, [st["document"] for st in pend["minor"]])
        # El anexo de su perfil también aplica.
        anexo = self.Doc._shrimp_annex("camaronera")
        self.assertIn(anexo, [st["document"] for st in pend["major"] + pend["minor"]])
        self.Acc._shrimp_record(user, priv.current_version_id | self.cookies.current_version_id,
                                "login_gate", generate_receipt=False)
        self.assertEqual(priv._shrimp_status(user, empresa)["status"], "ok")
        # Nueva versión MENOR de cookies: aviso; MAYOR de privacidad: bloquea.
        for doc, tipo, esperado in ((self.cookies, "minor", "minor"), (priv, "major", "major")):
            nueva = self.Version.browse(doc.action_new_version()["res_id"])
            nueva.write({"change_summary": "x", "change_type": tipo, "content": "<p>v2</p>"})
            nueva.action_publish()
            self.assertEqual(doc._shrimp_status(user, empresa)["status"], esperado)

    def test_migracion_de_la_aceptacion_anterior(self):
        user = _socio(self.env, "legal.migra")
        user.partner_id.sudo().write({"shrimp_privacy_version": "2026-10",
                                      "shrimp_privacy_ip": "200.1.1.1"})
        creadas = self.Acc._shrimp_migrate_legacy_privacy()
        self.assertGreaterEqual(creadas, 2)
        accs = self.Acc.sudo().search([("user_id", "=", user.id)])
        self.assertEqual(set(accs.mapped("document_id.doc_type")), {"privacy", "terms"})
        self.assertEqual(set(accs.mapped("channel")), {"migration"})
        self.assertEqual(set(accs.mapped("receipt_state")), {"migrated"})
        self.assertEqual(set(accs.mapped("ip")), {"200.1.1.1"})
        # Idempotente.
        self.assertEqual(self.Acc._shrimp_migrate_legacy_privacy(), 0)


@tagged("post_install", "-at_install")
class TestLegalPortal(HttpCase):

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _instalado(self, modulo):
        return bool(self.env["ir.module.module"].sudo().search_count(
            [("name", "=", modulo), ("state", "=", "installed")]))

    def test_comprobante_pdf_real(self):
        # wkhtmltopdf pide los estilos al servidor: el de este test.
        self.env["ir.config_parameter"].sudo().set_param("report.url", self.base_url())
        user = _socio(self.env, "legal.pdf")
        cookies = self.env.ref("shrimp_user_registry.legal_doc_cookies")
        acc = self.env["shrimp.legal.acceptance"].with_context(force_report_rendering=True)._shrimp_record(
            user, cookies.current_version_id, "signup", ip="10.0.0.2")
        self.assertEqual(acc.receipt_state, "done", acc.receipt_error)
        contenido, mime, _nombre = acc.receipt_content()
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(contenido.startswith(b"%PDF"))

    # ------------------------------------------------------------ páginas públicas
    def test_paginas_publicas_e_historial(self):
        for ruta in ("/privacy", "/legal/terms", "/legal/cookies", "/legal/contract",
                     "/legal/contract/camaronera", "/legal/contract/semillero",
                     "/privacy/history", "/legal/terms/history", "/legal/cookies/history",
                     "/legal/contract/history", "/legal/contract/camaronera/history",
                     "/legal/terms/history/2026-10", "/legal/contract/history/2026-10",
                     "/privacy/history/2026-10"):
            with self.subTest(ruta=ruta):
                r = self.url_open(ruta)
                self.assertEqual(r.status_code, 200, ruta)
        self.assertIn("Naupliero", self.url_open("/legal/contract/semillero").text)
        self.assertEqual(self.url_open("/legal/contract/noexiste").status_code, 404)
        self.assertEqual(self.url_open("/legal/terms/history/1900-01").status_code, 404)
        # Una versión sustituida se sigue viendo en el historial.
        terms = self.env.ref("shrimp_user_registry.legal_doc_terms")
        vieja = terms.current_version_id
        nueva = self.env["shrimp.legal.document.version"].browse(terms.action_new_version()["res_id"])
        nueva.write({"name": "2027-01", "change_summary": "Resumen visible", "content": "<p>Texto 2027</p>"})
        nueva.action_publish()
        self.assertIn("Texto 2027", self.url_open("/legal/terms").text)
        hist = self.url_open("/legal/terms/history").text
        self.assertIn("Resumen visible", hist)
        self.assertIn("2026-10", hist)
        antigua = self.url_open("/legal/terms/history/%s" % vieja.name).text
        self.assertIn("versión anterior", antigua)
        # La política de cookies nativa de Odoo lleva a la nuestra.
        r = self.url_open("/cookie-policy", allow_redirects=False)
        self.assertIn(r.status_code, (301, 302, 303))
        self.assertEqual(urlsplit(r.headers["Location"]).path, "/legal/cookies")

    # ------------------------------------------------------------ alta por perfil
    def test_alta_con_cada_perfil_registra_aceptaciones(self):
        perfiles = ["semillero", "laboratorio", "camaronera"]
        if self._instalado("shrimp_packer"):
            perfiles.append("empacadora")
        pagina = self.url_open("/register").text
        for p in perfiles:
            # La causa del error reportado: la empacadora no aparecía en el
            # formulario del sitio principal.
            self.assertRegex(pagina, r'name="shrimp_user_types"[^>]*value="%s"' % p)
        # Perfiles por sitio: la verificadora y el maquilador se registran
        # (y aceptan su anexo) solo en su propio sitio; el principal no los
        # ofrece (shrimp_copacking/tests/test_perfiles_por_sitio.py).
        for propio in ("verificador", "maquilador"):
            self.assertNotRegex(pagina, r'name="shrimp_user_types"[^>]*value="%s"' % propio)
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        generales = self.env["shrimp.legal.document"].sudo()._shrimp_general_documents()
        for p in perfiles:
            with self.subTest(perfil=p):
                correo = "alta.%s@prueba.test" % p
                token = _csrf(self.url_open("/register").text)
                r = self.url_open("/register/submit", data=signup_post(
                    self.env, [p], correo, csrf_token=token), allow_redirects=False)
                self.assertIn(r.status_code, (302, 303), r.text[:3000] if r.status_code == 200 else "")
                user = self.env["res.users"].sudo().search([("login", "=", correo)])
                self.assertTrue(user)
                socio = user.partner_id
                self.assertEqual(socio.shrimp_user_type, p)
                aprobacion = socio._shrimp_type_can(p, "requires_approval")
                self.assertEqual(socio.shrimp_account_state, "pending" if aprobacion else "approved")
                accs = Acc.search([("user_id", "=", user.id)])
                anexo = self.env["shrimp.legal.document"].sudo()._shrimp_annex(p)
                self.assertEqual(accs.mapped("document_id"), generales | anexo)
                self.assertEqual(set(accs.mapped("channel")), {"signup"})
                self.assertTrue(all(a.ip for a in accs))
                self.assertTrue(all(a.receipt_attachment_id for a in accs), "comprobante generado")
                self.assertEqual(accs.filtered(lambda a: a.document_id == anexo).role, p)
                miembro = user._shrimp_member()
                self.assertTrue(miembro.is_admin, "quien crea la cuenta es su administrador")

    def test_alta_con_varios_perfiles(self):
        roles = ["camaronera"] + (["empacadora"] if self._instalado("shrimp_packer") else ["laboratorio"])
        token = _csrf(self.url_open("/register").text)
        self.url_open("/register/submit", data=signup_post(
            self.env, roles, "alta.multi@prueba.test", csrf_token=token))
        socio = self.env["res.partner"].sudo().search([("email", "=", "alta.multi@prueba.test")])
        self.assertTrue(socio)
        self.assertEqual(socio.shrimp_user_type, "camaronera", "activo: el que no requiere aprobación")
        self.assertEqual(set(socio._shrimp_roles(include_pending=True)), set(roles))
        anexos = self.env["shrimp.legal.acceptance"].sudo().search(
            [("commercial_partner_id", "=", socio.id), ("document_id.doc_type", "=", "annex")])
        self.assertEqual(set(anexos.mapped("role")), set(roles))

    def test_alta_rechaza_identificacion_invalida_y_anexo_faltante(self):
        buena = cedula_valida()
        cedula_mala = buena[:-1] + str((int(buena[-1]) + 1) % 10)
        token = _csrf(self.url_open("/register").text)
        r = self.url_open("/register/submit", data=signup_post(
            self.env, ["camaronera"], "alta.vat@prueba.test", vat=cedula_mala, csrf_token=token))
        self.assertIn("no son válidos", r.text)
        datos = signup_post(self.env, ["camaronera"], "alta.anexo@prueba.test", csrf_token=token)
        datos.pop("legal_accept_annex_camaronera")
        r = self.url_open("/register/submit", data=datos)
        self.assertIn("Anexo de perfil: Camaronera", r.text)
        # Una versión desactualizada tampoco vale.
        datos = signup_post(self.env, ["camaronera"], "alta.vieja@prueba.test", csrf_token=token,
                            legal_accept_terms="1900-01")
        r = self.url_open("/register/submit", data=datos)
        self.assertIn("Se publicó una versión nueva", r.text)
        Users = self.env["res.users"].sudo()
        self.assertFalse(Users.search([("login", "in", ["alta.vat@prueba.test", "alta.anexo@prueba.test",
                                                         "alta.vieja@prueba.test"])]))

    # ------------------------------------------------------------ puerta: mayor vs menor
    def test_puerta_mayor_bloquea_y_menor_avisa(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        user = _socio(self.env, "legal.puerta")
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        # Acepta todo lo vigente: entra sin pasar por la puerta.
        Acc._shrimp_record(user, self.env["shrimp.legal.document.version"].browse(
            [v.id for _d, v in self.env["shrimp.legal.document"].sudo()._shrimp_signup_documents(
                ["camaronera"])]), "signup", generate_receipt=False)
        self.authenticate("legal.puerta", CLAVE)
        r = self.url_open("/my/account", allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("shrimpLegalBanner", r.text)
        # MENOR (contrato): no bloquea; aviso en el portal.
        contrato = self.env.ref("shrimp_user_registry.legal_doc_contract")
        v = self.env["shrimp.legal.document.version"].browse(contrato.action_new_version()["res_id"])
        v.write({"change_summary": "Ajuste menor", "change_type": "minor"})
        v.action_publish()
        r = self.url_open("/my/account", allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("shrimpLegalBanner", r.text)
        # «Recordármelo luego» lo oculta en la sesión.
        token = _csrf(r.text)
        self.url_open("/my/legal/dismiss", data={"csrf_token": token, "redirect": "/my/account"})
        self.assertNotIn("shrimpLegalBanner", self.url_open("/my/account").text)
        # MAYOR (privacidad): bloquea hasta aceptarla.
        priv = self.env.ref("shrimp_user_registry.legal_doc_privacy")
        v2 = self.env["shrimp.legal.document.version"].browse(priv.action_new_version()["res_id"])
        v2.write({"change_summary": "Cambio importante", "change_type": "major"})
        v2.action_publish()
        r = self.url_open("/my/account", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(urlsplit(r.headers["Location"]).path, "/my/legal/accept")
        pagina = self.url_open("/my/legal/accept?redirect=/my/account").text
        self.assertIn("Cambio importante", pagina)
        self.assertIn("Obligatorio", pagina)
        datos = {"csrf_token": _csrf(pagina), "redirect": "/my/account",
                 "legal_accept_privacy": v2.name}
        self.url_open("/my/legal/accept", data=datos)
        self.assertEqual(self.url_open("/my/account", allow_redirects=False).status_code, 200)
        self.assertTrue(Acc.search([("user_id", "=", user.id), ("version_id", "=", v2.id),
                                    ("channel", "=", "login_gate")]))

    # ------------------------------------------------------------ anexo al activar/agregar perfil
    def test_anexo_obligatorio_al_activar_perfil(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        user = _socio(self.env, "legal.anexo", roles=("camaronera", "laboratorio"))
        socio = user.partner_id
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        # Generales + anexo de camaronera aceptados; el de laboratorio, no.
        Acc._shrimp_record(user, self.env["shrimp.legal.document.version"].browse(
            [v.id for _d, v in self.env["shrimp.legal.document"].sudo()._shrimp_signup_documents(
                ["camaronera"])]), "signup", generate_receipt=False)
        self.authenticate("legal.anexo", CLAVE)
        token = _csrf(self.url_open("/my/data").text)
        r = self.url_open("/my/profile/activate", data={
            "csrf_token": token, "role": "laboratorio", "next": "/my/data"}, allow_redirects=False)
        self.assertEqual(urlsplit(r.headers["Location"]).path, "/my/legal/accept")
        self.assertEqual(user.shrimp_active_role, "camaronera", "sin aceptar el anexo no se activa")
        pagina = self.url_open(r.headers["Location"]).text
        self.assertIn("Anexo de perfil: Laboratorio", pagina)
        anexo = self.env["shrimp.legal.document"].sudo()._shrimp_annex("laboratorio")
        self.url_open("/my/legal/accept", data={
            "csrf_token": _csrf(pagina), "redirect": "/my/data", "activate": "laboratorio",
            "legal_accept_annex_laboratorio": anexo.current_version_id.name})
        # Perfil activo POR USUARIO (CR-12): cambia el del usuario.
        self.assertEqual(user.shrimp_active_role, "laboratorio")
        acc = Acc.search([("user_id", "=", user.id), ("document_id", "=", anexo.id)])
        self.assertEqual(acc.channel, "profile")
        self.assertEqual(acc.role, "laboratorio")

    def test_mis_datos_solo_comprobantes_propios(self):
        a = _socio(self.env, "legal.mio")
        b = _socio(self.env, "legal.ajeno")
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        terms = self.env.ref("shrimp_user_registry.legal_doc_terms").current_version_id
        mia = Acc._shrimp_record(a, terms, "signup")
        ajena = Acc._shrimp_record(b, terms, "signup")
        self.authenticate("legal.mio", CLAVE)
        pagina = self.url_open("/my/data").text
        self.assertIn(mia.uuid_ref, pagina)
        self.assertNotIn(ajena.uuid_ref, pagina)
        r = self.url_open("/my/legal/receipt/%s" % mia.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.url_open("/my/legal/receipt/%s" % ajena.uuid_ref).status_code, 404)
        self.assertEqual(self.url_open("/my/legal/receipt/%s" % ajena.id).status_code, 404)
