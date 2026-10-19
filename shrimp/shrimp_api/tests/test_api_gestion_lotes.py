"""Gestionar un lote desde la app sin pasar por el portal (19.0.1.17.0).

* Editar: borrador sin firma; en un lote publicado, precio, cantidad y unidad
  van firmados (`POST /products/{id}:update-terms`) y el resto por PATCH.
  Con compras, los datos críticos bloqueados con el campo en errors[].
  Cambiar la cantidad sin compras mueve el lote de stock base.
* Fotos: subir (JPG/PNG reales, 5 MB, 20 como máximo), portada, orden, bajar
  el archivo y quitar.
* Certificados: ligar uno vigente de «mis certificados», subir uno propio
  (pendiente de revisión) y quitarlo.
* Reactivar un lote retirado va firmado; eliminar un borrador, no.
"""

import base64
import json
import time
import uuid

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_marketplace.tests.common import PDF_MIN, PNG_1PX

from .common import ApiCase
from .test_api_dispositivos import new_keypair, sign

PASSWORD = "Clave-api-123"
JPG_MIN = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiGestionLotes(ApiCase):

    def setUp(self):
        super().setUp()
        failures = getattr(self.env.registry, "_login_failures", None)
        if failures is not None:
            failures.clear()

    # ------------------------------------------------------------------
    def app_with_device(self, login="lab.api"):
        resp = self.api("POST", "/auth/login", body={"login": login, "password": PASSWORD,
                                                     "device": "Pixel"})
        self.assertEqual(resp.status_code, 200, resp.text)
        raw = resp.json()["api_key"]
        priv, pub = new_keypair()
        resp = self.api("POST", "/me/devices", raw=raw, body={
            "name": "Pixel", "platform": "android", "public_key": pub, "alg": "ES256"})
        self.assertEqual(resp.status_code, 201, resp.text)
        return raw, priv, resp.json()["id"]

    def signed_post(self, path, raw, device_id, priv, body):
        data = json.dumps(body).encode()
        idem = str(uuid.uuid4())
        ts = int(time.time())
        h = {"Content-Type": "application/json", "Authorization": "Bearer " + raw,
             "Idempotency-Key": idem, "X-Device-Id": device_id,
             "X-Signature-Timestamp": str(ts),
             "X-Signature": sign(priv, "POST", "/api/v1" + path, ts, idem, data)}
        return self.url_open("/api/v1" + path, data=data, headers=h, method="POST",
                             allow_redirects=False)

    def draft(self, **extra):
        vals = {"name": "PL12 gestión", "seller_partner_id": self.lab.id,
                "seller_role": "laboratorio", "initial_qty": 1000.0, "price": 2.0,
                "uom_id": self.uom_millar.id, "stage_id": self.stage_pl12.id, "state": "draft"}
        vals.update(extra)
        return self.env["shrimp.product"].create(vals)

    def photo_body(self, content=PNG_1PX, name="foto.png", **extra):
        return dict({"filename": name, "data": base64.b64encode(content).decode()}, **extra)

    # ------------------------------------------------------------------
    # Editar
    # ------------------------------------------------------------------
    def test_editar_borrador_y_la_cantidad_mueve_el_stock(self):
        raw, _priv, _dev = self.app_with_device()
        p = self.draft()
        self.assertEqual(p.available_qty, 1000.0)
        resp = self.api("PATCH", "/products/%s" % p.uuid_ref, raw=raw, body={
            "price": 2.4, "initial_qty": 1500, "expected_delivery_date": "2026-12-01",
            "health_status": "PCR negativo"}, headers={"Idempotency-Key": str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["initial_qty"], 1500.0)
        self.assertEqual(data["available_qty"], 1500.0, "el lote de stock sigue a la cantidad")
        self.assertEqual(data["expected_delivery_date"], "2026-12-01")
        self.assertEqual(data["locks"], {"fields": [], "reason": None})
        self.assertEqual(data["photos"], [])
        lot = p.stock_lot_ids
        self.assertEqual((lot.initial_qty, lot.available_qty), (1500.0, 1500.0))
        # Borrar la fecha (null) también vale.
        resp = self.api("PATCH", "/products/%s" % p.uuid_ref, raw=raw,
                        body={"expected_delivery_date": None})
        self.assertIsNone(resp.json()["expected_delivery_date"])

    def test_cantidad_con_movimientos_se_declara(self):
        p = self.draft()
        p.stock_lot_ids._shrimp_internal_move("production", 100.0, "Mortalidad", direction="out")
        body = self.assertProblem(self.api("PATCH", "/products/%s" % p.uuid_ref, raw=self.raw_lab,
                                           body={"initial_qty": 2000}), 422, "business-rule")
        self.assertIn("declarando la producción real", body["detail"])
        self.assertEqual(p.initial_qty, 1000.0)

    def test_publicado_precio_y_cantidad_van_firmados(self):
        raw, priv, dev = self.app_with_device()
        p = self.draft()
        resp = self.signed_post("/products/%s:publish" % p.uuid_ref, raw, dev, priv, {})
        self.assertEqual(resp.json()["state"], "published", resp.text)
        # Por PATCH sin firma: 403 con los campos, y nada cambia.
        body = self.assertProblem(self.api("PATCH", "/products/%s" % p.uuid_ref, raw=raw, body={
            "price": 3.1, "initial_qty": 1200, "health_status": "x"}), 403, "signature-required")
        self.assertEqual(sorted(body["fields"]), ["initial_qty", "price"])
        self.assertIn(":update-terms", body["detail"])
        self.assertEqual(p.price, 2.0)
        # Lo demás de un lote publicado, sin firma (también reenviar el mismo precio).
        resp = self.api("PATCH", "/products/%s" % p.uuid_ref, raw=raw, body={
            "health_status": "Revisado", "price": 2.0, "location": "Santa Elena"})
        self.assertEqual(resp.status_code, 200, resp.text)
        # :update-terms sin firma: 403; firmada: se aplica y queda en la auditoría.
        path = "/products/%s:update-terms" % p.uuid_ref
        self.assertProblem(self.api("POST", path, raw=raw, body={"price": 3.1}), 403,
                           "signature-required")
        resp = self.signed_post(path, raw, dev, priv, {"price": 3.1, "initial_qty": 1200})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["price"]["amount"], 3.1)
        self.assertEqual(resp.json()["available_qty"], 1200.0)
        self.assertEqual(resp.json()["state"], "published")
        self.assertEqual(self.env["shrimp.api.signed.operation"].sudo().search_count(
            [("path", "=", "/api/v1" + path)]), 1)
        # Una clave de integración (ERP) cambia el precio por PATCH como siempre.
        resp = self.api("PATCH", "/products/%s" % p.uuid_ref, raw=self.raw_lab, body={"price": 3.3})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_bloqueos_por_compras_con_el_campo(self):
        tx = self.product.execute_purchase_flow(self.farm_a, 5.0)["transaction"]
        self.assertTrue(self.product.has_purchases())
        data = self.api("GET", "/products/%s" % self.product.uuid_ref, raw=self.raw_lab).json()
        self.assertIn("initial_qty", data["locks"]["fields"])
        self.assertIn("stage", data["locks"]["fields"])
        self.assertTrue(data["locks"]["reason"])
        body = self.assertProblem(self.api("PATCH", "/products/%s" % self.product.uuid_ref,
                                           raw=self.raw_lab, body={"initial_qty": 5000}),
                                  422, "validation-error")
        self.assertEqual([e["field"] for e in body["errors"]], ["initial_qty"])
        # El precio, solo mientras la compra siga abierta.
        if tx.state != "done":
            self.assertIn("price", data["locks"]["fields"])
            body = self.assertProblem(self.api("PATCH", "/products/%s" % self.product.uuid_ref,
                                               raw=self.raw_lab, body={"price": 9.0}),
                                      422, "validation-error")
            self.assertEqual(body["errors"][0]["field"], "price")
        # Lo que no está bloqueado sí cambia.
        resp = self.api("PATCH", "/products/%s" % self.product.uuid_ref, raw=self.raw_lab,
                        body={"health_status": "Revisado"})
        self.assertEqual(resp.status_code, 200, resp.text)

    # ------------------------------------------------------------------
    # Fotos
    # ------------------------------------------------------------------
    def test_fotos_subir_portada_ordenar_bajar_y_quitar(self):
        p = self.draft()
        path = "/products/%s/photos" % p.uuid_ref
        resp = self.api("POST", path, raw=self.raw_lab, body=self.photo_body(name="a.png"))
        self.assertEqual(resp.status_code, 201, resp.text)
        a = resp.json()["photo"]
        self.assertEqual(a["mime"], "image/png")
        resp = self.api("POST", path, raw=self.raw_lab, body=self.photo_body(JPG_MIN, "b.jpg"))
        b = resp.json()["photo"]
        self.assertEqual([f["filename"] for f in resp.json()["data"]], ["a.png", "b.jpg"])
        resp = self.api("POST", path, raw=self.raw_lab, body=self.photo_body(name="c.png", cover=True))
        fotos = resp.json()["data"]
        self.assertEqual([f["filename"] for f in fotos], ["c.png", "a.png", "b.jpg"])
        self.assertEqual(resp.json()["meta"], {"count": 3, "max": 20})
        self.assertEqual(p.photo_attachment_ids[:1].name, "c.png", "portada de las tarjetas")
        # Ordenar: b primero; las que no vienen, al final.
        ids = {f["filename"]: f["id"] for f in fotos}
        resp = self.api("POST", path + ":reorder", raw=self.raw_lab, body={"order": [ids["b.jpg"]]})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([f["filename"] for f in resp.json()["data"]], ["b.jpg", "c.png", "a.png"])
        self.assertProblem(self.api("POST", path + ":reorder", raw=self.raw_lab,
                                    body={"order": ["no-es-mia"]}), 422, "validation-error")
        # Bajar el archivo (y la miniatura).
        fotos = resp.json()["data"]
        archivo = self.api("GET", "%s/%s" % (path, fotos[2]["id"]), raw=self.raw_lab)
        self.assertEqual(archivo.status_code, 200)
        self.assertEqual(archivo.headers["Content-Type"], "image/png")
        self.assertEqual(archivo.content, PNG_1PX)
        self.assertEqual(self.api("GET", "%s/%s?thumb=1" % (path, fotos[2]["id"]),
                                  raw=self.raw_lab).status_code, 200)
        # Quitar la portada: la siguiente pasa a serlo.
        resp = self.api("DELETE", "%s/%s" % (path, fotos[0]["id"]), raw=self.raw_lab)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([f["filename"] for f in resp.json()["data"]], ["c.png", "a.png"])
        self.assertProblem(self.api("DELETE", "%s/%s" % (path, fotos[0]["id"]), raw=self.raw_lab),
                           404, "not-found")
        # El detalle del lote trae las fotos en orden.
        data = self.api("GET", "/products/%s" % p.uuid_ref, raw=self.raw_lab).json()
        self.assertEqual([f["filename"] for f in data["photos"]], ["c.png", "a.png"])
        del a, b

    def test_fotos_limites(self):
        p = self.draft()
        path = "/products/%s/photos" % p.uuid_ref
        # No es una imagen (aunque diga que sí).
        body = self.assertProblem(self.api("POST", path, raw=self.raw_lab, body=self.photo_body(
            b"<html>no</html>", "x.png", mime="image/png")), 422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "data")
        # Más de 5 MB.
        grande = JPG_MIN + b"\x00" * (5 * 1024 * 1024)
        self.assertProblem(self.api("POST", path, raw=self.raw_lab, body=self.photo_body(grande, "g.jpg")),
                           413, "payload-too-large")
        # Ya tiene 20.
        Att = self.env["ir.attachment"].sudo()
        atts = Att.create([{"name": "f%s.png" % i, "raw": PNG_1PX, "mimetype": "image/png",
                            "res_model": "shrimp.product", "res_id": p.id} for i in range(20)])
        p.photo_attachment_ids = [(6, 0, atts.ids)]
        body = self.assertProblem(self.api("POST", path, raw=self.raw_lab, body=self.photo_body()),
                                  422, "validation-error")
        self.assertIn("20 fotos", body["detail"])
        # Un lote ajeno: 404.
        self.assertProblem(self.api("POST", path, raw=self.raw_a, body=self.photo_body()), 404)

    # ------------------------------------------------------------------
    # Certificados
    # ------------------------------------------------------------------
    def test_certificados_ligar_subir_y_quitar(self):
        p = self.draft()
        Cert = self.env["shrimp.certificate"].sudo()
        pcr = Cert.create({"name": "PCR gestión", "issuer": "Lab", "role": "all", "code": "PCR-GL",
                           "expires_required": True, "duration_value": 1})
        solo_cam = Cert.create({"name": "Solo camaronera", "issuer": "X", "role": "camaronera",
                                "code": "SC-GL", "expires_required": False})
        archivo = self.env["ir.attachment"].sudo().create({
            "name": "pcr.pdf", "raw": PDF_MIN, "mimetype": "application/pdf"})
        UserLine = self.env["shrimp.user.certificate.line"].sudo()
        vigente = UserLine.create({"partner_id": self.lab.id, "certificate_id": pcr.id,
                                   "certificate_number": "A-1", "expiry_date": "2099-01-01",
                                   "file_attachment_id": archivo.id, "status": "approved"})
        pendiente = UserLine.create({"partner_id": self.lab.id, "certificate_id": pcr.id,
                                     "certificate_number": "A-2", "expiry_date": "2099-01-01",
                                     "file_attachment_id": archivo.id, "status": "pending"})
        path = "/products/%s/certificates" % p.uuid_ref
        data = self.api("GET", path, raw=self.raw_lab).json()
        self.assertEqual([c["id"] for c in data["available"]], [vigente.uuid_ref])
        catalogo = [c["id"] for c in data["catalog"]]
        self.assertIn(pcr.uuid_ref, catalogo)
        self.assertNotIn(solo_cam.uuid_ref, catalogo, "solo los del perfil del lote")
        # Ligar uno vigente: hereda la aprobación y no se duplica.
        resp = self.api("POST", path, raw=self.raw_lab, body={"account_certificate": vigente.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        line = resp.json()["line"]
        self.assertEqual((line["status"], line["source"]), ("approved", "account"))
        self.assertEqual(resp.json()["available"], [])
        self.api("POST", path, raw=self.raw_lab, body={"account_certificate": vigente.uuid_ref})
        self.assertEqual(len(p.certificate_line_ids), 1)
        body = self.assertProblem(self.api("POST", path, raw=self.raw_lab, body={
            "account_certificate": pendiente.uuid_ref}), 422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "account_certificate")
        # Subir uno propio: pendiente de revisión.
        nuevo = {"certificate": pcr.uuid_ref, "number": "B-7", "issue_date": "2026-10-01",
                 "expiry_date": "2027-10-01", "filename": "pcr.pdf",
                 "data": base64.b64encode(PDF_MIN).decode()}
        resp = self.api("POST", path, raw=self.raw_lab, body=nuevo)
        self.assertEqual(resp.status_code, 201, resp.text)
        propio = resp.json()["line"]
        self.assertEqual((propio["status"], propio["source"], propio["mime"]),
                         ("pending", "product", "application/pdf"))
        # Errores en su campo.
        for cambio, campo in (({"expiry_date": None}, "expiry_date"),
                              ({"expiry_date": "2026-01-01"}, "expiry_date"),
                              ({"number": "B-7"}, "number"),
                              ({"number": "C", "data": base64.b64encode(b"<html>").decode()}, "data"),
                              ({"certificate": solo_cam.uuid_ref}, "certificate"),
                              ({"data": None}, "data")):
            body = self.assertProblem(self.api("POST", path, raw=self.raw_lab, body=dict(nuevo, **cambio)),
                                      422, "validation-error")
            self.assertEqual(body["errors"][0]["field"], campo, cambio)
        # Quitar: la línea sale del lote; el de la cuenta sigue.
        resp = self.api("DELETE", "%s/%s" % (path, line["id"]), raw=self.raw_lab)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([c["id"] for c in resp.json()["data"]], [propio["id"]])
        self.assertEqual([c["id"] for c in resp.json()["available"]], [vigente.uuid_ref])
        self.assertTrue(vigente.exists())

    # ------------------------------------------------------------------
    # Reactivar y eliminar
    # ------------------------------------------------------------------
    def test_reactivar_va_firmado_y_eliminar_un_borrador_no(self):
        raw, priv, dev = self.app_with_device()
        p = self.draft()
        resp = self.api("POST", "/products/%s:archive" % p.uuid_ref, raw=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["can_reactivate"])
        archivados = self.api("GET", "/products?state=archived", raw=raw).json()["data"]
        self.assertEqual([x["id"] for x in archivados], [p.uuid_ref])
        path = "/products/%s:unarchive" % p.uuid_ref
        self.assertProblem(self.api("POST", path, raw=raw), 403, "signature-required")
        self.assertFalse(p.active)
        resp = self.signed_post(path, raw, dev, priv, {})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual((resp.json()["active"], resp.json()["state"]), (True, "draft"))
        # Eliminar el borrador: sin firma; no se reactiva ni sale en archivados.
        resp = self.api("DELETE", "/products/%s" % p.uuid_ref, raw=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual((resp.json()["state"], resp.json()["can_reactivate"]), ("cancel", False))
        self.assertFalse(self.api("GET", "/products?state=archived", raw=raw).json()["data"])
        self.assertProblem(self.signed_post(path, raw, dev, priv, {}), 409, "invalid-state")

    def test_openapi_y_subusuario(self):
        paths = self.api("GET", "/openapi.json").json()["paths"]
        self.assertTrue(paths["/api/v1/products/{id}:update-terms"]["post"].get("x-signed"))
        self.assertTrue(paths["/api/v1/products/{id}:unarchive"]["post"].get("x-signed"))
        for ruta, metodo in (("/api/v1/products/{id}/photos", "post"),
                             ("/api/v1/products/{id}/photos:reorder", "post"),
                             ("/api/v1/products/{id}/photos/{photo}", "delete"),
                             ("/api/v1/products/{id}/certificates", "post"),
                             ("/api/v1/products/{id}/certificates/{line}", "delete")):
            self.assertFalse(paths[ruta][metodo].get("x-signed"), ruta)
        # Un subusuario sin «Vender» no toca fotos ni certificados.
        contacto = self.env["res.partner"].sudo().create({
            "name": "Sub lab gl", "email": "sub.lab.gl@prueba.test", "parent_id": self.lab.id,
            "type": "contact"})
        user = self.env["res.users"].sudo().create({
            "name": "Sub lab gl", "login": "sub.lab.gl", "partner_id": contacto.id,
            "password": PASSWORD, "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.env["shrimp.account.member"].sudo()._shrimp_ensure(
            user, self.lab, permissions={"laboratorio": {"view"}})
        _k, sub = self.env["shrimp.api.key"].api_create_key(user, "sub", SCOPE_CODES)
        p = self.draft()
        for method, path, body in (
                ("POST", "/products/%s/photos" % p.uuid_ref, self.photo_body()),
                ("POST", "/products/%s/certificates" % p.uuid_ref, {"account_certificate": "x"}),
                ("PATCH", "/products/%s" % p.uuid_ref, {"price": 1.0})):
            body = self.assertProblem(self.api(method, path, raw=sub, body=body), 403, "permission-denied")
            self.assertEqual(body["code"], "permission_denied")
        # Ver sí.
        self.assertEqual(self.api("GET", "/products/%s/photos" % p.uuid_ref, raw=sub).status_code, 200)
