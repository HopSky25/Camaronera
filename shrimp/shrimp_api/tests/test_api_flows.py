"""Flujos de punta a punta por la API: maquila, reserva anticipada, despacho y
verificación. Comprueban que la API llama a los mismos métodos de negocio que
el portal y que cada papel solo puede hacer lo suyo."""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase, FORBIDDEN_KEYS, int_ids, walk_keys


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiFlows(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        P = env["res.partner"]
        portal = env.ref("base.group_portal")
        cls.copacker = P.create({
            "name": "Maquila API", "is_company": True, "email": "maq.api@prueba.test",
            "vat_or_id": "0999900005001", "shrimp_user_type": "maquilador",
            "pack_razon_social": "Maquila API S.A.", "pack_ubicacion": "Durán",
            "pack_codigo_establecimiento": "AS-999", "pack_en_directorio": True,
        })
        cls.verifier = P.create({
            "name": "Verificadora API", "is_company": True, "email": "ver.api@prueba.test",
            "vat_or_id": "0999900006001", "shrimp_user_type": "verificador",
            "ver_bank_account_number": "2200112233", "ver_bank_holder_id": "0912345678",
        })
        cls.tech = P.create({
            "name": "Técnico API", "email": "tec.api@prueba.test", "parent_id": cls.verifier.id,
            "shrimp_is_field_tech": True,
        })

        def user(partner, login):
            return env["res.users"].create({
                "name": partner.name, "login": login, "partner_id": partner.id,
                "password": "Clave-api-123", "group_ids": [(6, 0, [portal.id])]})

        Key = env["shrimp.api.key"]
        cls.u_copacker = user(cls.copacker, "maq.api")
        cls.u_verifier = user(cls.verifier, "ver.api")
        cls.u_tech = user(cls.tech, "tec.api")
        cls.key_copacker, cls.raw_copacker = Key.api_create_key(cls.u_copacker, "maq", SCOPE_CODES)
        cls.key_verifier, cls.raw_verifier = Key.api_create_key(cls.u_verifier, "ver", SCOPE_CODES)
        cls.key_tech, cls.raw_tech = Key.api_create_key(cls.u_tech, "tec", SCOPE_CODES)

        cls.grade = env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)
        cls.engorde = env.ref("shrimp_marketplace.shrimp_stage_engorde")
        cls.libra = env.ref("shrimp_marketplace.uom_libra")

    # ------------------------------------------------------------------
    def test_copack_flow(self):
        today = fields.Date.today()
        resp = self.api("POST", "/copack/requests", raw=self.raw_a, body={
            "quantity_lb": 20000, "presentation": "entero", "needed_from": str(today),
            "needed_to": str(today + timedelta(days=5)), "copacker": self.copacker.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        req_id = resp.json()["id"]
        # El maquilador la ve en su bandeja; la otra camaronera no.
        inbox = self.api("GET", "/copack/requests", raw=self.raw_copacker).json()["data"]
        self.assertIn(req_id, [r["id"] for r in inbox])
        self.assertProblem(self.api("GET", "/copack/requests/%s" % req_id, raw=self.raw_b), 404)
        # Solo un maquilador oferta.
        offer_body = {"rate_per_lb": 0.18, "capacity_lb": 20000, "available_from": str(today),
                      "available_to": str(today + timedelta(days=4))}
        self.assertProblem(self.api("POST", "/copack/requests/%s/offers" % req_id, raw=self.raw_b,
                                    body=offer_body), 403)
        resp = self.api("POST", "/copack/requests/%s/offers" % req_id, raw=self.raw_copacker, body=offer_body)
        self.assertEqual(resp.status_code, 201, resp.text)
        offer_id = resp.json()["id"]
        # El maquilador no se autoadjudica.
        self.assertProblem(self.api("POST", "/copack/offers/%s:accept" % offer_id, raw=self.raw_copacker), 403)
        resp = self.api("POST", "/copack/offers/%s:accept" % offer_id, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        order = resp.json()
        self.assertEqual(order["state"], "confirmed")
        self.assertNotIn("platform_amount", order)
        oid = order["id"]
        # La recepción la registra el maquilador, no el cliente.
        self.assertProblem(self.api("POST", "/copack/orders/%s:reception" % oid, raw=self.raw_a,
                                    body={"received_lb": 20000}), 403)
        self.assertEqual(self.api("POST", "/copack/orders/%s:reception" % oid, raw=self.raw_copacker,
                                  body={"received_lb": 20000, "supplies_received": True}).status_code, 200)
        resp = self.api("POST", "/copack/orders/%s:packing" % oid, raw=self.raw_copacker,
                        body={"packed_lb": 19950, "boxes": 997})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["acceptance_state"], "open")
        for raw in (self.raw_a, self.raw_copacker):
            resp = self.api("POST", "/copack/orders/%s:sign" % oid, raw=raw, body={"decision": "accepted"})
            self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "signed")
        resp = self.api("POST", "/copack/orders/%s:close" % oid, raw=self.raw_a)
        self.assertEqual(resp.json()["state"], "closed")
        self.assertProblem(self.api("GET", "/copack/orders/%s" % oid, raw=self.raw_b), 404)
        self.assertFalse(FORBIDDEN_KEYS & walk_keys(resp.json()))

    def test_copack_tariff_recipients_restricted(self):
        resp = self.api("POST", "/copack/tariffs", raw=self.raw_copacker, body={
            "name": "Tarifa 2026", "recipients": [self.farm_b.uuid_ref],
            "lines": [{"presentation": "entero", "pack_format": "Master 20 kg", "rate_per_lb": 0.2}]})
        # farm_b nunca le pidió nada: no puede ser destinataria.
        self.assertProblem(resp, 422)

    def test_harvest_flow(self):
        pond = self.env["shrimp.partner.pond"].create({"partner_id": self.farm_a.id, "name": "P7"})
        self.packer.reserva_acepta = True
        expected = fields.Date.today() + timedelta(days=20)
        resp = self.api("POST", "/harvest/forecasts", raw=self.raw_a, body={
            "expected_date": str(expected), "expected_lb": 40000, "presentation": "entero",
            "size_grade": self.grade.uuid_ref, "pond": pond.uuid_ref,
            "recipients": [self.packer.uuid_ref]})
        self.assertEqual(resp.status_code, 201, resp.text)
        fc = resp.json()
        self.assertEqual(fc["state"], "published")
        # Una camaronera ajena no la ve; la empacadora destinataria sí, sin ver destinatarios.
        self.assertProblem(self.api("GET", "/harvest/forecasts/%s" % fc["id"], raw=self.raw_b), 404)
        seen = self.api("GET", "/harvest/forecasts/%s" % fc["id"], raw=self.raw_packer)
        self.assertEqual(seen.status_code, 200, seen.text)
        self.assertNotIn("recipients", seen.json())
        resp = self.api("POST", "/harvest/forecasts/%s/commitments" % fc["id"], raw=self.raw_packer, body={
            "committed_lb": 30000, "price_mode": "fijo", "price_per_lb": 2.4,
            "step_delta_per_lb": 0.1, "valid_until": str(expected - timedelta(days=1))})
        self.assertEqual(resp.status_code, 201, resp.text)
        cm_id = resp.json()["id"]
        # La empacadora no se acepta a sí misma.
        self.assertProblem(self.api("POST", "/harvest/commitments/%s:accept" % cm_id, raw=self.raw_packer), 403)
        sub = self.env["shrimp.webhook.subscription"].create({
            "name": "emp", "partner_id": self.packer.id, "user_id": self.u_packer.id,
            "url": "https://hooks.example.com/emp", "event_types": "commitment.accepted", "secret": "s"})
        resp = self.api("POST", "/harvest/commitments/%s:accept" % cm_id, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "accepted")
        self.assertTrue(self.env["shrimp.webhook.delivery"].search([("subscription_id", "=", sub.id)]))
        resp = self.api("POST", "/harvest/commitments/%s:desist" % cm_id, raw=self.raw_packer, body={})
        self.assertProblem(resp, 422)

    def _verified_purchase(self):
        """Compra de camarón adulto con verificación, creada como lo hace el portal."""
        product = self.env["shrimp.product"].create({
            "name": "Engorde API", "seller_partner_id": self.farm_a.id, "seller_role": "camaronera",
            "initial_qty": 10000, "price": 2.2, "uom_id": self.libra.id, "stage_id": self.engorde.id,
            "presentation": "entero", "size_grade_id": self.grade.id, "state": "published"})
        tx = self.env["shrimp.transaction"].create({
            "transaction_type": "camaronera_to_buyer", "product_id": product.id,
            "seller_partner_id": self.farm_a.id, "buyer_partner_id": self.packer.id,
            "state": "pending_verification", "needs_verification": True, "transaction_qty": 10000,
            "price_unit": 2.2, "amount_total": 22000, "desired_qty": 10000,
            "desired_date": fields.Date.today()})
        verification = self.env["shrimp.verification"].create({
            "transaction_id": tx.id, "verifier_partner_id": self.verifier.id, "fee": 300})
        return tx, verification

    def test_dispatch_roles(self):
        tx, verification = self._verified_purchase()
        dispatch = tx.dispatch_id
        self.assertTrue(dispatch)
        eta = (fields.Datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Solo el vendedor fija la cita.
        self.assertProblem(self.api("PATCH", "/dispatches/%s" % dispatch.uuid_ref, raw=self.raw_packer,
                                    body={"eta": eta}), 403)
        resp = self.api("PATCH", "/dispatches/%s" % dispatch.uuid_ref, raw=self.raw_a,
                        body={"eta": eta, "carrier_name": "Transportes Vera", "vehicle_plate": "gba-1234"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["vehicle_plate"], "GBA-1234")
        self.assertEqual(resp.json()["eta"], eta)
        # La empacadora lo ve; un tercero no.
        self.assertEqual(self.api("GET", "/dispatches/%s" % dispatch.uuid_ref, raw=self.raw_packer).status_code, 200)
        self.assertProblem(self.api("GET", "/dispatches/%s" % dispatch.uuid_ref, raw=self.raw_b), 404)
        # La llegada no la ponen ni vendedor ni empacadora.
        self.assertProblem(self.api("POST", "/dispatches/%s/arrival" % dispatch.uuid_ref, raw=self.raw_a,
                                    body={}), 403)
        self.assertProblem(self.api("POST", "/dispatches/%s/arrival" % dispatch.uuid_ref, raw=self.raw_packer,
                                    body={}), 403)
        resp = self.api("POST", "/dispatches/%s/arrival" % dispatch.uuid_ref, raw=self.raw_verifier, body={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["actual_arrival"])

    def test_verification_field_app_and_acceptance(self):
        tx, verification = self._verified_purchase()
        ref = verification.uuid_ref
        # El técnico todavía no está asignado: no la ve.
        self.assertProblem(self.api("GET", "/verifications/%s" % ref, raw=self.raw_tech), 404)
        resp = self.api("POST", "/verifications/%s:assign" % ref, raw=self.raw_verifier,
                        body={"technician": self.tech.uuid_ref})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.api("GET", "/verifications/%s" % ref, raw=self.raw_tech).status_code, 200)
        # Las partes no escriben el informe.
        self.assertProblem(self.api("PATCH", "/verifications/%s/report" % ref, raw=self.raw_packer,
                                    body={"weight_plant_lb": 1}), 403)
        self.assertEqual(self.api("POST", "/verifications/%s:start" % ref, raw=self.raw_tech).status_code, 200)
        report = {
            "weight_sent_lb": 10000, "weight_plant_lb": 10100, "trash_lb": 50, "presentation": "entero",
            "metabisulfite_ppm": 40, "taste_result": "good", "gps_latitude": -2.2, "gps_longitude": -79.9,
            "lines": [{"quality_class": "a", "size_code": self.grade.name, "weight_lb": 6000},
                      {"quality_class": "b", "size_code": "40/50", "weight_lb": 600}],
            "counts": [{"value": 35}]}
        resp = self.api("PATCH", "/verifications/%s/report" % ref, raw=self.raw_tech, body=report)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(len(data["classification"]["lines"]), 2)
        self.assertNotIn("gps_latitude", walk_keys(data))
        self.assertFalse(FORBIDDEN_KEYS & walk_keys(data))
        resp = self.api("POST", "/verifications/%s:verdict" % ref, raw=self.raw_tech, body={"verdict": "approve"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "approved")
        self.assertEqual(resp.json()["acceptance_state"], "waiting")
        # El verificador no decide por las partes.
        self.assertProblem(self.api("POST", "/verifications/%s/acceptance" % ref, raw=self.raw_verifier,
                                    body={"decision": "accept"}), 403)
        for raw in (self.raw_packer, self.raw_a):
            resp = self.api("POST", "/verifications/%s/acceptance" % ref, raw=raw, body={"decision": "accept"})
            self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["acceptance_state"], "closed")
        # Para comprador/vendedor no salen datos bancarios ni el reparto del honorario.
        detail = self.api("GET", "/verifications/%s" % ref, raw=self.raw_packer).json()
        self.assertFalse(FORBIDDEN_KEYS & walk_keys(detail))
        self.assertIsNone(detail["technician"])
        self.assertFalse(int_ids(detail))
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "confirmed")
        # El ranking de proveedores ya cuenta este lote.
        ranking = self.api("GET", "/suppliers/ranking", raw=self.raw_packer).json()
        self.assertEqual(ranking["rows"][0]["supplier"]["id"], self.farm_a.uuid_ref)
