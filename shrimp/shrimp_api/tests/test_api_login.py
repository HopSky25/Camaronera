"""Inicio de sesión de la app móvil: POST /auth/login y POST /auth/logout."""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.shrimp_api.controllers.auth import (APP_KEY_PREFIX, LOGIN_MAX_FAILURES,
                                                     app_scopes)

from .common import ApiCase

PASSWORD = "Clave-api-123"


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiLogin(ApiCase):

    def setUp(self):
        super().setUp()
        # res.users._login lleva su propio contador de fallos por IP en
        # memoria del registro (base.login_cooldown_after). Se limpia para
        # que un test de fallos no bloquee al siguiente.
        self._clear_odoo_cooldown()
        self.addCleanup(self._clear_odoo_cooldown)

    def _clear_odoo_cooldown(self):
        failures = getattr(self.env.registry, "_login_failures", None)
        if failures is not None:
            failures.clear()

    def login(self, login, password, device=None):
        body = {"login": login, "password": password}
        if device is not None:
            body["device"] = device
        return self.api("POST", "/auth/login", body=body)

    # ------------------------------------------------------------------
    def test_login_ok_devuelve_clave_usable(self):
        resp = self.login("a.api", PASSWORD, device="Pixel 7 de prueba")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        data = resp.json()
        self.assertEqual(set(data), {"api_key", "expires_at", "me"})
        secret = data["api_key"]
        self.assertTrue(secret.startswith("trz_"), secret)

        key = self.env["shrimp.api.key"].sudo().search([("user_id", "=", self.u_a.id)], order="id desc", limit=1)
        self.assertEqual(key.name, APP_KEY_PREFIX + "Pixel 7 de prueba")
        self.assertTrue(secret.startswith("trz_%s_" % key.key_prefix))
        self.assertEqual(sorted(key.scope_ids.mapped("code")), app_scopes())
        self.assertNotIn("webhooks:manage", key.scope_ids.mapped("code"))
        self.assertIn("catalog:read", key.scope_ids.mapped("code"))
        self.assertIn("verifications:write", key.scope_ids.mapped("code"))
        self.assertFalse(key.ip_allowlist)
        self.assertFalse(key.allowed_origins)
        self.assertFalse(key.key)  # el secreto no se guarda en claro
        # Caduca en un año (± un día).
        delta = key.expires_at - fields.Datetime.now()
        self.assertTrue(timedelta(days=364) < delta <= timedelta(days=365), delta)
        self.assertEqual(data["expires_at"][-1], "Z")

        # La clave sirve y "me" es exactamente lo que devuelve GET /me.
        me = self.api("GET", "/me", raw=secret)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json(), data["me"])
        self.assertEqual(data["me"]["id"], self.farm_a.uuid_ref)
        self.assertEqual(data["me"]["api_key"]["prefix"], key.key_prefix)
        self.assertEqual(data["me"]["active_role"], "camaronera")
        # Y permite escribir (scope de la app).
        creado = self.api("POST", "/facilities", raw=secret, body={"name": "Desde el móvil"})
        self.assertEqual(creado.status_code, 201, creado.text)

    def test_login_sin_device_y_sin_idempotency_key(self):
        # Ruta pública: no exige Idempotency-Key (self.api no la manda sin clave).
        resp = self.login("b.api", PASSWORD)
        self.assertEqual(resp.status_code, 200, resp.text)
        key = self.env["shrimp.api.key"].sudo().search([("user_id", "=", self.u_b.id)], order="id desc", limit=1)
        self.assertEqual(key.name, APP_KEY_PREFIX + "dispositivo")

    def test_contrasena_mala_401_generico(self):
        body = self.assertProblem(self.login("a.api", "incorrecta"), 401, "invalid-credentials")
        # Un login inexistente recibe exactamente lo mismo.
        body2 = self.assertProblem(self.login("nadie@ninguna.parte", "incorrecta"), 401, "invalid-credentials")
        self.assertEqual(body["detail"], body2["detail"])
        self.assertNotIn("incorrecta", body["detail"])
        # No se creó ninguna clave.
        self.assertFalse(self.env["shrimp.api.key"].sudo().search(
            [("user_id", "=", self.u_a.id), ("name", "like", APP_KEY_PREFIX + "%")]))

    def test_cuerpo_invalido_422(self):
        body = self.assertProblem(self.api("POST", "/auth/login", body={"login": "a.api"}), 422, "validation-error")
        self.assertIn("password", [e["field"] for e in body["errors"]])
        body = self.assertProblem(self.api("POST", "/auth/login", body={"login": "a.api", "password": "x",
                                                                         "otro": 1}), 422)
        self.assertIn("otro", [e["field"] for e in body["errors"]])
        self.assertProblem(self.api("POST", "/auth/login", body={"login": "a.api", "password": "x",
                                                                  "device": "d" * 65}), 422)

    def test_usuario_interno_403(self):
        self.env["res.users"].create({
            "name": "Interno app", "login": "interno.app", "password": PASSWORD,
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.assertProblem(self.login("interno.app", PASSWORD), 403, "user-not-allowed")

    def test_cuenta_sin_perfil_403(self):
        partner = self.env["res.partner"].create({"name": "Sin perfil", "email": "sin.perfil@prueba.test"})
        self.env["res.users"].create({
            "name": partner.name, "login": "sin.perfil", "partner_id": partner.id, "password": PASSWORD,
            "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertFalse(partner.shrimp_user_type)
        self.assertFalse(partner.shrimp_role_ids)
        body = self.assertProblem(self.login("sin.perfil", PASSWORD), 403, "account-not-approved")
        self.assertIn("perfil", body["detail"])

    def test_perfil_pendiente_no_bloquea(self):
        # Un perfil pendiente de aprobación entra igual: la app enseña el estado.
        rol = self.farm_b.sudo().shrimp_role_ids.filtered(lambda r: r.role == "camaronera")
        self.assertTrue(rol)
        rol.write({"state": "pending"})
        resp = self.login("b.api", PASSWORD)
        self.assertEqual(resp.status_code, 200, resp.text)
        estados = {r["role"]: r["state"] for r in resp.json()["me"]["roles"]}
        self.assertEqual(estados.get("camaronera"), "pending")

    def test_seis_fallos_429(self):
        for i in range(LOGIN_MAX_FAILURES):
            self.assertProblem(self.login("a.api", "mala-%s" % i), 401, "invalid-credentials")
        resp = self.login("a.api", "mala-6")
        self.assertProblem(resp, 429, "rate-limited")
        self.assertTrue(int(resp.headers.get("Retry-After", "0")) >= 1)
        # Bloqueado aunque ahora la contraseña sea la buena.
        self.assertProblem(self.login("a.api", PASSWORD), 429, "rate-limited")
        # El bloqueo es por (IP, login): otro usuario entra sin problema.
        self.assertEqual(self.login("b.api", PASSWORD).status_code, 200)
        # Pasada la ventana, vuelve a dejar entrar y el contador se limpia.
        self.env["shrimp.rate.limit"].sudo().search([("key", "like", "api-login:%")]).write(
            {"ts": fields.Datetime.now() - timedelta(minutes=16)})
        self.assertEqual(self.login("a.api", PASSWORD).status_code, 200)
        self.assertFalse(self.env["shrimp.rate.limit"].sudo().search(
            [("key", "like", "api-login:%:a.api")]))

    def test_logout_revoca_la_clave(self):
        secret = self.login("a.api", PASSWORD, device="Tablet").json()["api_key"]
        self.assertEqual(self.api("GET", "/me", raw=secret).status_code, 200)
        resp = self.api("POST", "/auth/logout", raw=secret)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"revoked": True})
        key = self.env["shrimp.api.key"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.u_a.id), ("name", "=", APP_KEY_PREFIX + "Tablet")])
        self.assertEqual(key.status, "revoked")
        self.assertEqual(key.revoked_by_id, self.u_a)
        self.assertProblem(self.api("GET", "/me", raw=secret), 401, "revoked-api-key")
        self.assertProblem(self.api("POST", "/auth/logout", raw=secret), 401, "revoked-api-key")
        # Logout exige Idempotency-Key como todo POST privado.
        secret2 = self.login("a.api", PASSWORD, device="Tablet 2").json()["api_key"]
        self.assertProblem(self.api("POST", "/auth/logout", raw=secret2, idem=False), 400,
                           "idempotency-key-required")

    def test_tope_de_claves_revoca_las_de_la_app(self):
        from odoo.addons.shrimp_api.controllers.portal import MAX_ACTIVE_KEYS
        Key = self.env["shrimp.api.key"].sudo()
        # u_lab ya tiene 1 clave manual (common.py); se le dan 9 de la app.
        for i in range(MAX_ACTIVE_KEYS - 1):
            self.assertEqual(self.login("lab.api", PASSWORD, device="tel %s" % i).status_code, 200)
        activas = Key.search([("user_id", "=", self.u_lab.id)]).filtered(lambda k: k.status == "active")
        self.assertEqual(len(activas), MAX_ACTIVE_KEYS)
        # La siguiente entrada revoca la clave de la app más antigua, no la manual.
        self.assertEqual(self.login("lab.api", PASSWORD, device="tel nuevo").status_code, 200)
        activas = Key.search([("user_id", "=", self.u_lab.id)]).filtered(lambda k: k.status == "active")
        self.assertEqual(len(activas), MAX_ACTIVE_KEYS)
        self.assertIn(self.key_lab, activas)
        self.assertNotIn(APP_KEY_PREFIX + "tel 0", activas.mapped("name"))
        self.assertIn(APP_KEY_PREFIX + "tel nuevo", activas.mapped("name"))

    def test_openapi_documenta_las_rutas(self):
        spec = self.url_open("/api/v1/openapi.json").json()
        login = spec["paths"]["/api/v1/auth/login"]["post"]
        self.assertEqual(login["security"], [])
        self.assertNotIn("Idempotency-Key", [p["name"] for p in login.get("parameters", [])])
        logout = spec["paths"]["/api/v1/auth/logout"]["post"]
        self.assertIn("Idempotency-Key", [p["name"] for p in logout.get("parameters", [])])
