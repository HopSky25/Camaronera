"""Publicar lotes desde la app móvil.

`POST /products/{id}:publish` va firmado con la clave de la app (crear y
editar el borrador, no), y la API aplica las reglas del formulario del
portal que el modelo no comprueba: la etapa que puede vender cada perfil,
presentación y talla solo en camarón, y el origen (instalación y piscina).
"""

import json
import time
import uuid

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase
from .test_api_dispositivos import new_keypair, sign

PASSWORD = "Clave-api-123"


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiPublicarApp(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.nauplio = ref("shrimp_marketplace.shrimp_stage_nauplio")
        cls.juvenil = ref("shrimp_marketplace.shrimp_stage_juvenil")
        cls.engorde = ref("shrimp_marketplace.shrimp_stage_engorde")
        cls.libra = ref("shrimp_marketplace.uom_libra")
        cls.grade = cls.env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)

    def setUp(self):
        super().setUp()
        failures = getattr(self.env.registry, "_login_failures", None)
        if failures is not None:
            failures.clear()

    # ------------------------------------------------------------------
    def app_with_device(self, login):
        resp = self.api("POST", "/auth/login", body={"login": login, "password": PASSWORD,
                                                     "device": "Pixel"})
        self.assertEqual(resp.status_code, 200, resp.text)
        raw = resp.json()["api_key"]
        priv, pub = new_keypair()
        resp = self.api("POST", "/me/devices", raw=raw, body={
            "name": "Pixel", "platform": "android", "public_key": pub, "alg": "ES256"})
        self.assertEqual(resp.status_code, 201, resp.text)
        return raw, priv, resp.json()["id"]

    def signed_post(self, path, raw, device_id, priv, body, idem=None):
        data = json.dumps(body).encode()
        idem = idem or str(uuid.uuid4())
        ts = int(time.time())
        h = {"Content-Type": "application/json", "Authorization": "Bearer " + raw,
             "Idempotency-Key": idem, "X-Device-Id": device_id,
             "X-Signature-Timestamp": str(ts),
             "X-Signature": sign(priv, "POST", "/api/v1" + path, ts, idem, data)}
        return self.url_open("/api/v1" + path, data=data, headers=h, method="POST",
                             allow_redirects=False), h, data

    def prod(self, uuid_ref):
        return self.env["shrimp.product"].sudo().with_context(active_test=False).search(
            [("uuid_ref", "=", uuid_ref)])

    # ------------------------------------------------------------------
    def test_publicar_con_la_app_va_firmado(self):
        raw, priv, dev_id = self.app_with_device("lab.api")
        # El borrador se crea sin firma (como lo manda la app).
        resp = self.api("POST", "/products", raw=raw, body={
            "name": "PL12 desde la app", "price": 2.8, "initial_qty": 1500,
            "stage": self.stage_pl12.uuid_ref, "species": self.species.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        pid = resp.json()["id"]
        self.assertEqual(resp.json()["state"], "draft")
        self.assertEqual(resp.json()["uom"]["id"], self.uom_millar.uuid_ref)
        # Editar el borrador tampoco se firma.
        resp = self.api("PATCH", "/products/%s" % pid, raw=raw, body={"price": 2.9},
                        headers={"Idempotency-Key": str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 200, resp.text)

        # Publicar sin firma: 403 y sigue en borrador.
        self.assertProblem(self.api("POST", "/products/%s:publish" % pid, raw=raw), 403,
                           "signature-required")
        self.assertEqual(self.prod(pid).state, "draft")

        # Firmado: se publica y queda en la auditoría.
        path = "/products/%s:publish" % pid
        resp, h, data = self.signed_post(path, raw, dev_id, priv, {})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "published")
        self.assertFalse(resp.json()["requires_verification"], "larva: verificación opcional")
        self.assertEqual(self.prod(pid).state, "published")
        audit = self.env["shrimp.api.signed.operation"].sudo().search(
            [("path", "=", "/api/v1" + path)])
        self.assertEqual(len(audit), 1)

        # Reintento legítimo (misma Idempotency-Key, misma firma): la misma respuesta.
        h2 = dict(h)
        again = self.url_open("/api/v1" + path, data=data, headers=h2, method="POST",
                              allow_redirects=False)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(again.json()["state"], "published")

    def test_clave_de_app_sin_dispositivo_no_publica(self):
        resp = self.api("POST", "/auth/login", body={"login": "lab.api", "password": PASSWORD,
                                                     "device": "Pixel"})
        raw = resp.json()["api_key"]
        product = self.env["shrimp.product"].create({
            "name": "PL sin firma", "seller_partner_id": self.lab.id, "seller_role": "laboratorio",
            "initial_qty": 100.0, "price": 2.0, "uom_id": self.uom_millar.id,
            "stage_id": self.stage_pl12.id, "state": "draft"})
        self.assertProblem(self.api("POST", "/products/%s:publish" % product.uuid_ref, raw=raw),
                           403, "device-not-registered")
        self.assertEqual(product.state, "draft")
        # Una clave de integración (ERP) no firma.
        resp = self.api("POST", "/products/%s:publish" % product.uuid_ref, raw=self.raw_lab)
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_openapi_marca_publicar_como_firmada(self):
        paths = self.api("GET", "/openapi.json").json()["paths"]
        self.assertTrue(paths["/api/v1/products/{id}:publish"]["post"].get("x-signed"))
        self.assertFalse(paths["/api/v1/products"]["post"].get("x-signed"))
        self.assertFalse(paths["/api/v1/products/{id}"]["patch"].get("x-signed"))
        self.assertFalse(paths["/api/v1/products/{id}:archive"]["post"].get("x-signed"))

    def test_catalogo_de_estadios_trae_el_tipo(self):
        data = self.api("GET", "/public/catalogs/stages").json()["data"]
        tipos = {d["code"]: d["type"] for d in data}
        self.assertEqual(tipos["NAUPLIO"], "nauplio")
        self.assertEqual(tipos["PL12"], "larva")
        self.assertEqual(tipos["ZOEA"], "larva")
        self.assertEqual(tipos["JUVENIL"], "camaron")
        self.assertEqual(tipos["ENGORDE"], "camaron")

    # ------------------------------------------------------------------
    # Reglas del formulario del portal
    # ------------------------------------------------------------------
    def test_etapa_segun_el_perfil(self):
        # El laboratorio no publica camarón.
        body = self.assertProblem(self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "x", "price": 1, "initial_qty": 10, "stage": self.engorde.uuid_ref}),
            422, "validation-error")
        self.assertIn("Etapas permitidas", body["detail"])
        self.assertEqual(body["errors"][0]["field"], "stage")
        # La camaronera no publica larva.
        self.assertProblem(self.api("POST", "/products", raw=self.raw_a, body={
            "name": "x", "price": 1, "initial_qty": 10, "stage": self.stage_pl12.uuid_ref}),
            422, "validation-error")
        # Nauplio del laboratorio: en millares.
        resp = self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "Nauplio", "price": 0.4, "initial_qty": 5000, "stage": self.nauplio.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        pid = resp.json()["id"]
        self.assertEqual(resp.json()["uom"]["id"], self.uom_millar.uuid_ref)
        # Cambiar a una etapa que el perfil no vende: 422; el precio solo, sí.
        self.assertProblem(self.api("PATCH", "/products/%s" % pid, raw=self.raw_lab,
                                    body={"stage": self.juvenil.uuid_ref}), 422, "validation-error")
        resp = self.api("PATCH", "/products/%s" % pid, raw=self.raw_lab, body={"price": 0.5})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_presentacion_y_talla_solo_en_camaron(self):
        body = self.assertProblem(self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "x", "price": 1, "initial_qty": 10, "stage": self.stage_pl12.uuid_ref,
            "presentation": "entero"}), 422, "validation-error")
        self.assertIn("solo aplican al camarón", body["detail"])
        # Engorde sin presentación ni talla: no se crea (modelo, listas de precios).
        self.assertProblem(self.api("POST", "/products", raw=self.raw_a, body={
            "name": "Engorde", "price": 2.5, "initial_qty": 1000,
            "stage": self.engorde.uuid_ref}), 422)
        # Con presentación y talla, en libras, y con verificación obligatoria.
        resp = self.api("POST", "/products", raw=self.raw_a, body={
            "name": "Engorde 30/40", "price": 2.5, "initial_qty": 1000,
            "stage": self.engorde.uuid_ref, "presentation": "entero",
            "size_grade": self.grade.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["uom"]["id"], self.libra.uuid_ref)
        pid = resp.json()["id"]
        resp = self.api("POST", "/products/%s:publish" % pid, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["requires_verification"])
        # Juvenil: presentación y talla opcionales.
        resp = self.api("POST", "/products", raw=self.raw_a, body={
            "name": "Juvenil", "price": 3, "initial_qty": 100, "stage": self.juvenil.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_sin_etapa_no_se_publica(self):
        # La API anterior creaba sin etapa (se conserva para los ERP), pero
        # ese borrador no sale al marketplace hasta tenerla.
        resp = self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "Sin etapa", "price": 1, "initial_qty": 10})
        self.assertEqual(resp.status_code, 201, resp.text)
        pid = resp.json()["id"]
        body = self.assertProblem(self.api("POST", "/products/%s:publish" % pid, raw=self.raw_lab),
                                  422, "validation-error")
        self.assertIn("etapa", body["detail"])
        self.assertEqual(self.prod(pid).state, "draft")

    def test_origen_instalacion_y_piscina(self):
        Fac = self.env["shrimp.partner.facility"].sudo()
        bodega = Fac.create({"name": "Bodega", "partner_id": self.farm_a.id,
                             "facility_type": "warehouse"})
        finca = Fac.create({"name": "Finca", "partner_id": self.farm_a.id, "facility_type": "farm"})
        pond = self.env["shrimp.partner.pond"].sudo().create({
            "name": "P1", "partner_id": self.farm_a.id, "facility_id": finca.id})
        base = {"name": "Juvenil", "price": 3, "initial_qty": 100, "stage": self.juvenil.uuid_ref}
        body = self.assertProblem(self.api("POST", "/products", raw=self.raw_a, body=dict(
            base, origin_facility=bodega.uuid_ref)), 422, "validation-error")
        self.assertIn("bodega", body["detail"])
        self.assertProblem(self.api("POST", "/products", raw=self.raw_a, body=dict(
            base, origin_pond=pond.uuid_ref)), 422, "validation-error")
        resp = self.api("POST", "/products", raw=self.raw_a, body=dict(
            base, origin_facility=finca.uuid_ref, origin_pond=pond.uuid_ref))
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["origin_pond"]["id"], pond.uuid_ref)

    def test_subusuario_sin_permiso_de_vender(self):
        contacto = self.env["res.partner"].sudo().create({
            "name": "Sub lab", "email": "sub.lab@prueba.test", "parent_id": self.lab.id,
            "type": "contact"})
        user = self.env["res.users"].sudo().create({
            "name": "Sub lab", "login": "sub.lab.api", "partner_id": contacto.id,
            "password": PASSWORD, "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.env["shrimp.account.member"].sudo()._shrimp_ensure(
            user, self.lab, permissions={"laboratorio": {"view"}})
        _k, raw = self.env["shrimp.api.key"].api_create_key(user, "sub", SCOPE_CODES)
        product = self.env["shrimp.product"].create({
            "name": "PL del laboratorio", "seller_partner_id": self.lab.id,
            "seller_role": "laboratorio", "initial_qty": 100.0, "price": 2.0,
            "uom_id": self.uom_millar.id, "stage_id": self.stage_pl12.id, "state": "draft"})
        for method, path in (("POST", "/products"),
                             ("POST", "/products/%s:publish" % product.uuid_ref),
                             ("POST", "/products/%s:archive" % product.uuid_ref)):
            body = self.assertProblem(self.api(method, path, raw=raw, body={
                "name": "x", "price": 1, "initial_qty": 1} if path == "/products" else {}),
                403, "permission-denied")
            self.assertEqual(body["code"], "permission_denied")
        self.assertEqual(product.state, "draft")
        self.assertTrue(product.active)
