"""Lo que faltaba del portal en la API para el vendedor (19.0.1.17.0):

* corregir número, fechas o archivo de un certificado propio del lote;
* «mis certificados» de la cuenta: listar, subir, renovar y quitar;
* instalaciones y piscinas con las reglas de Mi cuenta (código no repetido,
  límites, archivar la instalación archiva sus piscinas, foto de la piscina).
"""

import base64

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_marketplace.tests.common import PDF_MIN, PNG_1PX

from .common import ApiCase

PASSWORD = "Clave-api-123"


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiCuentaVendedor(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Cert = cls.env["shrimp.certificate"].sudo()
        cls.pcr = Cert.create({"name": "PCR cuenta", "issuer": "Lab", "role": "all", "code": "PCR-CV",
                               "expires_required": True, "duration_value": 1})
        cls.bap = Cert.create({"name": "BAP cuenta", "issuer": "GSA", "role": "laboratorio",
                               "code": "BAP-CV", "expires_required": False})
        cls.solo_emp = Cert.create({"name": "Solo empacadora", "issuer": "X", "role": "empacadora",
                                    "code": "EMP-CV", "expires_required": False})

    def pdf(self, **extra):
        return dict({"filename": "c.pdf", "data": base64.b64encode(PDF_MIN).decode()}, **extra)

    # ------------------------------------------------------------------
    def test_corregir_certificado_propio_del_lote(self):
        p = self.product
        path = "/products/%s/certificates" % p.uuid_ref
        resp = self.api("POST", path, raw=self.raw_lab, body=self.pdf(
            certificate=self.pcr.uuid_ref, number="A-1", expiry_date="2027-01-01"))
        self.assertEqual(resp.status_code, 201, resp.text)
        line = self.env["shrimp.product.certificate.line"].sudo().search([("uuid_ref", "=", resp.json()["line"]["id"])])
        line.status = "approved"
        resp = self.api("PATCH", "%s/%s" % (path, line.uuid_ref), raw=self.raw_lab,
                        body={"number": "A-2", "issue_date": "2026-01-01"})
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()["line"]
        self.assertEqual((out["number"], out["issue_date"], out["status"]), ("A-2", "2026-01-01", "pending"))
        self.assertEqual(out["expiry_date"], "2027-01-01", "lo que no viene se conserva")
        # Archivo nuevo, y errores en su campo.
        resp = self.api("PATCH", "%s/%s" % (path, line.uuid_ref), raw=self.raw_lab,
                        body={"filename": "n.png", "data": base64.b64encode(PNG_1PX).decode()})
        self.assertEqual(resp.json()["line"]["mime"], "image/png")
        for body, campo in (({"expiry_date": None}, "expiry_date"),
                            ({"expiry_date": "2025-01-01"}, "expiry_date"),
                            ({"data": base64.b64encode(b"<html>").decode(), "filename": "x"}, "data")):
            err = self.assertProblem(self.api("PATCH", "%s/%s" % (path, line.uuid_ref), raw=self.raw_lab,
                                              body=body), 422, "validation-error")
            self.assertEqual(err["errors"][0]["field"], campo)
        # Uno que viene de «mis certificados» se corrige en la cuenta.
        archivo = self.env["ir.attachment"].sudo().create({"name": "b.pdf", "raw": PDF_MIN})
        cuenta = self.env["shrimp.user.certificate.line"].sudo().create({
            "partner_id": self.lab.id, "certificate_id": self.bap.id, "certificate_number": "B-1",
            "file_attachment_id": archivo.id, "status": "approved"})
        ligada = p.sudo().shrimp_link_account_certificate(cuenta)
        err = self.assertProblem(self.api("PATCH", "%s/%s" % (path, ligada.uuid_ref), raw=self.raw_lab,
                                          body={"number": "X"}), 422, "validation-error")
        self.assertIn("cuenta", err["detail"])

    def test_mis_certificados_de_la_cuenta(self):
        data = self.api("GET", "/me/certificates", raw=self.raw_lab).json()
        catalogo = [c["id"] for c in data["catalog"]]
        self.assertIn(self.pcr.uuid_ref, catalogo)
        self.assertIn(self.bap.uuid_ref, catalogo)
        self.assertNotIn(self.solo_emp.uuid_ref, catalogo, "solo los de sus perfiles")
        resp = self.api("POST", "/me/certificates", raw=self.raw_lab, body=self.pdf(
            certificate=self.bap.uuid_ref, number="B-7", issue_date="2026-10-01"))
        self.assertEqual(resp.status_code, 201, resp.text)
        line = resp.json()["line"]
        self.assertEqual((line["status"], line["mime"], line["products"]), ("pending", "application/pdf", 0))
        for body, campo in ((self.pdf(certificate=self.bap.uuid_ref, number="B-7"), "number"),
                            (self.pdf(certificate=self.pcr.uuid_ref), "expiry_date"),
                            (self.pdf(certificate=self.solo_emp.uuid_ref), "certificate"),
                            ({"certificate": self.bap.uuid_ref}, "filename")):
            err = self.assertProblem(self.api("POST", "/me/certificates", raw=self.raw_lab, body=body),
                                     422, "validation-error")
            self.assertEqual(err["errors"][0]["field"], campo, body)
        # Renovar: vuelve a revisión aunque estuviera aprobado.
        rec = self.env["shrimp.user.certificate.line"].sudo().search([("uuid_ref", "=", line["id"])])
        rec.status = "approved"
        resp = self.api("PATCH", "/me/certificates/%s" % line["id"], raw=self.raw_lab,
                        body={"expiry_date": "2028-01-01"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual((resp.json()["line"]["status"], resp.json()["line"]["number"]), ("pending", "B-7"))
        # Ajeno: 404. Quitar.
        self.assertProblem(self.api("PATCH", "/me/certificates/%s" % line["id"], raw=self.raw_a,
                                    body={"number": "x"}), 404)
        resp = self.api("DELETE", "/me/certificates/%s" % line["id"], raw=self.raw_lab)
        self.assertEqual(resp.json()["data"], [])
        # Un subusuario sin «Administrar la cuenta» no sube certificados.
        contacto = self.env["res.partner"].sudo().create({
            "name": "Sub cv", "email": "sub.cv@prueba.test", "parent_id": self.lab.id, "type": "contact"})
        user = self.env["res.users"].sudo().create({
            "name": "Sub cv", "login": "sub.cv", "partner_id": contacto.id, "password": PASSWORD,
            "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.env["shrimp.account.member"].sudo()._shrimp_ensure(
            user, self.lab, permissions={"laboratorio": {"view", "sell"}})
        _k, sub = self.env["shrimp.api.key"].api_create_key(user, "sub", SCOPE_CODES)
        self.assertProblem(self.api("POST", "/me/certificates", raw=sub, body=self.pdf(
            certificate=self.bap.uuid_ref)), 403, "permission-denied")

    def test_instalaciones_y_piscinas_con_las_reglas_del_portal(self):
        resp = self.api("POST", "/facilities", raw=self.raw_lab, body={
            "name": "Lab Norte", "code": "LN", "facility_type": "laboratory", "province": "Santa Elena"})
        self.assertEqual(resp.status_code, 201, resp.text)
        fac = resp.json()["id"]
        err = self.assertProblem(self.api("POST", "/facilities", raw=self.raw_lab, body={
            "name": "Otra", "code": "LN"}), 422, "validation-error")
        self.assertEqual(err["errors"][0]["field"], "code")
        # El mismo código en otro socio sí vale.
        self.assertEqual(self.api("POST", "/facilities", raw=self.raw_a, body={
            "name": "Finca", "code": "LN"}).status_code, 201)
        foto = base64.b64encode(PNG_1PX).decode()
        resp = self.api("POST", "/ponds", raw=self.raw_lab, body={
            "name": "Tanque 1", "code": "T1", "pond_type": "tank", "facility": fac,
            "capacity_mode": "volume", "manual_volume_m3": 12.5, "image": foto})
        self.assertEqual(resp.status_code, 201, resp.text)
        pond = resp.json()
        self.assertTrue(pond["has_image"])
        img = self.api("GET", "/ponds/%s/image" % pond["id"], raw=self.raw_lab)
        self.assertEqual((img.status_code, img.content), (200, PNG_1PX))
        for body, campo in (({"name": "T2", "code": "T1"}, "code"),
                            ({"name": "T2", "depth_m": 80}, "depth_m"),
                            ({"name": "T2", "image": base64.b64encode(b"<html>").decode()}, "image")):
            err = self.assertProblem(self.api("POST", "/ponds", raw=self.raw_lab, body=body),
                                     422, "validation-error")
            self.assertEqual(err["errors"][0]["field"], campo, body)
        resp = self.api("PATCH", "/ponds/%s" % pond["id"], raw=self.raw_lab, body={"image": None, "name": "T-1"})
        self.assertFalse(resp.json()["has_image"])
        # Archivar la instalación archiva sus tanques (siguen en el historial).
        self.assertFalse(self.api("DELETE", "/facilities/%s" % fac, raw=self.raw_lab).json()["active"])
        self.assertFalse(self.api("GET", "/ponds/%s" % pond["id"], raw=self.raw_lab).json()["active"])
        self.assertTrue(any(f["id"] == fac for f in self.api(
            "GET", "/facilities?include_archived=true", raw=self.raw_lab).json()["data"]))
