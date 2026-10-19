"""Hueco #6: la API respeta la aprobación de la cuenta y los documentos
legales MAYORES, igual que el portal."""
import re

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase

FAC = {"name": "Planta gate", "facility_type": "farm"}


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiCuentaLegal(ApiCase):

    def _set_state(self, partner, state):
        partner.sudo().write({"shrimp_account_state": state})
        self.env.flush_all()

    # ------------------------------------------------------------ pendiente
    def test_cuenta_pendiente_solo_lectura(self):
        self._set_state(self.packer, "pending")
        self.assertEqual(self.api("GET", "/facilities", raw=self.raw_packer).status_code, 200)
        body = self.assertProblem(self.api("POST", "/facilities", raw=self.raw_packer, body=FAC),
                                  403, "account-pending")
        self.assertEqual(body["code"], "account_pending")
        self.assertEqual(body["role"], "empacadora")
        # La clave sigue activa (pendiente ≠ rechazada).
        self.assertTrue(self.key_packer.active)
        me = self.api("GET", "/me", raw=self.raw_packer).json()
        self.assertEqual(me["account_state"], "pending")

    def test_pendiente_no_crea_claves(self):
        self._set_state(self.packer, "pending")
        with self.assertRaises(ValidationError):
            self.env["shrimp.api.key"].api_create_key(self.u_packer, "otra", ["products:read"])

    def test_portal_no_ofrece_crear_claves_a_pendiente(self):
        self._set_state(self.packer, "pending")
        self.authenticate("emp.api", "Clave-api-123")
        page = self.url_open("/my/api-keys")
        self.assertEqual(page.status_code, 200)
        self.assertIn("api_account_block", page.text)
        self.assertNotIn('action="/my/api-keys/new"', page.text)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        antes = self.env["shrimp.api.key"].search_count([("user_id", "=", self.u_packer.id)])
        resp = self.url_open("/my/api-keys/new", data={
            "csrf_token": token, "name": "forzada", "scopes": ["products:read"], "expiry_days": "30"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotRegex(resp.text, r"trz_[0-9a-f]{8}_[A-Za-z0-9_\-]{20,}")
        self.assertEqual(self.env["shrimp.api.key"].search_count(
            [("user_id", "=", self.u_packer.id)]), antes)

    # ------------------------------------------------------------ rechazada
    def test_rechazar_desactiva_claves_y_bloquea(self):
        self._set_state(self.packer, "rejected")
        self.assertFalse(self.key_packer.active, "rechazar la cuenta desactiva sus claves")
        self.assertProblem(self.api("GET", "/facilities", raw=self.raw_packer), 401, "revoked-api-key")
        # Aunque alguien reactive la clave a mano, la puerta la sigue frenando.
        self.key_packer.sudo().write({"active": True})
        body = self.assertProblem(self.api("GET", "/facilities", raw=self.raw_packer),
                                  403, "account-rejected")
        self.assertEqual(body["code"], "account_rejected")
        self.assertEqual(self.api("GET", "/me", raw=self.raw_packer).status_code, 200)
        # Las demás cuentas no se tocan.
        self.assertTrue(self.key_a.active)
        self.assertEqual(self.api("GET", "/facilities", raw=self.raw_a).status_code, 200)

    def test_perfil_rechazado_por_linea_de_perfil(self):
        linea = self.packer.shrimp_role_ids.filtered(lambda r: r.role == "empacadora")
        linea.action_reject(reason="Documentos incompletos (prueba)")
        self.env.flush_all()
        self.assertEqual(self.packer.shrimp_account_state, "rejected")
        self.assertFalse(self.key_packer.active)

    def test_migracion_desactiva_claves_de_rechazadas(self):
        # Estado escrito por SQL (como estaba antes de este cambio): la clave sigue activa.
        self.env.cr.execute("UPDATE res_partner SET shrimp_account_state='rejected' WHERE id=%s",
                            (self.farm_b.id,))
        self.env.invalidate_all()
        self.assertTrue(self.key_b.active)
        keys = self.env["shrimp.api.key"]._shrimp_disable_keys_of_rejected()
        self.assertIn(self.key_b, keys)
        self.assertNotIn(self.key_a, keys)
        self.assertFalse(self.key_b.active)

    # ------------------------------------------------------------ legal
    def test_documentos_mayores_pendientes(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        docs = self.env["shrimp.legal.document"].sudo()._shrimp_pending(self.u_a)
        self.assertTrue(docs["major"], "los datos de instalación publican privacidad y términos MAYORES")
        body = self.assertProblem(self.api("GET", "/facilities", raw=self.raw_a),
                                  403, "legal-acceptance-required")
        self.assertEqual(body["code"], "legal_acceptance_required")
        self.assertTrue(body["accept_url"].endswith("/my/legal/accept"))
        self.assertTrue(body["pending_documents"])
        self.assertProblem(self.api("POST", "/facilities", raw=self.raw_a, body=FAC),
                           403, "legal-acceptance-required")
        me = self.api("GET", "/me", raw=self.raw_a)
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["legal"]["acceptance_required"])
        # Acepta todo lo pendiente -> vuelve a operar.
        pend = self.env["shrimp.legal.document"].sudo()._shrimp_pending(self.u_a)
        versiones = [st["version"] for st in pend["major"] + pend["minor"]]
        self.env["shrimp.legal.acceptance"].sudo()._shrimp_record(
            self.u_a, self.env["shrimp.legal.document.version"].browse([v.id for v in versiones]),
            "signup", generate_receipt=False)
        self.assertEqual(self.api("GET", "/facilities", raw=self.raw_a).status_code, 200)
        self.assertFalse(self.api("GET", "/me", raw=self.raw_a).json()["legal"]["acceptance_required"])

    # ------------------------------------------------------------ suspensión
    def test_vendedor_suspendido_no_publica(self):
        product = self.env["shrimp.product"].create({
            "name": "PL borrador", "seller_partner_id": self.lab.id, "seller_role": "laboratorio",
            "initial_qty": 100.0, "price": 2.0, "uom_id": self.uom_millar.id,
            "stage_id": self.stage_pl12.id, "species_id": self.species.id, "state": "draft",
        })
        self.env["shrimp.seller.suspension"].sudo()._shrimp_open(self.lab, 5)
        self.env.flush_all()
        body = self.assertProblem(self.api("POST", "/products/%s:publish" % product.uuid_ref,
                                           raw=self.raw_lab), 403, "seller-suspended")
        self.assertEqual(body["code"], "seller_suspended")
        self.assertEqual(product.state, "draft")
        # Las claves no se desactivan: sigue pudiendo leer y operar lo demás.
        self.assertTrue(self.key_lab.active)
        self.assertEqual(self.api("GET", "/products", raw=self.raw_lab).status_code, 200)

    def test_cuenta_aprobada_sin_cambios(self):
        self.assertEqual(self.api("POST", "/facilities", raw=self.raw_a, body=FAC).status_code, 201)
        me = self.api("GET", "/me", raw=self.raw_a).json()
        self.assertEqual(me["account_state"], "approved")
        self.assertFalse(me["legal"]["acceptance_required"])

    def test_openapi_documenta_los_403(self):
        doc = self.url_open("/api/v1/openapi.json").json()
        self.assertIn("account-pending", doc["paths"]["/api/v1/facilities"]["post"]["responses"]["403"]["description"])
        self.assertTrue(SCOPE_CODES)
