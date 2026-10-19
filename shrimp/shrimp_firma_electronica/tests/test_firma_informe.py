"""Firma electrónica (PAdES) del informe de verificación.

Los certificados se generan aquí mismo con `cryptography` (autofirmados) y se
empaquetan en PKCS#12 en memoria: el test nunca toca un .p12 en disco.
"""
import base64
import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from asn1crypto import x509 as a_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, csrf_de
from odoo.addons.shrimp_verification.tests.common_seguridad import (
    abrir_ronda, montar_verificacion)

from ..tools import pades

CLAVE_P12 = "Firma-Secreta-7781"
_CLAVE_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def hacer_p12(cn="MARÍA JOSÉ PEÑA ROMERO", cedula="0912345678",
              emisor="AC de Pruebas CamaronMarket", desde_dias=-1, hasta_dias=365,
              uso_firma=True, clave=CLAVE_P12):
    """(.p12 en bytes, certificado) de un certificado autofirmado de prueba."""
    ahora = datetime.now(timezone.utc)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, cedula),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "EC"),
    ])
    emisor_dn = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, emisor),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, emisor),
    ])
    constructor = (
        x509.CertificateBuilder()
        .subject_name(sujeto).issuer_name(emisor_dn)
        .public_key(_CLAVE_RSA.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora + timedelta(days=desde_dias))
        .not_valid_after(ahora + timedelta(days=hasta_dias))
        .add_extension(x509.KeyUsage(
            digital_signature=uso_firma, content_commitment=uso_firma,
            key_encipherment=not uso_firma, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False),
            critical=True)
        # RUC en la extensión propia de Security Data (como los certificados reales).
        .add_extension(x509.UnrecognizedExtension(
            x509.ObjectIdentifier("1.3.6.1.4.1.37947.3.11"),
            b"\x0c\x0d" + (cedula + "001").encode()), critical=False)
    )
    cert = constructor.sign(_CLAVE_RSA, hashes.SHA256())
    p12 = pkcs12.serialize_key_and_certificates(
        b"firma", _CLAVE_RSA, cert, None,
        serialization.BestAvailableEncryption(clave.encode()))
    return p12, cert


def pdf_simple():
    """PDF mínimo (reportlab) para las pruebas HTTP, que no pasan por
    wkhtmltopdf: el PDF real ya se prueba en las pruebas del modelo."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 760, "Informe de verificacion (prueba)")
    c.showPage()
    c.save()
    return buf.getvalue()


def _alerta(html):
    """El texto del aviso de error de la página (para los mensajes de fallo)."""
    import re
    m = re.search(r'alert-danger">(.*?)</div>', html, re.S)
    return m.group(1).strip() if m else "(sin aviso de error en la página)"


class _CapturaLog(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.textos = []

    def emit(self, record):
        try:
            self.textos.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


@tagged("post_install", "-at_install")
class TestFirmaInforme(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "fir")
        cls.v.sudo().write({"technician_partner_id": cls.s["tec"].id, "state": "assigned"})
        abrir_ronda(cls.v)
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        # Base de pruebas: certificados autofirmados.
        cls.ICP.set_param("shrimp_firma.solo_emisores_acreditados", "False")
        cls.ICP.set_param("shrimp_firma.obligatoria", "False")
        cls.ICP.set_param("shrimp_firma.tsa_url", "")
        sitio = cls.env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False
        cls.p12, cls.cert = hacer_p12()
        cls.Firma = cls.env["shrimp.verification.firma"].sudo()
        cls.Att = cls.env["ir.attachment"].sudo()

    # ------------------------------------------------------------------
    def _firmar(self, p12=None, clave=CLAVE_P12, partner=None, user=None):
        return self.v.action_firmar_informe(
            p12 or self.p12, clave, partner or self.s["verif"], user=user or self.s["u_verif"])

    def _nada_guardado(self):
        self.v.invalidate_recordset()
        self.assertFalse(self.v.signed_at)
        self.assertFalse(self.v.firma_id)
        self.assertFalse(self.Firma.search([("verification_id", "=", self.v.id)]))
        self.assertFalse(self.Att.search([("res_model", "=", "shrimp.verification.firma")]))

    def _p12_no_esta_en_la_bd(self, p12):
        sha1 = hashlib.sha1(p12).hexdigest()
        self.assertFalse(self.Att.search([("checksum", "=", sha1)]),
                         "el .p12 no puede quedar como adjunto")
        self.assertFalse(self.Att.search(["|", "|", ("name", "ilike", ".p12"),
                                          ("name", "ilike", ".pfx"),
                                          ("mimetype", "ilike", "pkcs12")]))
        b64 = base64.b64encode(p12).decode()[:60]
        cr = self.env.cr
        cr.execute("SELECT count(*) FROM ir_attachment WHERE db_datas = %s", [p12])
        self.assertEqual(cr.fetchone()[0], 0)
        for tabla, columna in (("mail_message", "body"), ("ir_config_parameter", "value"),
                               ("shrimp_verification_firma", "signer_name")):
            cr.execute("SELECT count(*) FROM %s WHERE %s::text ILIKE %%s OR %s::text ILIKE %%s"
                       % (tabla, columna, columna), ["%" + CLAVE_P12 + "%", "%" + b64 + "%"])
            self.assertEqual(cr.fetchone()[0], 0, "%s.%s guarda la firma" % (tabla, columna))
        for registro in (self.v.sudo(), self.v.sudo().firma_ids):
            for valores in registro.read():
                texto = str(valores)
                self.assertNotIn(CLAVE_P12, texto)
                self.assertNotIn(b64, texto)

    # ------------------------------------------------------------------
    # Firma correcta
    # ------------------------------------------------------------------
    def test_firma_ok_y_valida_con_pyhanko(self):
        captura = _CapturaLog()
        raiz = logging.getLogger()
        raiz.addHandler(captura)
        try:
            firma = self._firmar()
        finally:
            raiz.removeHandler(captura)

        v = self.v
        self.assertEqual(v.firma_id, firma)
        self.assertEqual(firma.state, "vigente")
        self.assertTrue(v.signed_at)
        self.assertEqual(v.signed_by_user_id, self.s["u_verif"])
        self.assertEqual(v.signer_name, "MARÍA JOSÉ PEÑA ROMERO")
        self.assertIn("0912345678", v.signer_id_number)
        self.assertIn("0912345678001", v.signer_id_number)
        self.assertIn("AC de Pruebas CamaronMarket", v.signer_issuer)
        self.assertEqual(v.signer_cert_serial, format(self.cert.serial_number, "X"))
        self.assertEqual(v.signer_cert_sha256,
                         self.cert.fingerprint(hashes.SHA256()).hex().upper())
        self.assertEqual(firma.signature_level, "B-B")
        self.assertGreaterEqual(len(firma.token), 40)
        self.assertNotEqual(firma.token, str(v.id))

        # El PDF guardado: hash coincide y la firma valida con pyHanko.
        pdf = v.signed_pdf_attachment_id.raw
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(pdf).hexdigest().upper(), v.signed_pdf_sha256)
        self.assertEqual(v.signed_pdf_attachment_id, firma.attachment_id)

        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko.sign.validation.status import SignatureCoverageLevel
        from pyhanko_certvalidator import ValidationContext
        lector = PdfFileReader(io.BytesIO(pdf))
        firmas = lector.embedded_signatures
        self.assertEqual(len(firmas), 1)
        raiz_confianza = a_x509.Certificate.load(self.cert.public_bytes(serialization.Encoding.DER))
        estado = validate_pdf_signature(
            firmas[0], ValidationContext(trust_roots=[raiz_confianza], allow_fetching=False))
        self.assertTrue(estado.intact, "el PDF firmado no está íntegro")
        self.assertTrue(estado.valid, "la firma no es criptográficamente válida")
        self.assertTrue(estado.trusted)
        self.assertEqual(estado.coverage, SignatureCoverageLevel.ENTIRE_FILE)
        self.assertEqual(estado.signing_cert.sha256.hex().upper(), v.signer_cert_sha256)
        self.assertEqual(firmas[0].sig_object["/SubFilter"], "/ETSI.CAdES.detached")
        # Firma visible (rectángulo con área) en la última página.
        rect = [float(x) for x in firmas[0].sig_field["/Rect"]]
        self.assertGreater(rect[2] - rect[0], 100)
        self.assertGreater(rect[3] - rect[1], 30)

        # Nada del .p12 ni de su contraseña queda en la BD ni en el log.
        self._p12_no_esta_en_la_bd(self.p12)
        b64 = base64.b64encode(self.p12).decode()[:60]
        for texto in captura.textos:
            self.assertNotIn(CLAVE_P12, texto)
            self.assertNotIn(b64, texto)

    # ------------------------------------------------------------------
    # Rechazos
    # ------------------------------------------------------------------
    def test_contrasena_incorrecta(self):
        with self.assertRaises(UserError) as cm:
            self._firmar(clave="no-es-la-clave")
        self.assertIn("contraseña es incorrecta", cm.exception.args[0])
        self.assertNotIn("OpenSSL", cm.exception.args[0])
        self._nada_guardado()
        self._p12_no_esta_en_la_bd(self.p12)

    def test_certificado_vencido(self):
        p12, _cert = hacer_p12(desde_dias=-400, hasta_dias=-5)
        with self.assertRaises(UserError) as cm:
            self._firmar(p12=p12)
        self.assertIn("vencido", cm.exception.args[0])
        self._nada_guardado()

    def test_certificado_aun_no_vigente_y_sin_uso_de_firma(self):
        p12, _cert = hacer_p12(desde_dias=3, hasta_dias=300)
        with self.assertRaises(UserError) as cm:
            self._firmar(p12=p12)
        self.assertIn("todavía no está vigente", cm.exception.args[0])
        p12, _cert = hacer_p12(uso_firma=False)
        with self.assertRaises(UserError) as cm:
            self._firmar(p12=p12)
        self.assertIn("no está habilitado para firmar", cm.exception.args[0])
        self._nada_guardado()

    def test_emisor_no_aceptado_con_la_lista_activa(self):
        self.ICP.set_param("shrimp_firma.solo_emisores_acreditados", "True")
        with self.assertRaises(UserError) as cm:
            self._firmar()
        self.assertIn("acreditada", cm.exception.args[0])
        self._nada_guardado()
        # Un emisor de la lista sí pasa la validación (DN real de Security Data).
        _p12, cert = hacer_p12(emisor="SECURITY DATA S.A. 2")
        self.assertTrue(pades.validar_certificado(
            cert, emisores_aceptados=self.v._firma_emisores()))
        _p12, cert = hacer_p12(emisor="ENTIDAD DE CERTIFICACIÓN DE INFORMACIÓN-ECIBCE")
        self.assertTrue(pades.validar_certificado(
            cert, emisores_aceptados=self.v._firma_emisores()))

    def test_solo_firma_la_verificadora(self):
        # Ni el comprador ni el técnico de otra verificadora.
        for ajeno in (self.s["cam"], self.s["tec_ajeno"], self.s["verif2"]):
            with self.assertRaises(AccessError):
                self._firmar(partner=ajeno, user=self.s["u_cam"])
        self._nada_guardado()
        # Por HTTP: el comprador recibe 403 en el formulario y en el POST.
        self.authenticate("cam.secfir", CLAVE)
        ruta = "/verifier/verifications/%s/sign" % self.v.uuid_ref
        self.assertEqual(self.url_open(ruta).status_code, 403)
        pagina = self.url_open("/marketplace/verifications/%s/acceptance" % self.v.uuid_ref).text
        r = self.url_open(ruta, data={"csrf_token": csrf_de(pagina) or "x",
                                      "contrasena": CLAVE_P12, "acepto": "1"},
                          files={"certificado": ("firma.p12", self.p12, "application/x-pkcs12")})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.url_open(
            "/verifier/verifications/%s/report/pdf" % self.v.uuid_ref).status_code, 403)
        self._nada_guardado()

    def test_segunda_firma_rechazada_y_reapertura_archiva(self):
        primera = self._firmar()
        with self.assertRaises(UserError) as cm:
            self._firmar(partner=self.s["tec"], user=self.s["u_tec"])
        self.assertIn("ya está firmado", cm.exception.args[0])
        self.assertEqual(len(self.v.firma_ids), 1)

        # Si la verificación se reabre, la firma anterior queda archivada y
        # el informe se puede volver a firmar al dictaminar de nuevo.
        self.v.sudo().write({"state": "done"})
        self.assertEqual(primera.state, "archivada")
        self.assertTrue(primera.archived_reason)
        self.assertFalse(self.v.firma_id)
        self.assertFalse(self.v.signed_at)
        self.v.sudo().write({"state": "approved"})
        segunda = self._firmar(partner=self.s["tec"], user=self.s["u_tec"])
        self.assertEqual(segunda.state, "vigente")
        self.assertEqual(self.v.firma_id, segunda)
        self.assertEqual(len(self.v.firma_ids), 2)
        # La página de la firma archivada lo dice.
        r = self.url_open(primera.public_path())
        self.assertEqual(r.status_code, 200)
        self.assertIn("Firma archivada", r.text)

    # ------------------------------------------------------------------
    # Comprobación pública
    # ------------------------------------------------------------------
    def test_pagina_publica_por_token(self):
        firma = self._firmar()
        r = self.url_open(firma.public_path())     # sin sesión: es pública
        self.assertEqual(r.status_code, 200)
        self.assertIn("Informe firmado electrónicamente", r.text)
        self.assertIn("MARÍA JOSÉ PEÑA ROMERO", r.text)
        self.assertIn("AC de Pruebas CamaronMarket", r.text)
        self.assertIn(firma.signed_pdf_sha256, r.text)
        # La cédula no se publica en la página (está en el propio PDF).
        self.assertNotIn("0912345678", r.text)
        r = self.url_open(firma.pdf_path())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Content-Type"], "application/pdf")
        self.assertEqual(hashlib.sha256(r.content).hexdigest().upper(), firma.signed_pdf_sha256)
        # Tokens inventados o por id: 404.
        for falso in ("A" * 43, "x", str(self.v.id), firma.token[:-1] + ("B" if firma.token[-1] != "B" else "C")):
            self.assertEqual(self.url_open("/signature/%s" % falso).status_code, 404)
            self.assertEqual(self.url_open("/signature/%s/pdf" % falso).status_code, 404)

    # ------------------------------------------------------------------
    # Firma obligatoria
    # ------------------------------------------------------------------
    def test_obligatoria_bloquea_la_aceptacion_hasta_firmar(self):
        self.ICP.set_param("shrimp_firma.obligatoria", "True")
        comprador = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        vendedor = self.v.acceptance_ids.filtered(lambda a: a.role == "seller")
        self.assertTrue(self.v.firma_bloquea_aceptacion())
        with self.assertRaises(UserError) as cm:
            comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertIn("firmado electrónicamente", cm.exception.args[0])
        with self.assertRaises(UserError):
            comprador.sudo().action_counter(1.0, actor=self.s["cam"])
        # El cron no da por aceptado por vencimiento lo que no se podía aceptar.
        self.v.sudo().acceptance_deadline = fields.Datetime.now() - timedelta(hours=1)
        self.env["shrimp.verification"]._cron_vencer_plazos()
        self.assertEqual(comprador.decision, "pending")
        self.assertEqual(vendedor.decision, "pending")

        # La pantalla de aceptación lo avisa y no ofrece aceptar.
        self.authenticate("cam.secfir", CLAVE)
        ruta = "/marketplace/verifications/%s/acceptance" % self.v.uuid_ref
        texto = self.url_open(ruta).text
        self.assertIn("Pendiente de firma electrónica de la verificadora", texto)
        self.assertNotIn("/marketplace/verifications/%s/accept\"" % self.v.uuid_ref, texto)

        # Firmado: se puede aceptar y el plazo corre desde la firma.
        self._firmar()
        self.assertFalse(self.v.firma_bloquea_aceptacion())
        self.assertGreater(self.v.acceptance_deadline, fields.Datetime.now())
        comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "accepted")
        texto = self.url_open(ruta).text
        self.assertIn("Informe firmado electrónicamente por", texto)
        self.assertIn("MARÍA JOSÉ PEÑA ROMERO", texto)

    def test_no_obligatoria_permite_aceptar_y_muestra_pendiente(self):
        self.assertFalse(self.v.firma_bloquea_aceptacion())
        self.authenticate("cam.secfir", CLAVE)
        texto = self.url_open("/marketplace/verifications/%s/acceptance" % self.v.uuid_ref).text
        self.assertIn("Pendiente de firma electrónica de la verificadora", texto)
        self.assertIn("/marketplace/verifications/%s/accept\"" % self.v.uuid_ref, texto)
        comprador = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "accepted")

    # ------------------------------------------------------------------
    # Sello de tiempo (PAdES-B-T)
    # ------------------------------------------------------------------
    def _tsa_de_prueba(self):
        """Un DummyTimeStamper de pyHanko con un certificado de TSA propio."""
        from asn1crypto import keys as a_keys
        from cryptography.x509.oid import ExtendedKeyUsageOID
        from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
        ahora = datetime.now(timezone.utc)
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TSA de pruebas")])
        cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
                .public_key(_CLAVE_RSA.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(ahora - timedelta(days=1))
                .not_valid_after(ahora + timedelta(days=30))
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
                .sign(_CLAVE_RSA, hashes.SHA256()))
        return DummyTimeStamper(
            tsa_cert=a_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
            tsa_key=a_keys.PrivateKeyInfo.load(_CLAVE_RSA.private_bytes(
                serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption())))

    def test_sello_de_tiempo_pades_b_t(self):
        self.ICP.set_param("shrimp_firma.tsa_url", "https://tsa.prueba.invalid/tsr")
        tsa = self._tsa_de_prueba()
        urls = []

        def falso_http(url, *args, **kwargs):
            urls.append(url)
            return tsa

        Verificacion = type(self.env["shrimp.verification"])
        with patch.object(Verificacion, "_firma_render_pdf", lambda self, token=None: pdf_simple()), \
                patch("pyhanko.sign.timestamps.HTTPTimeStamper", side_effect=falso_http):
            firma = self._firmar()
        self.assertEqual(urls, ["https://tsa.prueba.invalid/tsr"])
        self.assertEqual(firma.signature_level, "B-T")
        from pyhanko.pdf_utils.reader import PdfFileReader
        emb = PdfFileReader(io.BytesIO(firma.attachment_id.raw)).embedded_signatures[0]
        self.assertTrue(emb.attached_timestamp_data, "la firma B-T lleva el sello de tiempo")

    def test_sello_de_tiempo_caido_no_guarda_nada(self):
        self.ICP.set_param("shrimp_firma.tsa_url", "https://tsa.prueba.invalid/tsr")

        class TsaCaida:
            def __init__(self, *args, **kwargs):
                pass

            def __getattr__(self, nombre):
                raise ConnectionError("TSA caída")

        Verificacion = type(self.env["shrimp.verification"])
        with patch.object(Verificacion, "_firma_render_pdf", lambda self, token=None: pdf_simple()), \
                patch("pyhanko.sign.timestamps.HTTPTimeStamper", TsaCaida), \
                self.assertLogs("odoo.addons.shrimp_firma_electronica.tools.pades", "WARNING"):
            with self.assertRaises(UserError) as cm:
                self._firmar()
        self.assertIn("sello de tiempo", cm.exception.args[0])
        self._nada_guardado()

    # ------------------------------------------------------------------
    # Flujo HTTP del portal del verificador
    # ------------------------------------------------------------------
    def test_flujo_http_del_tecnico(self):
        Verificacion = type(self.env["shrimp.verification"])
        with patch.object(Verificacion, "_firma_render_pdf", lambda self, token=None: pdf_simple()):
            self.authenticate("tec.fir", CLAVE)
            detalle = self.url_open("/verifier/verifications/%s" % self.v.uuid_ref).text
            self.assertIn("Firmar informe con firma electrónica", detalle)
            ruta = "/verifier/verifications/%s/sign" % self.v.uuid_ref
            pagina = self.url_open(ruta)
            self.assertEqual(pagina.status_code, 200)
            token = csrf_de(pagina.text)
            self.assertTrue(token)

            # Contraseña errada: vuelve al formulario con el aviso, nada guardado.
            r = self.url_open(ruta, data={"csrf_token": token, "contrasena": "mala", "acepto": "1"},
                              files={"certificado": ("firma.p12", self.p12, "application/x-pkcs12")})
            self.assertEqual(r.status_code, 200)
            self.assertIn("contraseña es incorrecta", r.text, _alerta(r.text))
            self._nada_guardado()

            # Sin CSRF no hay firma.
            r = self.url_open(ruta, data={"contrasena": CLAVE_P12, "acepto": "1"},
                              files={"certificado": ("firma.p12", self.p12, "application/x-pkcs12")})
            self.assertNotEqual(r.status_code, 200)
            self._nada_guardado()

            r = self.url_open(ruta, data={"csrf_token": token, "contrasena": CLAVE_P12, "acepto": "1"},
                              files={"certificado": ("firma.p12", self.p12, "application/x-pkcs12")})
            self.assertEqual(r.status_code, 200)
            self.assertIn("Informe firmado electrónicamente", r.text)
            self.v.invalidate_recordset()
            self.assertTrue(self.v.signed_at)
            self.assertEqual(self.v.signed_by_user_id, self.s["u_tec"])
            self._p12_no_esta_en_la_bd(self.p12)
            # Ya firmado: el formulario redirige a la verificación.
            r = self.url_open(ruta, allow_redirects=False)
            self.assertIn(r.status_code, (302, 303))
