"""Modo de verificación por la API: compra con modo, informe declarado cargado
y presentado por una parte, confirmación de la otra, webhook
verification.declared_submitted y etiquetas en la trazabilidad pública."""

import base64

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_marketplace.tests.common import PDF_MIN
from odoo.addons.shrimp_verification.tests.common_seguridad import montar_verificacion

from .common import ApiCase

INFORME = {"larvae_qty_verified": 10.0, "larvae_survival_rate": 91.0,
           "larvae_avg_size_mg": 5.0, "larvae_health_status": "good"}


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiVerificacionDeclarada(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v_plat = montar_verificacion(cls.env, "apidcl")
        Key = cls.env["shrimp.api.key"]
        _k, cls.raw_cam = Key.api_create_key(cls.s["u_cam"], "cam", SCOPE_CODES)
        _k, cls.raw_lab = Key.api_create_key(cls.s["u_lab"], "lab", SCOPE_CODES)
        _k, cls.raw_ro = Key.api_create_key(cls.s["u_cam"], "ro", ["verifications:read"])
        cls.lote = cls.env["shrimp.product"].create({
            "name": "Larva API declarada", "seller_partner_id": cls.s["lab"].id,
            "seller_role": "laboratorio", "initial_qty": 100.0, "price": 10.0,
            "uom_id": cls.env.ref("shrimp_marketplace.uom_millar").id,
            "stage_id": cls.env.ref("shrimp_marketplace.shrimp_stage_pl12").id,
            "state": "published"})

    def test_flujo_declarado_por_api(self):
        # Sin modo, o con un modo que no existe: 422.
        self.assertProblem(self.api("POST", "/verifications", raw=self.raw_cam,
                                    body={"product": self.lote.uuid_ref, "qty": 2.0}), 422)
        self.assertProblem(self.api("POST", "/verifications", raw=self.raw_cam, body={
            "product": self.lote.uuid_ref, "qty": 2.0, "verification_mode": "na"}), 422)
        # Solo lectura: 403.
        self.assertProblem(self.api("POST", "/verifications", raw=self.raw_ro, body={
            "product": self.lote.uuid_ref, "qty": 2.0, "verification_mode": "declared"}), 403)
        resp = self.api("POST", "/verifications", raw=self.raw_cam, body={
            "product": self.lote.uuid_ref, "qty": 4.0, "verification_mode": "declared",
            "declared_source": "external", "external_verifier_name": "Externa API SA",
            "external_verifier_vat": "0990000003001"})
        self.assertIn(resp.status_code, (200, 201), resp.text)
        data = resp.json()
        self.assertEqual(data["verification_mode"], "declared")
        self.assertEqual(data["state"], "declared_draft")
        self.assertIsNone(data["verifier"])
        self.assertEqual(data["declared"]["external_verifier_name"], "Externa API SA")
        ref = data["id"]
        v = self.env["shrimp.verification"].search([("uuid_ref", "=", ref)])
        self.assertEqual(v.fee, 0.0)
        tx = self.api("GET", "/transactions/%s" % v.transaction_id.uuid_ref, raw=self.raw_cam).json()
        self.assertEqual(tx["verification_mode"], "declared")

        # El vendedor carga el informe (con el PDF) y lo presenta.
        sub = self.env["shrimp.webhook.subscription"].create({
            "name": "dcl", "partner_id": self.s["cam"].id, "user_id": self.s["u_cam"].id,
            "url": "https://hooks.example.com/dcl", "event_types": "verification.declared_submitted",
            "secret": "s"})
        resp = self.api("PATCH", "/verifications/%s/declared-report" % ref, raw=self.raw_lab,
                        body=dict(INFORME, report_pdf=base64.b64encode(PDF_MIN).decode(),
                                  report_pdf_filename="informe.pdf", notes="Conforme"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["declared"]["has_report_pdf"])
        # Un PDF que no es PDF: 422.
        self.assertProblem(self.api("PATCH", "/verifications/%s/declared-report" % ref,
                                    raw=self.raw_lab,
                                    body={"report_pdf": base64.b64encode(b"hola").decode()}), 422)
        resp = self.api("POST", "/verifications/%s:declare" % ref, raw=self.raw_lab, body={})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["state"], "declared")
        self.assertEqual(data["acceptance_state"], "waiting")
        self.assertEqual(data["declared"]["declarant_role"], "seller")
        self.assertIn("Verificación declarada por", data["verification_label"])
        self.assertEqual(len(self.env["shrimp.webhook.delivery"].search([
            ("subscription_id", "=", sub.id),
            ("event_type", "=", "verification.declared_submitted")])), 1)
        # Ya presentado no se edita.
        self.assertProblem(self.api("PATCH", "/verifications/%s/declared-report" % ref,
                                    raw=self.raw_lab, body={"larvae_survival_rate": 50.0}), 422)
        # El comprador confirma.
        resp = self.api("POST", "/verifications/%s/acceptance" % ref, raw=self.raw_cam,
                        body={"decision": "accept"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["acceptance_state"], "closed")

        # Trazabilidad pública: rotulada como declarada, distinta de la plataforma.
        tx_rec = v.transaction_id
        pub = self.api("GET", "/public/traceability/%s" % tx_rec._ensure_trace_token()).json()
        ver = pub.get("verification") or pub.get("data", {}).get("verification")
        self.assertTrue(ver, pub)
        self.assertEqual(ver["mode"], "declared")
        self.assertFalse(ver["accredited"])
        self.assertIsNone(ver["verifier"])
        self.assertIn("Verificación declarada por", ver["label"])
        pagina = self.url_open("/t/%s" % tx_rec._ensure_trace_token()).text
        self.assertIn("Verificación declarada por las partes", pagina)
        self.assertIn("no la realizó una verificadora acreditada", pagina)

    def test_declarante_retira_por_api(self):
        resp = self.api("POST", "/verifications", raw=self.raw_cam, body={
            "product": self.lote.uuid_ref, "qty": 2.0, "verification_mode": "declared",
            "declared_source": "self"})
        ref = resp.json()["id"]
        self.api("PATCH", "/verifications/%s/declared-report" % ref, raw=self.raw_cam, body=INFORME)
        resp = self.api("POST", "/verifications/%s:declare" % ref, raw=self.raw_cam, body={})
        self.assertEqual(resp.json()["state"], "declared", resp.text)
        resp = self.api("POST", "/verifications/%s/acceptance:revert" % ref, raw=self.raw_cam,
                        body={"reason": "corrijo"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "declared_draft")
        self.assertEqual(resp.json()["acceptance_state"], "na")

    def test_modo_plataforma_por_api_y_listado(self):
        self.assertProblem(self.api("POST", "/verifications", raw=self.raw_cam, body={
            "product": self.lote.uuid_ref, "qty": 2.0, "verification_mode": "platform"}), 422)
        resp = self.api("POST", "/verifications", raw=self.raw_cam, body={
            "product": self.lote.uuid_ref, "qty": 2.0, "verification_mode": "platform",
            "verifier": self.s["verif"].uuid_ref})
        self.assertIn(resp.status_code, (200, 201), resp.text)
        data = resp.json()
        self.assertEqual(data["verification_mode"], "platform")
        self.assertEqual(data["verifier"]["id"], self.s["verif"].uuid_ref)
        self.assertTrue(data["fee"])
        # La plataforma no admite el informe declarado.
        self.assertProblem(self.api("PATCH", "/verifications/%s/declared-report" % data["id"],
                                    raw=self.raw_cam, body=INFORME), 409)
        lista = self.api("GET", "/verifications?verification_mode=platform", raw=self.raw_cam).json()
        modos = {item["verification_mode"] for item in lista["data"]}
        self.assertEqual(modos, {"platform"})
