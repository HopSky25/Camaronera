"""Mejoras de la app móvil (docs/app/CONTRATO-MEJORAS-APP.md, C1–C6).

C1 versión mínima (426), C2 errores de la app, C3 bandeja de avisos, C4
fotos de campo firmadas, C5 compra directa firmada y C6 assetlinks.json.
"""

import base64
import io
import json
import time
import uuid
from datetime import timedelta

from PIL import Image

from odoo import fields
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase, walk_keys
from .test_api_dispositivos import new_keypair, sign

PASSWORD = "Clave-api-123"


def jpeg_bytes(color=(200, 80, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (12, 8), color).save(buf, format="JPEG")
    return buf.getvalue()


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiMejorasApp(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        P = env["res.partner"]
        portal = env.ref("base.group_portal")
        cls.verifier = P.create({
            "name": "Verificadora Mejoras", "is_company": True, "email": "ver.mej@prueba.test",
            "vat_or_id": "0999900011001", "shrimp_user_type": "verificador",
            "ver_bank_account_number": "2200112299", "ver_bank_holder_id": "0912345670",
        })
        cls.tech = P.create({"name": "Técnico Mejoras", "email": "tec.mej@prueba.test",
                             "parent_id": cls.verifier.id, "shrimp_is_field_tech": True})
        cls.tech2 = P.create({"name": "Otro Técnico Mejoras", "email": "tec2.mej@prueba.test",
                              "parent_id": cls.verifier.id, "shrimp_is_field_tech": True})
        cls.copacker = P.create({
            "name": "Maquila Mejoras", "is_company": True, "email": "maq.mej@prueba.test",
            "vat_or_id": "0999900012001", "shrimp_user_type": "maquilador",
            "pack_razon_social": "Maquila Mejoras S.A.", "pack_ubicacion": "Durán",
            "pack_codigo_establecimiento": "AS-998", "pack_en_directorio": True,
        })

        def user(partner, login):
            return env["res.users"].create({
                "name": partner.name, "login": login, "partner_id": partner.id,
                "password": PASSWORD, "group_ids": [(6, 0, [portal.id])]})

        Key = env["shrimp.api.key"]
        cls.u_verifier = user(cls.verifier, "ver.mej")
        cls.u_tech = user(cls.tech, "tec.mej")
        cls.u_tech2 = user(cls.tech2, "tec2.mej")
        cls.u_copacker = user(cls.copacker, "maq.mej")
        cls.key_verifier, cls.raw_verifier = Key.api_create_key(cls.u_verifier, "ver", SCOPE_CODES)
        cls.key_tech2, cls.raw_tech2 = Key.api_create_key(cls.u_tech2, "tec2", SCOPE_CODES)
        cls.key_copacker, cls.raw_copacker = Key.api_create_key(cls.u_copacker, "maq", SCOPE_CODES)
        cls.u_sup = env["res.users"].create({
            "name": "Supervisora Mejoras", "login": "sup.mej", "password": PASSWORD,
            "group_ids": [(6, 0, [env.ref("base.group_user").id,
                                  env.ref("shrimp_api.group_shrimp_app_supervisor").id])]})
        cls.grade = env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)
        cls.engorde = env.ref("shrimp_marketplace.shrimp_stage_engorde")
        cls.libra = env.ref("shrimp_marketplace.uom_libra")

    def setUp(self):
        super().setUp()
        failures = getattr(self.env.registry, "_login_failures", None)
        if failures is not None:
            failures.clear()

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------
    def set_param(self, key, value):
        self.env["ir.config_parameter"].sudo().set_param(key, value)

    def login(self, login, version=None):
        headers = {"X-App-Version": version, "X-App-Platform": "android"} if version else None
        return self.api("POST", "/auth/login", headers=headers,
                        body={"login": login, "password": PASSWORD, "device": "Pixel"})

    def app_with_device(self, login):
        resp = self.login(login)
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
                             allow_redirects=False)

    def verified_purchase(self):
        """Compra de camarón adulto con verificación (como en test_api_flows)."""
        product = self.env["shrimp.product"].create({
            "name": "Engorde Mejoras", "seller_partner_id": self.farm_a.id,
            "seller_role": "camaronera", "initial_qty": 10000, "price": 2.2,
            "uom_id": self.libra.id, "stage_id": self.engorde.id, "presentation": "entero",
            "size_grade_id": self.grade.id, "state": "published"})
        tx = self.env["shrimp.transaction"].create({
            "transaction_type": "camaronera_to_buyer", "product_id": product.id,
            "seller_partner_id": self.farm_a.id, "buyer_partner_id": self.packer.id,
            "state": "pending_verification", "needs_verification": True, "transaction_qty": 10000,
            "price_unit": 2.2, "amount_total": 22000, "desired_qty": 10000,
            "desired_date": fields.Date.today()})
        verification = self.env["shrimp.verification"].create({
            "transaction_id": tx.id, "verifier_partner_id": self.verifier.id, "fee": 300})
        return product, tx, verification

    def notifs(self, partner, **domain):
        dom = [("partner_id", "=", partner.id)] + [(k, "=", v) for k, v in domain.items()]
        return self.env["shrimp.app.notification"].sudo().search(dom)

    # ------------------------------------------------------------------
    # C1. Versión mínima
    # ------------------------------------------------------------------
    def test_c1_sin_minimo_no_exige(self):
        resp = self.api("GET", "/public/app-version?platform=android")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"min_version": None, "latest_version": None,
                                       "download_url": None, "message": None})
        # Sin mínimo: login y clave de app sin X-App-Version funcionan.
        resp = self.login("a.api")
        self.assertEqual(resp.status_code, 200, resp.text)
        raw = resp.json()["api_key"]
        self.assertEqual(self.api("GET", "/me", raw=raw).status_code, 200)
        self.assertProblem(self.api("GET", "/public/app-version?platform=web"), 400,
                           "invalid-parameter")

    def test_c1_con_minimo(self):
        # Clave de app emitida antes de fijar el mínimo.
        raw = self.login("a.api").json()["api_key"]
        self.set_param("shrimp_api.app_min_version", "1.2.0")
        self.set_param("shrimp_api.app_latest_version", "1.3.0")
        self.set_param("shrimp_api.app_download_url", "https://camaronmarket.com/app")
        self.set_param("shrimp_api.app_update_message", "Hay una versión nueva obligatoria.")
        info = self.api("GET", "/public/app-version?platform=android").json()
        self.assertEqual(info, {"min_version": "1.2.0", "latest_version": "1.3.0",
                                "download_url": "https://camaronmarket.com/app",
                                "message": "Hay una versión nueva obligatoria."})
        # Versión vieja o sin cabecera -> 426 con min_version y download_url.
        for headers in ({"X-App-Version": "1.1.9"}, {}, {"X-App-Version": "basura"}):
            body = self.assertProblem(self.api("GET", "/me", raw=raw, headers=headers), 426,
                                      "app-update-required")
            self.assertEqual(body["min_version"], "1.2.0")
            self.assertEqual(body["download_url"], "https://camaronmarket.com/app")
            self.assertEqual(body["detail"], "Hay una versión nueva obligatoria.")
        # Igual o mayor (también con número de compilación de pubspec) -> pasa.
        for version in ("1.2.0", "1.2.0+15", "1.10.0", "2.0"):
            resp = self.api("GET", "/me", raw=raw, headers={"X-App-Version": version})
            self.assertEqual(resp.status_code, 200, (version, resp.text))
        # La clave manual de integraciones no se ve afectada.
        self.assertEqual(self.api("GET", "/me", raw=self.raw_a).status_code, 200)
        # Las rutas públicas tampoco.
        self.assertEqual(self.api("GET", "/public/catalogs/species").status_code, 200)
        # El inicio de sesión sí.
        body = self.assertProblem(self.login("b.api"), 426, "app-update-required")
        self.assertEqual(body["min_version"], "1.2.0")
        self.assertProblem(self.login("b.api", version="1.0.0"), 426, "app-update-required")
        resp = self.login("b.api", version="1.2.0")
        self.assertEqual(resp.status_code, 200, resp.text)
        # Cerrar sesión sigue permitido: la app vieja tiene que poder revocar su clave.
        resp = self.api("POST", "/auth/logout", raw=raw, headers={"X-App-Version": "1.0.0"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["revoked"])

    def test_c1_ajustes_validan_version(self):
        Settings = self.env["res.config.settings"]
        with self.assertRaises(Exception):
            Settings.create({"shrimp_app_min_version": "uno punto dos"})
        with self.assertRaises(Exception):
            Settings.create({"shrimp_app_min_version": "2.0.0", "shrimp_app_latest_version": "1.0.0"})
        Settings.create({"shrimp_app_min_version": "1.0.0", "shrimp_app_latest_version": "1.1.0",
                         "shrimp_android_cert_sha256": ""}).execute()
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param(
            "shrimp_api.app_min_version"), "1.0.0")

    # ------------------------------------------------------------------
    # C2. Errores de la app
    # ------------------------------------------------------------------
    def error_body(self, **extra):
        body = {"app_version": "1.0.0", "platform": "android", "os_version": "Android 14",
                "device_model": "SM-A546E", "screen": "verificaciones/detalle",
                "error": "Null check operator used on a null value",
                "stack": "#0      _VerifState.build (package:verificador_campo/ver.dart:120:7)\n"
                         "#1      StatelessElement.build (package:flutter/src/widgets/framework.dart:5)",
                "occurred_at": "2026-10-15T14:00:00Z"}
        body.update(extra)
        return body

    def test_c2_acepta_y_agrupa_por_huella(self):
        Error = self.env["shrimp.app.error"].sudo()
        # La base puede traer errores reales de la app con la misma huella;
        # se parte de cero (el test se deshace al terminar).
        Error.search([]).unlink()
        resp = self.api("POST", "/public/app-errors", body=self.error_body())
        self.assertEqual(resp.status_code, 204, resp.text)
        self.assertEqual(resp.text, "")
        err = Error.search([("error", "=", "Null check operator used on a null value")])
        self.assertEqual(len(err), 1)
        self.assertEqual(err.count, 1)
        self.assertEqual(err.stack_line, "_VerifState.build (package:verificador_campo/ver.dart:120:7)")
        # Mismo error y misma primera línea útil (otra versión, otra pila de
        # Flutter debajo) -> mismo grupo.
        resp = self.api("POST", "/public/app-errors", body=self.error_body(
            app_version="1.0.1", stack="#0      _VerifState.build (package:verificador_campo/ver.dart:120:7)"))
        self.assertEqual(resp.status_code, 204, resp.text)
        err.invalidate_recordset()
        self.assertEqual(err.count, 2)
        self.assertEqual(err.app_versions, "1.0.0,1.0.1")
        self.assertEqual(err.last_app_version, "1.0.1")
        # Otro error -> otro grupo.
        self.api("POST", "/public/app-errors", body=self.error_body(error="FormatException"))
        self.assertEqual(Error.search_count([("error", "=", "FormatException")]), 1)
        # El administrador ve el menú; la vista existe.
        self.assertTrue(self.env.ref("shrimp_api.menu_app_errors"))

    def test_c2_campos_extra_422(self):
        body = self.assertProblem(self.api("POST", "/public/app-errors",
                                           body=self.error_body(user="juan@finca.ec")),
                                  422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "user")
        self.assertProblem(self.api("POST", "/public/app-errors", body={"screen": "x"}), 422,
                           "validation-error")
        self.assertProblem(self.api("POST", "/public/app-errors",
                                    body=self.error_body(error="x" * 4001)), 422, "validation-error")

    def test_c2_limite_por_ip(self):
        self.env.cr.execute("""SELECT coalesce(sum(hits), 0) FROM shrimp_api_rate_bucket
                                WHERE subject LIKE 'apperr:%%' AND kind = 'h'
                                  AND bucket = date_trunc('hour', now() at time zone 'utc')""")
        used = self.env.cr.fetchone()[0]
        self.set_param("shrimp_api.app_errors_per_hour", str(used + 2))
        for _i in range(2):
            self.assertEqual(self.api("POST", "/public/app-errors", body=self.error_body()).status_code, 204)
        resp = self.api("POST", "/public/app-errors", body=self.error_body())
        self.assertProblem(resp, 429, "rate-limited")
        self.assertTrue(resp.headers.get("Retry-After"))

    def test_c2_cron_borra_viejos(self):
        Error = self.env["shrimp.app.error"].sudo()
        viejo = Error._record({"error": "viejo", "stack": ""})
        nuevo = Error._record({"error": "nuevo", "stack": ""})
        viejo.last_seen = fields.Datetime.subtract(fields.Datetime.now(), days=91)
        Error._cron_cleanup()
        self.assertFalse(viejo.exists())
        self.assertTrue(nuevo.exists())

    # ------------------------------------------------------------------
    # C3. Bandeja de avisos
    # ------------------------------------------------------------------
    def test_c3_evento_real_genera_aviso_al_destinatario(self):
        _product, tx, v = self.verified_purchase()
        # Al nacer la verificación, la verificadora recibe «Nueva verificación»
        # y el vendedor «Nueva compra».
        self.assertEqual(self.notifs(self.verifier, kind="verification_assigned").target_uuid,
                         v.uuid_ref)
        self.assertEqual(self.notifs(self.farm_a, kind="purchase_received").target_uuid, tx.uuid_ref)
        # La verificadora asigna el técnico por la API -> aviso al técnico.
        resp = self.api("POST", "/verifications/%s:assign" % v.uuid_ref, raw=self.raw_verifier,
                        body={"technician": self.tech.uuid_ref})
        self.assertEqual(resp.status_code, 200, resp.text)
        tech_notif = self.notifs(self.tech, kind="verification_assigned")
        self.assertEqual(len(tech_notif), 1)
        self.assertEqual(tech_notif.target_type, "verification")
        self.assertEqual(tech_notif.link, "/verifier/verifications/%s" % v.uuid_ref)
        self.assertEqual(tech_notif.role, "verificador")
        # Nadie más recibe ese aviso.
        for other in (self.tech2, self.farm_b, self.packer, self.lab):
            self.assertFalse(self.notifs(other, kind="verification_assigned"), other.name)
        # Lo ve el técnico por la API; la otra camaronera no ve nada de esto.
        _key, raw_tech = self.env["shrimp.api.key"].api_create_key(self.u_tech, "tec", SCOPE_CODES)
        data = self.api("GET", "/me/notifications", raw=raw_tech).json()
        self.assertEqual([n["kind"] for n in data["data"]], ["verification_assigned"])
        item = data["data"][0]
        self.assertEqual(set(item), {"id", "kind", "title", "body", "target", "link", "created_at",
                                     "read"})
        self.assertEqual(item["target"], {"type": "verification", "id": v.uuid_ref})
        self.assertIs(item["read"], False)
        ajenos = self.api("GET", "/me/notifications", raw=self.raw_b).json()["data"]
        self.assertNotIn(v.uuid_ref, [n["target"]["id"] for n in ajenos])

    def test_c3_lista_unread_read_y_read_all(self):
        Notif = self.env["shrimp.app.notification"].sudo()
        made = Notif.create([{"partner_id": self.farm_b.id, "kind": "purchase_confirmed",
                              "title": "Aviso %s" % i, "body": "Texto", "target_type": "transaction",
                              "target_uuid": str(uuid.uuid4()), "link": "/marketplace/purchases/x"}
                             for i in range(3)])
        resp = self.api("GET", "/me/notifications?limit=2", raw=self.raw_b)
        self.assertEqual(resp.status_code, 200, resp.text)
        page = resp.json()
        self.assertEqual(len(page["data"]), 2)
        self.assertTrue(page["meta"]["has_more"])
        self.assertEqual(page["meta"]["unread_count"], 3)
        # Del más nuevo al más viejo; el cursor trae el resto.
        self.assertEqual(page["data"][0]["id"], made[2].uuid_ref)
        rest = self.api("GET", "/me/notifications?limit=2&cursor=%s" % page["meta"]["next_cursor"],
                        raw=self.raw_b).json()
        self.assertEqual([n["id"] for n in rest["data"]], [made[0].uuid_ref])
        self.assertProblem(self.api("GET", "/me/notifications?cursor=zzz", raw=self.raw_b), 400,
                           "invalid-cursor")
        # Marcar uno.
        resp = self.api("POST", "/me/notifications/%s:read" % made[1].uuid_ref, raw=self.raw_b)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIs(resp.json()["read"], True)
        unread = self.api("GET", "/me/notifications?unread=true", raw=self.raw_b).json()
        self.assertEqual({n["id"] for n in unread["data"]}, {made[0].uuid_ref, made[2].uuid_ref})
        self.assertEqual(unread["meta"]["unread_count"], 2)
        # Uno ajeno no se puede marcar (404).
        self.assertProblem(self.api("POST", "/me/notifications/%s:read" % made[0].uuid_ref,
                                    raw=self.raw_a), 404, "not-found")
        # Exige Idempotency-Key, como todo POST.
        self.assertProblem(self.api("POST", "/me/notifications:read-all", raw=self.raw_b, idem=False),
                           400, "idempotency-key-required")
        resp = self.api("POST", "/me/notifications:read-all", raw=self.raw_b)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["updated"], 2)
        final = self.api("GET", "/me/notifications?unread=true", raw=self.raw_b).json()
        self.assertEqual(final["data"], [])
        self.assertEqual(final["meta"]["unread_count"], 0)

    def test_c3_supervisor_y_act_as(self):
        Notif = self.env["shrimp.app.notification"].sudo()
        notif = Notif.create({"partner_id": self.farm_a.id, "kind": "purchase_confirmed",
                              "title": "Compra confirmada", "target_type": "transaction",
                              "target_uuid": str(uuid.uuid4())})
        Key = self.env["shrimp.api.key"].sudo()
        sup_key, raw_sup = Key.api_create_supervisor_key(self.u_sup, "Supervisor (app) – test")
        _act, raw_act = sup_key.api_create_act_as_key(self.u_a)
        # La clave de supervisor lista vacío.
        resp = self.api("GET", "/me/notifications", raw=raw_sup)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"], [])
        # La de «actuar como» ve los del socio, pero no puede marcarlos.
        resp = self.api("GET", "/me/notifications", raw=raw_act)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn(notif.uuid_ref, [n["id"] for n in resp.json()["data"]])
        self.assertProblem(self.api("POST", "/me/notifications/%s:read" % notif.uuid_ref, raw=raw_act),
                           403, "insufficient-scope")
        self.assertProblem(self.api("POST", "/me/notifications:read-all", raw=raw_act), 403,
                           "insufficient-scope")
        notif.invalidate_recordset()
        self.assertFalse(notif.is_read)

    def test_c3_x_shrimp_role_filtra(self):
        Notif = self.env["shrimp.app.notification"].sudo()
        de_camaronera = Notif.create({"partner_id": self.farm_a.id, "role": "camaronera",
                                      "kind": "purchase_received", "title": "Camaronera"})
        sin_perfil = Notif.create({"partner_id": self.farm_a.id, "kind": "purchase_received",
                                   "title": "Sin perfil"})
        de_otro = Notif.create({"partner_id": self.farm_a.id, "role": "empacadora",
                                "kind": "purchase_confirmed", "title": "Empacadora"})
        todos = self.api("GET", "/me/notifications", raw=self.raw_a).json()["data"]
        self.assertTrue({de_camaronera.uuid_ref, sin_perfil.uuid_ref, de_otro.uuid_ref}
                        <= {n["id"] for n in todos})
        filtrados = self.api("GET", "/me/notifications", raw=self.raw_a,
                             headers={"X-Shrimp-Role": "camaronera"}).json()
        ids = {n["id"] for n in filtrados["data"]}
        self.assertIn(de_camaronera.uuid_ref, ids)
        self.assertIn(sin_perfil.uuid_ref, ids)
        self.assertNotIn(de_otro.uuid_ref, ids)
        # Un perfil que la cuenta no tiene: 403, como en el resto de la API.
        self.assertProblem(self.api("GET", "/me/notifications", raw=self.raw_a,
                                    headers={"X-Shrimp-Role": "empacadora"}), 403)

    def test_c3_cron_borra_leidos_viejos(self):
        Notif = self.env["shrimp.app.notification"].sudo()
        leido = Notif.create({"partner_id": self.farm_a.id, "kind": "x", "title": "viejo leído",
                              "is_read": True})
        sin_leer = Notif.create({"partner_id": self.farm_a.id, "kind": "x", "title": "viejo"})
        reciente = Notif.create({"partner_id": self.farm_a.id, "kind": "x", "title": "nuevo",
                                 "is_read": True})
        viejo = fields.Datetime.subtract(fields.Datetime.now(), days=61)
        self.env.cr.execute("UPDATE shrimp_app_notification SET create_date = %s WHERE id IN %s",
                            (viejo, (leido.id, sin_leer.id)))
        Notif.invalidate_model()
        Notif._cron_cleanup()
        self.assertFalse(leido.exists())
        self.assertTrue(sin_leer.exists())
        self.assertTrue(reciente.exists())

    def test_c3_copack_y_plazo(self):
        # Solicitud de empaque dirigida -> aviso al maquilador; al aceptar su
        # oferta -> «Oferta de empaque aceptada».
        today = fields.Date.today()
        resp = self.api("POST", "/copack/requests", raw=self.raw_a, body={
            "quantity_lb": 5000, "presentation": "entero", "needed_from": str(today),
            "needed_to": str(today), "copacker": self.copacker.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        req_id = resp.json()["id"]
        n = self.notifs(self.copacker, kind="copack_request_published")
        self.assertEqual(n.target_uuid, req_id)
        self.assertEqual(n.link, "/copacker/requests/%s" % req_id)
        self.assertFalse(self.notifs(self.farm_b, kind="copack_request_published"))
        resp = self.api("POST", "/copack/requests/%s/offers" % req_id, raw=self.raw_copacker, body={
            "rate_per_lb": 0.2, "capacity_lb": 5000, "available_from": str(today),
            "available_to": str(today)})
        self.assertEqual(resp.status_code, 201, resp.text)
        resp = self.api("POST", "/copack/offers/%s:accept" % resp.json()["id"], raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        n = self.notifs(self.copacker, kind="copack_offer_accepted")
        self.assertEqual(n.target_uuid, resp.json()["id"])
        self.assertEqual(n.link, "/copacker/orders/%s" % resp.json()["id"])
        # Plazo de aceptación por vencer: solo a la parte que no respondió.
        _product, _tx, v = self.verified_purchase()
        v.sudo().write({"state": "approved", "verified_date": fields.Datetime.now()})
        v.sudo()._abrir_ronda_aceptacion()
        self.assertEqual(len(self.notifs(self.packer, kind="report_ready")), 1)
        v.acceptance_ids.filtered(lambda a: a.role == "seller").sudo().action_accept(
            actor=self.farm_a)
        v.sudo().acceptance_deadline = fields.Datetime.now() + timedelta(hours=3)
        self.assertEqual(self.env["shrimp.verification"]._cron_api_acceptance_due(), 1)
        self.assertEqual(len(self.notifs(self.packer, kind="acceptance_due")), 1)
        self.assertFalse(self.notifs(self.farm_a, kind="acceptance_due"))
        # Una vez por plazo.
        self.assertEqual(self.env["shrimp.verification"]._cron_api_acceptance_due(), 0)

    # ------------------------------------------------------------------
    # C4. Fotos de campo
    # ------------------------------------------------------------------
    def tech_in_field(self):
        _product, _tx, v = self.verified_purchase()
        v.sudo().action_assign_technician(self.tech)
        raw, priv, dev = self.app_with_device("tec.mej")
        return v, raw, priv, dev

    def photo_body(self, content, **extra):
        body = {"filename": "pesaje.jpg", "mime": "image/jpeg",
                "data": base64.b64encode(content).decode(), "taken_at": "2026-10-15T13:00:00Z",
                "latitude": -2.19, "longitude": -79.88, "note": "Pesaje en planta"}
        body.update(extra)
        return body

    def test_c4_foto_firmada_queda_en_el_modelo_del_portal(self):
        v, raw, priv, dev = self.tech_in_field()
        path = "/verifications/%s/photos" % v.uuid_ref
        content = jpeg_bytes()
        # Asignada pero sin empezar: todavía no.
        self.assertProblem(self.signed_post(path, raw, dev, priv, self.photo_body(content)), 409,
                           "invalid-state")
        v.sudo().action_start_field()
        resp = self.signed_post(path, raw, dev, priv, self.photo_body(content))
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual(set(data), {"id", "filename", "taken_at", "size"})
        self.assertEqual(data["filename"], "pesaje.jpg")
        self.assertEqual(data["taken_at"], "2026-10-15T13:00:00Z")
        self.assertEqual(data["size"], len(content))
        v.invalidate_recordset()
        att = v.photo_ids
        self.assertEqual(len(att), 1)
        # Los mismos campos que llena el portal.
        self.assertEqual(att.res_model, "shrimp.verification")
        self.assertEqual(att.res_id, v.id)
        self.assertEqual(att.mimetype, "image/jpeg")
        self.assertFalse(att.public)
        self.assertEqual(att.access_token, data["id"])
        self.assertEqual(base64.b64decode(att.datas), content)
        self.assertEqual(att.shrimp_note, "Pesaje en planta")
        # GET: metadatos sin binario ni GPS; también lo ven las partes.
        for key in (raw, self.raw_packer):
            listed = self.api("GET", path, raw=key, headers={"X-App-Version": "1.0.0"})
            self.assertEqual(listed.status_code, 200, listed.text)
            items = listed.json()["data"]
            self.assertEqual([p["id"] for p in items], [data["id"]])
            self.assertFalse({"latitude", "longitude", "data", "datas"} & walk_keys(items))
        # PNG real también vale; el portal la sirve igual.
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, format="PNG")
        resp = self.signed_post(path, raw, dev, priv, self.photo_body(
            buf.getvalue(), filename="cola.png", mime="image/png"))
        self.assertEqual(resp.status_code, 201, resp.text)
        v.invalidate_recordset()
        self.assertEqual(len(v.photo_ids), 2)

    def test_c4_sin_firma_tamano_y_tipo(self):
        v, raw, priv, dev = self.tech_in_field()
        v.sudo().action_start_field()
        path = "/verifications/%s/photos" % v.uuid_ref
        # Con la clave de la app y sin firma.
        self.assertProblem(self.api("POST", path, raw=raw, body=self.photo_body(jpeg_bytes())), 403,
                           "signature-required")
        # Más de 5 MB decodificada.
        grande = b"\xff\xd8\xff\xe0" + b"\x00" * (5 * 1024 * 1024)
        self.assertProblem(self.signed_post(path, raw, dev, priv, self.photo_body(grande)), 413,
                           "payload-too-large")
        # No es una imagen (aunque diga image/jpeg).
        body = self.assertProblem(self.signed_post(path, raw, dev, priv,
                                                   self.photo_body(b"%PDF-1.4 no soy una foto")),
                                  422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "data")
        self.assertProblem(self.signed_post(path, raw, dev, priv, self.photo_body(b"x", mime="image/gif")),
                           422, "validation-error")
        v.invalidate_recordset()
        self.assertFalse(v.photo_ids)

    def test_c4_tecnico_ajeno_y_partes(self):
        v, _raw, _priv, _dev = self.tech_in_field()
        v.sudo().action_start_field()
        path = "/verifications/%s/photos" % v.uuid_ref
        body = self.photo_body(jpeg_bytes())
        # Otro técnico de la misma empresa (no asignado) no la ve.
        self.assertProblem(self.api("POST", path, raw=self.raw_tech2, body=body), 404)
        # Una camaronera ajena tampoco.
        self.assertProblem(self.api("POST", path, raw=self.raw_b, body=body), 404)
        # El comprador la ve pero no sube fotos.
        self.assertProblem(self.api("POST", path, raw=self.raw_packer, body=body), 403, "forbidden")
        # El administrador de la verificadora sí (clave manual: sin firma).
        resp = self.api("POST", path, raw=self.raw_verifier, body=body)
        self.assertEqual(resp.status_code, 201, resp.text)

    # ------------------------------------------------------------------
    # C5. Compra directa
    # ------------------------------------------------------------------
    def test_c5_compra_de_larva_firmada(self):
        raw, priv, dev = self.app_with_device("a.api")
        before = self.product.available_qty
        body = {"product": self.product.uuid_ref, "qty": 100, "notes": "Entregar en la mañana"}
        # Sin firma con la clave de la app.
        self.assertProblem(self.api("POST", "/purchases", raw=raw, body=body), 403, "signature-required")
        resp = self.signed_post("/purchases", raw, dev, priv, body)
        self.assertEqual(resp.status_code, 201, resp.text)
        tx = resp.json()
        self.assertEqual(tx["state"], "confirmed")
        self.assertEqual(tx["my_role"], "buyer")
        self.assertEqual(tx["qty"], 100)
        self.assertEqual(tx["product"]["id"], self.product.uuid_ref)
        self.assertEqual(tx["unit_price"]["amount"], 2.5)
        # Mismo serializer que GET /transactions/{id}.
        again = self.api("GET", "/transactions/%s" % tx["id"], raw=raw).json()
        self.assertEqual(set(again), set(tx))
        self.product.invalidate_recordset()
        self.assertAlmostEqual(self.product.available_qty, before - 100)
        # Avisos: el vendedor «Nueva compra», el comprador «Compra confirmada».
        self.assertTrue(self.notifs(self.lab, kind="purchase_received", target_uuid=tx["id"]))
        self.assertTrue(self.notifs(self.farm_a, kind="purchase_confirmed", target_uuid=tx["id"]))
        self.assertFalse(self.notifs(self.lab, kind="purchase_confirmed", target_uuid=tx["id"]))
        # La nota queda en la compra.
        record = self.env["shrimp.transaction"].sudo().search([("uuid_ref", "=", tx["id"])])
        self.assertIn("Entregar en la mañana", " ".join(record.message_ids.mapped("body")))

    def test_c5_lote_con_verificacion_y_perfil_que_no_compra(self):
        product, _tx, _v = self.verified_purchase()
        body = self.assertProblem(self.api("POST", "/purchases", raw=self.raw_packer,
                                           body={"product": product.uuid_ref, "qty": 10}),
                                  422, "verification-required")
        self.assertIn("/verifications", body["detail"])
        # Una empacadora no compra larva: 403 con el motivo del portal.
        body = self.assertProblem(self.api("POST", "/purchases", raw=self.raw_packer,
                                           body={"product": self.product.uuid_ref, "qty": 10}),
                                  403, "forbidden")
        self.assertTrue(body["detail"])
        # El laboratorio no se compra a sí mismo.
        self.assertProblem(self.api("POST", "/purchases", raw=self.raw_lab,
                                    body={"product": self.product.uuid_ref, "qty": 10}), 403, "forbidden")
        # Sin stock suficiente: regla de negocio.
        self.assertProblem(self.api("POST", "/purchases", raw=self.raw_b,
                                    body={"product": self.product.uuid_ref, "qty": 10 ** 7}),
                           422, "business-rule")
        # Lote inexistente / no publicado.
        self.assertProblem(self.api("POST", "/purchases", raw=self.raw_b,
                                    body={"product": str(uuid.uuid4()), "qty": 1}), 422,
                           "validation-error")

    # ------------------------------------------------------------------
    # C6. assetlinks.json
    # ------------------------------------------------------------------
    def test_c6_assetlinks(self):
        package = "com.camaronmarket.verificador_campo"
        resp = self.url_open("/.well-known/assetlinks.json")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers["Content-Type"], "application/json")
        # Sin huellas no hay entrada de CamaronMarket (la de la app oficial de
        # Odoo, que pone auth_passkey para las passkeys, se conserva).
        base = resp.json()
        self.assertNotIn(package, [e["target"]["package_name"] for e in base])
        h1 = ":".join(["AB"] * 32)
        h2 = "cd" * 32   # sin dos puntos y en minúsculas: se normaliza
        self.set_param("shrimp_api.android_cert_sha256", "%s, %s, no-es-huella" % (h1, h2))
        data = self.url_open("/.well-known/assetlinks.json").json()
        self.assertEqual(len(data), len(base) + 1)
        ours = data[0]
        self.assertEqual(ours["relation"], ["delegate_permission/common.handle_all_urls"])
        self.assertEqual(ours["target"], {"namespace": "android_app", "package_name": package,
                                          "sha256_cert_fingerprints": [h1, ":".join(["CD"] * 32)]})
        self.assertEqual(data[1:], base)
        # La función propia, sin huellas, es la lista vacía del contrato.
        from odoo.addons.shrimp_api.controllers.well_known import assetlinks
        self.set_param("shrimp_api.android_cert_sha256", "")
        self.assertEqual(assetlinks(self.env), [])

    # ------------------------------------------------------------------
    def test_openapi_documenta_las_rutas_nuevas(self):
        spec = self.api("GET", "/openapi.json").json()
        paths = spec["paths"]
        self.assertTrue(paths["/api/v1/verifications/{id}/photos"]["post"]["x-signed"])
        self.assertTrue(paths["/api/v1/purchases"]["post"]["x-signed"])
        self.assertIn("/api/v1/public/app-version", paths)
        self.assertIn("/api/v1/public/app-errors", paths)
        self.assertIn("/api/v1/me/notifications", paths)
        self.assertIn("426", paths["/api/v1/me"]["get"]["responses"])

    def test_c3_texto_sin_punto_doble(self):
        from odoo.addons.shrimp_api.models.app_notification import _item
        partner = self.env["res.partner"]
        body = _item(partner, False, "x", "T", "Te asignaron VER-1 de Río Chone S.A..", "v", "u", "/l")["body"]
        self.assertEqual(body, "Te asignaron VER-1 de Río Chone S.A.")
        body = _item(partner, False, "x", "T", "Espera...", "v", "u", "/l")["body"]
        self.assertEqual(body, "Espera...")
