"""Maquila por la API con las reglas de la auditoría de procesos (D-14, D-34,
D-47): las mismas del portal porque viven en el modelo."""

from datetime import date, timedelta

from freezegun import freeze_time

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase

HOY = date(2026, 10, 19)


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiCopackDecisiones(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        portal = env.ref("base.group_portal")
        cls.copacker = env["res.partner"].create({
            "name": "Maquila decisiones", "is_company": True, "email": "maq.dec@prueba.test",
            "vat_or_id": "0999900015001", "shrimp_user_type": "maquilador",
            "pack_razon_social": "Maquila decisiones S.A.", "pack_ubicacion": "Durán",
            "pack_codigo_establecimiento": "AS-915", "pack_en_directorio": True,
        })
        u = env["res.users"].create({
            "name": cls.copacker.name, "login": "maq.dec", "partner_id": cls.copacker.id,
            "password": "Clave-api-123", "group_ids": [(6, 0, [portal.id])]})
        cls.key_copacker, cls.raw_copacker = env["shrimp.api.key"].api_create_key(
            u, "maq", SCOPE_CODES)

    def _request(self, lb=20000):
        resp = self.api("POST", "/copack/requests", raw=self.raw_a, body={
            "quantity_lb": lb, "presentation": "entero", "needed_from": str(HOY),
            "needed_to": str(HOY + timedelta(days=20))})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["expires_on"], str(HOY + timedelta(days=15)))
        return resp.json()["id"]

    def _offer(self, req_id, lb=20000):
        resp = self.api("POST", "/copack/requests/%s/offers" % req_id, raw=self.raw_copacker, body={
            "rate_per_lb": 0.18, "capacity_lb": lb, "available_from": str(HOY),
            "available_to": str(HOY + timedelta(days=10))})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    @freeze_time("2026-10-19 15:00:00")
    def test_reject_offer_and_partial_award(self):
        req_id = self._request()
        offer_id = self._offer(req_id)
        self.assertProblem(self.api("POST", "/copack/offers/%s:reject" % offer_id,
                                    raw=self.raw_copacker, body={}), 403)
        resp = self.api("POST", "/copack/offers/%s:reject" % offer_id, raw=self.raw_a,
                        body={"reason": "Muy cara"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual((resp.json()["state"], resp.json()["closed_reason"]), ("rejected", "client"))
        # La planta la mejora (más barata y menos libras) y el cliente adjudica: parcial.
        resp = self.api("POST", "/copack/requests/%s/offers" % req_id, raw=self.raw_copacker, body={
            "rate_per_lb": 0.15, "capacity_lb": 12000, "available_from": str(HOY),
            "available_to": str(HOY + timedelta(days=10))})
        self.assertEqual(resp.json()["state"], "sent", resp.text)
        resp = self.api("POST", "/copack/offers/%s:accept" % offer_id, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["agreed_qty_lb"], 12000)
        req = self.api("GET", "/copack/requests/%s" % req_id, raw=self.raw_a).json()
        self.assertEqual(len(req["remainder_requests"]), 1)
        child = self.api("GET", "/copack/requests/%s" % req["remainder_requests"][0],
                         raw=self.raw_a).json()
        self.assertEqual((child["quantity_lb"], child["state"]), (8000, "published"))
        self.assertEqual(child["parent_request"], req_id)

    @freeze_time("2026-10-19 15:00:00")
    def test_copacker_cancel_rules(self):
        req_id = self._request()
        oid = self.api("POST", "/copack/offers/%s:accept" % self._offer(req_id),
                       raw=self.raw_a).json()["id"]
        # Antes de recibir: con motivo, y la solicitud se reabre.
        self.assertProblem(self.api("POST", "/copack/orders/%s:cancel" % oid,
                                    raw=self.raw_copacker, body={}), 422)
        resp = self.api("POST", "/copack/orders/%s:cancel" % oid, raw=self.raw_copacker,
                        body={"reason": "Sin cupo en cámara"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual((resp.json()["state"], resp.json()["cancelled_by"]),
                         ("cancelled", "copacker"))
        req = self.api("GET", "/copack/requests/%s" % req_id, raw=self.raw_a).json()
        self.assertEqual(req["state"], "published")

    @freeze_time("2026-10-19 15:00:00")
    def test_return_act_with_shrimp_at_plant(self):
        req_id = self._request()
        oid = self.api("POST", "/copack/offers/%s:accept" % self._offer(req_id),
                       raw=self.raw_a).json()["id"]
        self.api("POST", "/copack/orders/%s:reception" % oid, raw=self.raw_copacker,
                 body={"received_lb": 20000})
        self.assertProblem(self.api("POST", "/copack/orders/%s:cancel" % oid,
                                    raw=self.raw_copacker, body={"reason": "x"}), 409)
        resp = self.api("POST", "/copack/orders/%s:return" % oid, raw=self.raw_copacker,
                        body={"reason": "Avería", "returned_lb": 20000})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["pending_cancellation"]["kind"], "return")
        self.assertProblem(self.api("POST", "/copack/orders/%s:return-sign" % oid,
                                    raw=self.raw_copacker, body={"decision": "accepted"}), 403)
        resp = self.api("POST", "/copack/orders/%s:return-sign" % oid, raw=self.raw_a,
                        body={"decision": "accepted"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "cancelled")

    @freeze_time("2026-10-19 15:00:00")
    def test_request_cancel_same_rule_as_portal(self):
        req_id = self._request()
        oid = self.api("POST", "/copack/offers/%s:accept" % self._offer(req_id),
                       raw=self.raw_a).json()["id"]
        # Adjudicada y sin empacar: se cancela con su orden (antes la API decía 409).
        resp = self.api("POST", "/copack/requests/%s:cancel" % req_id, raw=self.raw_a, body={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "cancelled")
        self.assertEqual(self.api("GET", "/copack/orders/%s" % oid, raw=self.raw_a).json()["state"],
                         "cancelled")
        # Empacada: no.
        req2 = self._request()
        oid2 = self.api("POST", "/copack/offers/%s:accept" % self._offer(req2),
                        raw=self.raw_a).json()["id"]
        self.api("POST", "/copack/orders/%s:reception" % oid2, raw=self.raw_copacker,
                 body={"received_lb": 20000})
        resp = self.api("POST", "/copack/orders/%s:packing" % oid2, raw=self.raw_copacker,
                        body={"packed_lb": 19900})
        self.assertFalse(resp.json()["billable"], "a la firma no es facturable")
        self.assertTrue(resp.json()["acceptance_deadline"])
        self.assertProblem(self.api("POST", "/copack/requests/%s:cancel" % req2,
                                    raw=self.raw_a, body={}), 409)
