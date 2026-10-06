import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.shrimp_api.hooks import migrate_legacy_keys

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiAuth(ApiCase):

    def test_missing_key(self):
        resp = self.api("GET", "/me")
        self.assertProblem(resp, 401, "missing-api-key")
        self.assertIn("Bearer", resp.headers.get("WWW-Authenticate", ""))

    def test_invalid_key(self):
        self.assertProblem(self.api("GET", "/me", raw="trz_deadbeef_" + "x" * 43), 401, "invalid-api-key")
        self.assertProblem(self.api("GET", "/me", raw="cualquier-cosa"), 401, "invalid-api-key")
        # Prefijo correcto, secreto equivocado.
        wrong = "trz_%s_%s" % (self.key_a.key_prefix, "y" * 43)
        self.assertProblem(self.api("GET", "/me", raw=wrong), 401, "invalid-api-key")

    def test_valid_key_bearer_and_header(self):
        resp = self.api("GET", "/me", raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["id"], self.farm_a.uuid_ref)
        resp = self.url_open("/api/v1/me", headers={"X-API-Key": self.raw_a})
        self.assertEqual(resp.status_code, 200)
        resp = self.url_open("/api/v1/me", headers={"X-API-Key": self.raw_a,
                                                    "Authorization": "Bearer " + self.raw_b})
        self.assertProblem(resp, 400, "ambiguous-credentials")

    def test_expired_key(self):
        self.key_a.sudo().expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertProblem(self.api("GET", "/me", raw=self.raw_a), 401, "expired-api-key")

    def test_revoked_key(self):
        self.assertEqual(self.api("GET", "/me", raw=self.raw_b).status_code, 200)
        self.key_b.action_revoke()
        self.assertProblem(self.api("GET", "/me", raw=self.raw_b), 401, "revoked-api-key")

    def test_key_stored_only_as_hash(self):
        key = self.key_a.sudo()
        self.assertFalse(key.key, "La columna de texto plano debe quedar vacía")
        self.assertTrue(key.key_hash.startswith("$pbkdf2-sha512$"))
        self.assertNotIn(self.raw_a, key.key_hash)
        self.env.cr.execute("SELECT count(*) FROM shrimp_api_key WHERE key = %s OR key_hash = %s "
                            "OR key_prefix = %s", (self.raw_a, self.raw_a, self.raw_a))
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        self.assertTrue(self.raw_a.startswith("trz_%s_" % key.key_prefix))

    def test_internal_user_cannot_get_key(self):
        with self.assertRaises(ValidationError):
            self.env["shrimp.api.key"].api_create_key(self.env.ref("base.user_admin"), "x", ["catalog:read"])

    def test_scope_required(self):
        _key, raw = self.env["shrimp.api.key"].api_create_key(self.u_a, "solo catálogo", ["catalog:read"])
        body = self.assertProblem(self.api("GET", "/products", raw=raw), 403, "insufficient-scope")
        self.assertIn("products:read", body["detail"])
        # /me no exige scope.
        self.assertEqual(self.api("GET", "/me", raw=raw).status_code, 200)

    def test_ip_allowlist(self):
        self.key_a.sudo().ip_allowlist = "10.255.255.1"
        self.assertProblem(self.api("GET", "/me", raw=self.raw_a), 403, "ip-not-allowed")
        self.key_a.sudo().ip_allowlist = "127.0.0.0/8\n::1"
        self.assertEqual(self.api("GET", "/me", raw=self.raw_a).status_code, 200)

    def test_problem_json_and_routing(self):
        self.assertProblem(self.api("GET", "/no-existe", raw=self.raw_a), 404, "not-found")
        resp = self.api("DELETE", "/me", raw=self.raw_a)
        self.assertProblem(resp, 405, "method-not-allowed")
        self.assertIn("GET", resp.headers.get("Allow", ""))

    def test_body_validation(self):
        resp = self.api("POST", "/facilities", raw=self.raw_a, data="[1, 2]",
                        headers={"Content-Type": "application/json"})
        self.assertProblem(resp, 400, "body-not-object")
        resp = self.api("POST", "/facilities", raw=self.raw_a, data="{no es json",
                        headers={"Content-Type": "application/json"})
        self.assertProblem(resp, 400, "invalid-json")
        resp = self.api("POST", "/facilities", raw=self.raw_a, data="name=x",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertProblem(resp, 415, "unsupported-media-type")
        body = self.assertProblem(self.api("POST", "/facilities", raw=self.raw_a,
                                           body={"name": "X", "partner_id": 1}), 422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "partner_id")
        body = self.assertProblem(self.api("POST", "/facilities", raw=self.raw_a, body={}), 422)
        self.assertIn("name", [e["field"] for e in body["errors"]])

    def test_business_error_is_422_without_trace(self):
        # Asignar más de lo que tiene el lote: lo rechaza el modelo.
        pond = self.env["shrimp.partner.pond"].create({"partner_id": self.farm_a.id, "name": "P1"})
        lot = self.env["shrimp.stock.lot"].create({
            "product_id": self.product.id, "owner_id": self.farm_a.id,
            "initial_qty": 10, "available_qty": 10, "uom_id": self.uom_millar.id})
        body = self.assertProblem(self.api("POST", "/lots/%s/allocations" % lot.uuid_ref, raw=self.raw_a,
                                           body={"pond": pond.uuid_ref, "allocated_qty": 99}),
                                  422, "business-rule")
        self.assertNotIn("shrimp.", body["detail"])
        self.assertFalse(self.env["shrimp.lot.allocation"].search([("stock_lot_id", "=", lot.id)]))

    def test_idempotency(self):
        key = str(uuid.uuid4())
        first = self.api("POST", "/facilities", raw=self.raw_a, body={"name": "Granja idem"}, idem=key)
        self.assertEqual(first.status_code, 201, first.text)
        again = self.api("POST", "/facilities", raw=self.raw_a, body={"name": "Granja idem"}, idem=key)
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(self.env["shrimp.partner.facility"].search_count(
            [("partner_id", "=", self.farm_a.id), ("name", "=", "Granja idem")]), 1)
        # Misma clave, otro cuerpo.
        self.assertProblem(self.api("POST", "/facilities", raw=self.raw_a,
                                    body={"name": "Otra"}, idem=key), 422, "idempotency-key-reused")
        # Sin cabecera.
        self.assertProblem(self.api("POST", "/facilities", raw=self.raw_a,
                                    body={"name": "Sin clave"}, idem=False), 400, "idempotency-key-required")

    def test_idempotency_replays_errors_too(self):
        key = str(uuid.uuid4())
        first = self.api("POST", "/facilities", raw=self.raw_a, body={"code": "x"}, idem=key)
        self.assertEqual(first.status_code, 422)
        again = self.api("POST", "/facilities", raw=self.raw_a, body={"code": "x"}, idem=key)
        self.assertEqual(again.status_code, 422)
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")

    def test_rate_limit(self):
        self.key_b.sudo().rate_read_per_min = 2
        self.assertEqual(self.api("GET", "/me", raw=self.raw_b).status_code, 200)
        resp = self.api("GET", "/me", raw=self.raw_b)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-RateLimit-Remaining"), "0")
        resp = self.api("GET", "/me", raw=self.raw_b)
        self.assertProblem(resp, 429, "rate-limited")
        self.assertTrue(int(resp.headers["Retry-After"]) >= 1)
        # Las escrituras tienen su propio cupo.
        self.key_b.sudo().rate_write_per_min = 1
        self.assertEqual(self.api("POST", "/facilities", raw=self.raw_b, body={"name": "w1"}).status_code, 201)
        self.assertProblem(self.api("POST", "/facilities", raw=self.raw_b, body={"name": "w2"}), 429)

    def test_last_used_is_throttled(self):
        self.api("GET", "/me", raw=self.raw_lab)
        self.key_lab.invalidate_recordset()
        first = self.key_lab.sudo().last_used
        self.assertTrue(first)
        self.api("GET", "/me", raw=self.raw_lab)
        self.key_lab.invalidate_recordset()
        # Menos de un minuto después: no se vuelve a escribir.
        self.assertEqual(self.key_lab.sudo().last_used, first)

    def test_cors_public(self):
        resp = self.url_open("/api/v1/public/catalogs/species", method="OPTIONS",
                             headers={"Origin": "https://cualquiera.example",
                                      "Access-Control-Request-Method": "GET"})
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("GET", resp.headers.get("Access-Control-Allow-Methods"))
        self.assertNotIn("Access-Control-Allow-Credentials", resp.headers)
        resp = self.url_open("/api/v1/public/catalogs/species", headers={"Origin": "https://x.example"})
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")

    def test_cors_private(self):
        origin = "https://erp.example.com"
        preflight = {"Origin": origin, "Access-Control-Request-Method": "GET"}
        resp = self.url_open("/api/v1/products", method="OPTIONS", headers=preflight)
        self.assertEqual(resp.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)
        self.key_a.sudo().allowed_origins = origin
        resp = self.url_open("/api/v1/products", method="OPTIONS", headers=preflight)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), origin)
        self.assertIn("Authorization", resp.headers.get("Access-Control-Allow-Headers"))
        resp = self.api("GET", "/me", raw=self.raw_a, headers={"Origin": origin})
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), origin)
        resp = self.api("GET", "/me", raw=self.raw_a, headers={"Origin": "https://malo.example"})
        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    def test_legacy_key_migration(self):
        raw = "LegacyKeyPlain_abcdefghijklmnopqrstuvwxyz0123456"
        self.env.cr.execute("""
            INSERT INTO shrimp_api_key (name, key, user_id, scope, active, uuid_ref, create_date, write_date)
            VALUES ('heredada', %s, %s, 'write', true, %s, now(), now()) RETURNING id
        """, (raw, self.u_a.id, str(uuid.uuid4())))
        key_id = self.env.cr.fetchone()[0]
        self.env.invalidate_all()
        migrate_legacy_keys(self.env)
        self.env.invalidate_all()
        key = self.env["shrimp.api.key"].sudo().browse(key_id)
        self.assertFalse(key.key)
        self.assertTrue(key.is_legacy)
        self.assertIn("products:write", key.scope_ids.mapped("code"))
        self.assertNotIn(raw, key.key_hash)
        resp = self.api("GET", "/me", raw=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        # La API vieja tenía el scope "admin": ya no actúa por terceros.
        self.assertProblem(self.api("GET", "/facilities", raw=raw + "x"), 401)

    def test_legacy_internal_key_is_disabled(self):
        raw = "InternalLegacy_abcdefghijklmnopqrstuvwxyz0123456"
        self.env.cr.execute("""
            INSERT INTO shrimp_api_key (name, key, user_id, scope, active, uuid_ref, create_date, write_date)
            VALUES ('interna', %s, %s, 'admin', true, %s, now(), now()) RETURNING id
        """, (raw, self.env.ref("base.user_admin").id, str(uuid.uuid4())))
        key_id = self.env.cr.fetchone()[0]
        self.env.invalidate_all()
        migrate_legacy_keys(self.env)
        self.env.invalidate_all()
        key = self.env["shrimp.api.key"].sudo().with_context(active_test=False).browse(key_id)
        self.assertFalse(key.active)
        self.assertProblem(self.api("GET", "/me", raw=raw), 401, "revoked-api-key")

    def test_old_plaintext_authenticate_disabled(self):
        # El método del marketplace ya no compara texto plano.
        self.assertFalse(self.env["shrimp.api.key"]._authenticate("lo-que-sea"))
        self.assertEqual(self.env["shrimp.api.key"]._authenticate(self.raw_a), self.key_a.sudo())

    def test_backend_rules(self):
        # Un usuario interno sin ser administrador no ve las claves de otros.
        internal = self.env["res.users"].create({
            "name": "Interno", "login": "interno.api",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])]})
        keys = self.env["shrimp.api.key"].with_user(internal).search([])
        self.assertFalse(keys)
        # El portal ve solo las suyas.
        mine = self.env["shrimp.api.key"].with_user(self.u_a).search([])
        self.assertEqual(mine.user_id, self.u_a)
