"""Modo supervisor de la app (contrato docs/app/CONTRATO-FIRMA-DISPOSITIVO.md,
Anexo B): login de supervisor, directorio de socios y «actuar como» en solo
lectura."""

import json
import time
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase
from .test_api_dispositivos import new_keypair, sign

PASSWORD = "Clave-api-123"
READ_SCOPES = sorted(c for c in SCOPE_CODES if c.endswith(":read"))


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiSupervisor(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.u_sup = env["res.users"].create({
            "name": "Ana Supervisora", "login": "sup.api", "password": PASSWORD,
            "email": "sup.api@prueba.test",
            "group_ids": [(6, 0, [env.ref("base.group_user").id,
                                  env.ref("shrimp_api.group_shrimp_app_supervisor").id])]})
        cls.u_interno = env["res.users"].create({
            "name": "Interno sin grupo", "login": "interno.sup.api", "password": PASSWORD,
            "group_ids": [(6, 0, [env.ref("base.group_user").id])]})
        P = env["res.partner"]
        # Socio aprobado pero sin usuario de portal.
        cls.sin_usuario = P.create({
            "name": "Camaronera Sin Usuario API", "is_company": True,
            "email": "sinusuario.api@prueba.test", "vat_or_id": "0999900009001",
            "shrimp_user_type": "camaronera", "farm_razon_social": "Sin Usuario S.A.",
            "farm_representante": "Rep", "farm_telefono": "04-0000000", "farm_ubicacion": "Guayas",
        })
        # Socio con usuario de portal pero sin ningún perfil aprobado.
        cls.pendiente = P.create({
            "name": "Camaronera Pendiente API", "is_company": True,
            "email": "pendiente.api@prueba.test", "vat_or_id": "0999900008001",
            "shrimp_user_type": "camaronera", "farm_razon_social": "Pendiente S.A.",
            "farm_representante": "Rep", "farm_telefono": "04-0000000", "farm_ubicacion": "Guayas",
        })
        cls.pendiente.shrimp_role_ids.write({"state": "pending"})
        cls.pendiente.shrimp_account_state = "pending"
        env["res.users"].create({
            "name": cls.pendiente.name, "login": "pendiente.api", "partner_id": cls.pendiente.id,
            "password": PASSWORD, "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})

    def setUp(self):
        super().setUp()
        failures = getattr(self.env.registry, "_login_failures", None)
        if failures is not None:
            failures.clear()

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------
    def login(self, login="sup.api"):
        return self.api("POST", "/auth/login", body={"login": login, "password": PASSWORD,
                                                     "device": "Pixel del supervisor"})

    def sup_login(self):
        resp = self.login()
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def sup_with_device(self):
        data = self.sup_login()
        raw = data["api_key"]
        priv, pub = new_keypair()
        resp = self.api("POST", "/me/devices", raw=raw, body={
            "name": "Pixel del supervisor", "platform": "android", "public_key": pub, "alg": "ES256"})
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

    def act_as(self, partner, sup=None):
        raw, priv, dev = sup or self.sup_with_device()
        resp, _h, _d = self.signed_post("/supervisor/act-as", raw, dev, priv,
                                        {"partner": partner.uuid_ref})
        return resp

    def key_of(self, secret):
        prefix = secret.split("_")[1]
        return self.env["shrimp.api.key"].sudo().with_context(active_test=False).search(
            [("key_prefix", "=", prefix)])

    # ------------------------------------------------------------------
    # B.1 Login
    # ------------------------------------------------------------------
    def test_login_supervisor(self):
        data = self.sup_login()
        self.assertIs(data["supervisor"], True)
        self.assertIs(data["device_required"], True)
        self.assertTrue(data["api_key"].startswith("trz_"))
        me = data["me"]
        self.assertEqual(me["name"], "Ana Supervisora")
        self.assertIsNone(me["type"])
        self.assertIs(me["supervisor"], True)
        self.assertEqual(me["roles"], [])
        key = self.key_of(data["api_key"])
        self.assertEqual(key.user_id, self.u_sup)
        self.assertTrue(key.is_supervisor_key)
        self.assertTrue(key.is_app_key)
        self.assertFalse(key.scope_ids)
        # 8 h.
        delta = key.expires_at - fields.Datetime.now()
        self.assertTrue(timedelta(hours=7, minutes=58) < delta <= timedelta(hours=8), delta)
        # GET /me con esa clave devuelve el mismo «me».
        resp = self.api("GET", "/me", raw=data["api_key"])
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), me)

    def test_interno_sin_grupo_403(self):
        self.assertProblem(self.login("interno.sup.api"), 403, "user-not-allowed")
        # El administrador del sistema sí es supervisor (base.group_system lo implica).
        self.assertTrue(self.env.ref("base.user_admin")._has_group(
            "shrimp_api.group_shrimp_app_supervisor"))

    def test_quitar_el_grupo_corta_la_clave(self):
        raw = self.sup_login()["api_key"]
        self.u_sup.group_ids = [(3, self.env.ref("shrimp_api.group_shrimp_app_supervisor").id)]
        self.assertProblem(self.api("GET", "/me", raw=raw), 403, "user-not-allowed")

    def test_clave_de_supervisor_no_lee_datos(self):
        raw = self.sup_login()["api_key"]
        for path in ("/transactions", "/products", "/lots", "/facilities", "/verifications",
                     "/charges", "/price-lists", "/webhooks"):
            self.assertProblem(self.api("GET", path, raw=raw), 403, "supervisor-key")
        self.assertProblem(self.api("PATCH", "/me", raw=raw, body={"phone": "1"}), 403, "supervisor-key")
        self.assertProblem(self.api("POST", "/facilities", raw=raw, body={"name": "x"}), 403,
                           "supervisor-key")
        # Lo suyo sí: /me, sus dispositivos.
        self.assertEqual(self.api("GET", "/me", raw=raw).status_code, 200)
        self.assertEqual(self.api("GET", "/me/devices", raw=raw).status_code, 200)
        # Y las rutas de supervisor no las admite otra clave.
        self.assertProblem(self.api("GET", "/supervisor/partners", raw=self.raw_a), 403,
                           "supervisor-only")
        self.assertProblem(self.api("POST", "/supervisor/act-as", raw=self.raw_a,
                                    body={"partner": self.farm_b.uuid_ref}), 403, "supervisor-only")

    # ------------------------------------------------------------------
    # B.2 Directorio
    # ------------------------------------------------------------------
    def test_directorio_filtra_por_q_y_type(self):
        raw = self.sup_login()["api_key"]
        resp = self.api("GET", "/supervisor/partners?q=API&limit=200", raw=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        rows = {r["id"]: r for r in resp.json()["data"]}
        for p in (self.farm_a, self.farm_b, self.packer, self.lab):
            self.assertIn(p.uuid_ref, rows)
        # Sin usuario de portal, sin perfil aprobado o interno: fuera.
        self.assertNotIn(self.sin_usuario.uuid_ref, rows)
        self.assertNotIn(self.pendiente.uuid_ref, rows)
        self.assertNotIn(self.u_sup.partner_id.uuid_ref, rows)
        fa = rows[self.farm_a.uuid_ref]
        self.assertEqual(set(fa), {"id", "name", "type", "roles", "city", "province"})
        self.assertEqual(fa["type"], "camaronera")
        self.assertEqual(fa["roles"], ["camaronera"])

        resp = self.api("GET", "/supervisor/partners?q=API&type=empacadora", raw=raw)
        ids = [r["id"] for r in resp.json()["data"]]
        self.assertIn(self.packer.uuid_ref, ids)
        self.assertNotIn(self.farm_a.uuid_ref, ids)

        resp = self.api("GET", "/supervisor/partners?q=Camaronera A API", raw=raw)
        self.assertEqual([r["id"] for r in resp.json()["data"]], [self.farm_a.uuid_ref])
        self.assertProblem(self.api("GET", "/supervisor/partners?type=astronauta", raw=raw), 400,
                           "invalid-parameter")

    # ------------------------------------------------------------------
    # B.3 Actuar como
    # ------------------------------------------------------------------
    def test_act_as_exige_dispositivo_y_firma(self):
        raw = self.sup_login()["api_key"]
        body = {"partner": self.farm_a.uuid_ref}
        self.assertProblem(self.api("POST", "/supervisor/act-as", raw=raw, body=body), 403,
                           "device-not-registered")
        raw, priv, dev = self.sup_with_device()
        self.assertProblem(self.api("POST", "/supervisor/act-as", raw=raw, body=body), 403,
                           "signature-required")
        # Firmado por OTRO dispositivo (el de otra clave de supervisor): no.
        _raw2, priv2, dev2 = self.sup_with_device()
        resp, _h, _d = self.signed_post("/supervisor/act-as", raw, dev2, priv2, body)
        self.assertProblem(resp, 403, "device-not-registered")
        self.assertFalse(self.env["shrimp.api.key"].sudo().search(
            [("supervisor_user_id", "=", self.u_sup.id)]))

    def test_act_as_firmado_lee_en_solo_lectura(self):
        tx = self.product.sudo().execute_purchase_flow(self.farm_a, 5.0)["transaction"]
        sup = self.sup_with_device()
        resp = self.act_as(self.farm_a, sup)
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual(set(data), {"api_key", "expires_at", "partner", "me", "read_only"})
        self.assertIs(data["read_only"], True)
        self.assertEqual(data["partner"]["id"], self.farm_a.uuid_ref)
        self.assertEqual(data["me"]["id"], self.farm_a.uuid_ref)
        self.assertIs(data["me"]["read_only"], True)
        self.assertEqual(data["me"]["viewed_by"], {"name": "Ana Supervisora"})
        secret = data["api_key"]

        key = self.key_of(secret)
        self.assertEqual(key.user_id, self.u_a)
        self.assertEqual(key.supervisor_user_id, self.u_sup)
        self.assertEqual(key.supervisor_key_id, self.key_of(sup[0]))
        self.assertTrue(key.is_app_key)
        self.assertFalse(key.is_supervisor_key)
        self.assertEqual(sorted(key.scope_ids.mapped("code")), READ_SCOPES)
        self.assertFalse(key.device_ids)
        # Caduca a las 8 h (no más que la clave de supervisor).
        delta = key.expires_at - fields.Datetime.now()
        self.assertTrue(timedelta(hours=7, minutes=58) < delta <= timedelta(hours=8), delta)
        self.assertLessEqual(key.expires_at, key.supervisor_key_id.expires_at)

        # GET /me con la clave = el «me» de la respuesta.
        me = self.api("GET", "/me", raw=secret)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json(), data["me"])
        # Lee las compras del socio, con sus reglas.
        buys = self.api("GET", "/transactions?role=buyer", raw=secret)
        self.assertEqual(buys.status_code, 200, buys.text)
        self.assertIn(tx.uuid_ref, [t["id"] for t in buys.json()["data"]])
        self.assertProblem(self.api("GET", "/transactions/%s" % tx.uuid_ref, raw=self.raw_b), 404)

        # Ninguna escritura: insufficient-scope (nunca device-not-registered).
        self.assertProblem(self.api("POST", "/facilities", raw=secret, body={"name": "x"}), 403,
                           "insufficient-scope")
        self.assertProblem(self.api("PATCH", "/me", raw=secret, body={"phone": "1"}), 403,
                           "insufficient-scope")
        self.assertProblem(self.api("POST", "/me/active-role", raw=secret,
                                    body={"role": "camaronera"}), 403, "insufficient-scope")
        self.assertProblem(self.api("POST", "/transactions/%s:complete" % tx.uuid_ref, raw=secret,
                                    body={}), 403, "insufficient-scope")
        _priv, pub = new_keypair()
        self.assertProblem(self.api("POST", "/me/devices", raw=secret, body={
            "name": "x", "platform": "android", "public_key": pub, "alg": "ES256"}), 403,
            "insufficient-scope")
        # Tampoco encadena otro act-as.
        self.assertProblem(self.api("POST", "/supervisor/act-as", raw=secret,
                                    body={"partner": self.farm_b.uuid_ref}), 403, "supervisor-only")

        # Nota en el historial del socio y registro en la auditoría.
        nota = self.env["mail.message"].sudo().search(
            [("model", "=", "res.partner"), ("res_id", "=", self.farm_a.id),
             ("body", "ilike", "vio tu cuenta desde la app (solo lectura)")])
        self.assertEqual(len(nota), 1)
        self.assertIn("Ana Supervisora", nota.body)
        self.assertEqual(nota.author_id, self.u_sup.partner_id)
        audit = self.env["shrimp.api.signed.operation"].sudo().search(
            [("act_as_partner_id", "=", self.farm_a.id)])
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.user_id, self.u_sup)
        self.assertEqual(audit.act_as_key_id, key)
        self.assertEqual(audit.path, "/api/v1/supervisor/act-as")
        self.assertEqual(audit.status_code, 201)

    def test_act_as_respeta_x_shrimp_role(self):
        # La camaronera A también es empacadora (perfil aprobado).
        self.env["shrimp.partner.role"].create({"partner_id": self.farm_a.id, "role": "empacadora",
                                                "state": "approved"})
        secret = self.act_as(self.farm_a).json()["api_key"]
        self.assertProblem(self.api("GET", "/lot-alerts", raw=secret), 403, "forbidden")
        resp = self.api("GET", "/lot-alerts", raw=secret, headers={"X-Shrimp-Role": "empacadora"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertProblem(self.api("GET", "/lot-alerts", raw=secret,
                                    headers={"X-Shrimp-Role": "maquilador"}), 403, "forbidden")

    def test_reintento_idempotente_no_devuelve_el_secreto(self):
        raw, priv, dev = self.sup_with_device()
        idem = str(uuid.uuid4())
        resp, h, data = self.signed_post("/supervisor/act-as", raw, dev, priv,
                                         {"partner": self.farm_a.uuid_ref}, idem=idem)
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertTrue(resp.json()["api_key"])
        again = self.url_open("/api/v1/supervisor/act-as", data=data, headers=h, method="POST")
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertIsNone(again.json()["api_key"])
        self.assertEqual(again.json()["partner"]["id"], self.farm_a.uuid_ref)
        stored = self.env["shrimp.api.idempotency"].sudo().search([("key", "=", idem)])
        self.assertNotIn(resp.json()["api_key"], stored.response_body)
        # Una sola clave emitida.
        self.assertEqual(self.env["shrimp.api.key"].sudo().search_count(
            [("supervisor_user_id", "=", self.u_sup.id)]), 1)

    def test_caducidad_tope_de_la_clave_de_supervisor(self):
        sup = self.sup_with_device()
        sup_key = self.key_of(sup[0])
        tope = fields.Datetime.now() + timedelta(hours=1)
        sup_key.write({"expires_at": tope})
        secret = self.act_as(self.farm_a, sup).json()["api_key"]
        self.assertLessEqual(self.key_of(secret).expires_at, tope)
        # Caducada la de supervisor, la de act-as tampoco sirve.
        sup_key.write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.key_of(secret).write({"expires_at": fields.Datetime.now() + timedelta(hours=1)})
        self.assertProblem(self.api("GET", "/me", raw=secret), 401, "expired-api-key")

    def test_revocar_supervisor_revoca_act_as(self):
        sup = self.sup_with_device()
        a = self.act_as(self.farm_a, sup).json()["api_key"]
        b = self.act_as(self.packer, sup).json()["api_key"]
        self.assertEqual(self.api("GET", "/me", raw=a).status_code, 200)
        resp = self.api("POST", "/auth/logout", raw=sup[0])
        self.assertEqual(resp.status_code, 200, resp.text)
        for secret in (a, b):
            self.assertProblem(self.api("GET", "/me", raw=secret), 401, "revoked-api-key")
            self.assertTrue(self.key_of(secret).revoked_at)

    def test_logout_con_la_de_act_as_solo_revoca_esa(self):
        sup = self.sup_with_device()
        secret = self.act_as(self.farm_a, sup).json()["api_key"]
        self.assertEqual(self.api("POST", "/auth/logout", raw=secret).status_code, 200)
        self.assertProblem(self.api("GET", "/me", raw=secret), 401, "revoked-api-key")
        self.assertEqual(self.api("GET", "/me", raw=sup[0]).status_code, 200)

    def test_revocar_el_dispositivo_del_supervisor_revoca_todo(self):
        raw, _priv, dev = sup = self.sup_with_device()
        secret = self.act_as(self.farm_a, sup).json()["api_key"]
        resp = self.api("POST", "/me/devices/%s:revoke" % dev, raw=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertProblem(self.api("GET", "/me", raw=secret), 401, "revoked-api-key")

    def test_act_as_socios_no_validos(self):
        sup = self.sup_with_device()
        self.assertProblem(self.act_as(self.sin_usuario, sup), 422, "partner-without-portal-user")
        self.assertProblem(self.act_as(self.pendiente, sup), 422, "partner-not-approved")
        self.assertProblem(self.act_as(self.u_interno.partner_id, sup), 403, "act-as-internal-user")
        self.assertProblem(self.act_as(self.u_sup.partner_id, sup), 403, "act-as-internal-user")
        raw, priv, dev = sup
        resp, _h, _d = self.signed_post("/supervisor/act-as", raw, dev, priv,
                                        {"partner": str(uuid.uuid4())})
        self.assertProblem(resp, 404, "not-found")
        self.assertFalse(self.env["shrimp.api.key"].sudo().search(
            [("supervisor_user_id", "=", self.u_sup.id)]))

    def test_nunca_claves_de_escritura(self):
        from odoo.exceptions import ValidationError
        sup_key = self.key_of(self.sup_login()["api_key"])
        with self.assertRaises(ValidationError):
            self.env["shrimp.api.key"].sudo().create({
                "name": "trampa", "user_id": self.u_a.id, "key_prefix": "deadbeef",
                "supervisor_key_id": sup_key.id, "supervisor_user_id": self.u_sup.id,
                "scope_ids": [(6, 0, self.env["shrimp.api.scope"].search(
                    [("code", "=", "transactions:write")]).ids)]})
        # Y una clave de supervisor no lleva scopes.
        with self.assertRaises(ValidationError):
            sup_key.scope_ids = [(6, 0, self.env["shrimp.api.scope"].search(
                [("code", "=", "catalog:read")]).ids)]

    def test_act_as_no_lo_bloquean_los_documentos_del_socio(self):
        # El socio tiene versiones MAYORES sin aceptar: su propia clave queda
        # bloqueada, pero el supervisor puede ver su app en solo lectura.
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        self.assertTrue(self.env["shrimp.legal.document"].sudo()._shrimp_pending(self.u_a)["major"])
        resp = self.act_as(self.farm_a)
        self.assertEqual(resp.status_code, 201, resp.text)
        secret = resp.json()["api_key"]
        self.assertEqual(self.api("GET", "/facilities", raw=secret).status_code, 200)
