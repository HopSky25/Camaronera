"""«Deshacer mi decisión» por la API: mismas reglas que el portal, scopes de
las operaciones de firma, y webhook *_reverted para la otra parte."""

from datetime import date, timedelta

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_verification.tests.common_seguridad import (
    abrir_ronda, montar_verificacion)

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiDeshacer(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.copacker = env["res.partner"].create({
            "name": "Maquila deshacer", "is_company": True, "email": "maq.dsh@prueba.test",
            "vat_or_id": "0999900015001", "shrimp_user_type": "maquilador",
            "pack_razon_social": "Maquila deshacer S.A.", "pack_ubicacion": "Durán",
            "pack_codigo_establecimiento": "AS-998", "pack_en_directorio": True,
        })
        cls.u_copacker = env["res.users"].create({
            "name": cls.copacker.name, "login": "maq.dsh", "partner_id": cls.copacker.id,
            "password": "Clave-api-123",
            "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
        Key = env["shrimp.api.key"]
        cls.key_copacker, cls.raw_copacker = Key.api_create_key(cls.u_copacker, "maq", SCOPE_CODES)
        cls.grade = env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)

    def _sub(self, partner, user, event):
        return self.env["shrimp.webhook.subscription"].create({
            "name": event, "partner_id": partner.id, "user_id": user.id,
            "url": "https://hooks.example.com/%s" % partner.id, "event_types": event,
            "secret": "s"})

    def _entregas(self, sub, event):
        return self.env["shrimp.webhook.delivery"].search([
            ("subscription_id", "=", sub.id), ("event_type", "=", event)])

    # ------------------------------------------------------------------
    def test_verification_acceptance_revert(self):
        s, v = montar_verificacion(self.env, "apid")
        abrir_ronda(v)
        Key = self.env["shrimp.api.key"]
        _k, raw_cam = Key.api_create_key(s["u_cam"], "cam", SCOPE_CODES)
        _k, raw_lab = Key.api_create_key(s["u_lab"], "lab", SCOPE_CODES)
        _k, raw_ver = Key.api_create_key(s["u_verif"], "ver", SCOPE_CODES)
        _k, raw_ro = Key.api_create_key(s["u_cam"], "ro", ["verifications:read"])
        ref = v.uuid_ref
        resp = self.api("POST", "/verifications/%s/acceptance" % ref, raw=raw_cam,
                        body={"decision": "reject", "reason": "por error"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["acceptance_state"], "waiting")
        mia = [a for a in data["acceptances"] if a["role"] == "buyer"][0]
        self.assertTrue(mia["can_revert"])
        # El verificador no deshace por las partes; el vendedor no tiene nada que deshacer.
        self.assertProblem(self.api("POST", "/verifications/%s/acceptance:revert" % ref,
                                    raw=raw_ver, body={}), 403)
        self.assertProblem(self.api("POST", "/verifications/%s/acceptance:revert" % ref,
                                    raw=raw_lab, body={}), 422)
        # Sin scope de escritura, no.
        self.assertProblem(self.api("POST", "/verifications/%s/acceptance:revert" % ref,
                                    raw=raw_ro, body={}), 403)
        sub = self._sub(s["lab"], s["u_lab"], "verification.acceptance_reverted")
        resp = self.api("POST", "/verifications/%s/acceptance:revert" % ref, raw=raw_cam,
                        body={"reason": "me equivoqué"})
        self.assertEqual(resp.status_code, 200, resp.text)
        mia = [a for a in resp.json()["acceptances"] if a["role"] == "buyer"][0]
        self.assertEqual(mia["decision"], "pending")
        self.assertFalse(mia["can_revert"])
        self.assertEqual(len(self._entregas(sub, "verification.acceptance_reverted")), 1)
        # Cerrada la ronda, ya no.
        for raw in (raw_cam, raw_lab):
            self.assertEqual(self.api("POST", "/verifications/%s/acceptance" % ref, raw=raw,
                                      body={"decision": "accept"}).status_code, 200)
        self.assertProblem(self.api("POST", "/verifications/%s/acceptance:revert" % ref,
                                    raw=raw_cam, body={}), 422)

    def test_copack_revert_signature(self):
        today = date.today()
        req = self.env["shrimp.copack.request"].create({
            "client_partner_id": self.farm_a.id, "quantity_lb": 20000, "presentation": "entero",
            "needed_from": today, "needed_to": today + timedelta(days=5),
            "copacker_partner_id": self.copacker.id})
        req.action_publish()
        offer = self.env["shrimp.copack.offer"].create({
            "request_id": req.id, "copacker_partner_id": self.copacker.id, "rate_per_lb": 0.18,
            "capacity_lb": 20000, "available_from": today,
            "available_to": today + timedelta(days=4)})
        order = offer.action_accept(actor=self.farm_a)
        order.write({"received_lb": 20000})
        order.action_register_reception()
        order.write({"packed_lb": 19950})
        order.action_register_packing()
        oid = order.uuid_ref
        resp = self.api("POST", "/copack/orders/%s:sign" % oid, raw=self.raw_a,
                        body={"decision": "rejected", "reason": "No cuadra"})
        self.assertEqual(resp.json()["acceptance_state"], "disputed")
        self.assertProblem(self.api("POST", "/copack/orders/%s:revert-signature" % oid,
                                    raw=self.raw_copacker, body={}), 422)
        self.assertProblem(self.api("POST", "/copack/orders/%s:revert-signature" % oid,
                                    raw=self.raw_b, body={}), 404)
        sub = self._sub(self.copacker, self.u_copacker, "copack.signature_reverted")
        resp = self.api("POST", "/copack/orders/%s:revert-signature" % oid, raw=self.raw_a,
                        body={"reason": "Firmé el acta equivocada"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["acceptance_state"], "open")
        firma = [f for f in resp.json()["signatures"] if f["role"] == "client"][0]
        self.assertEqual(firma["decision"], "pending")
        self.assertEqual(len(self._entregas(sub, "copack.signature_reverted")), 1)
        # Alias con barra para clientes que no admiten los dos puntos.
        for raw in (self.raw_a, self.raw_copacker):
            self.api("POST", "/copack/orders/%s:sign" % oid, raw=raw, body={"decision": "accepted"})
        self.assertProblem(self.api("POST", "/copack/orders/%s/revert-signature" % oid,
                                    raw=self.raw_a, body={}), 422)

    def test_harvest_confirmation_revert(self):
        env = self.env
        pond = env["shrimp.partner.pond"].create({"partner_id": self.farm_a.id, "name": "P-dsh"})
        self.packer.reserva_acepta = True
        esperada = date.today() + timedelta(days=20)
        fc = env["shrimp.harvest.forecast"].create({
            "farmer_partner_id": self.farm_a.id, "expected_date": esperada,
            "expected_lb": 30000.0, "presentation": "entero", "size_grade_id": self.grade.id,
            "pond_id": pond.id, "recipient_ids": [(6, 0, self.packer.ids)]})
        fc.action_publish(actor=self.farm_a)
        cm = env["shrimp.harvest.commitment"].create({
            "forecast_id": fc.id, "packer_partner_id": self.packer.id, "committed_lb": 30000.0,
            "price_mode": "fijo", "price_per_lb": 2.4, "step_delta_per_lb": 0.1,
            "valid_until": esperada - timedelta(days=1)})
        cm.action_accept(actor=self.farm_a)
        fc.action_registrar_cosecha(actual_lb=5000.0, actual_size_grade_id=self.grade.id,
                                    actual_date=esperada, actor=self.farm_a)
        self.assertEqual(cm.state, "to_confirm")
        cid = cm.uuid_ref
        resp = self.api("POST", "/harvest/commitments/%s/confirmation" % cid, raw=self.raw_packer,
                        body={"decision": "rejected", "reason": "Talla chica"})
        self.assertEqual(resp.json()["state"], "released")
        self.assertProblem(self.api("POST", "/harvest/commitments/%s/confirmation:revert" % cid,
                                    raw=self.raw_b, body={}), 404)
        self.assertProblem(self.api("POST", "/harvest/commitments/%s/confirmation:revert" % cid,
                                    raw=self.raw_a, body={}), 422)
        sub = self._sub(self.farm_a, self.u_a, "harvest.confirmation_reverted")
        sub_req = self._sub(self.farm_a, self.u_a, "harvest.confirmation_required")
        resp = self.api("POST", "/harvest/commitments/%s/confirmation:revert" % cid,
                        raw=self.raw_packer, body={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "to_confirm")
        self.assertEqual(len(self._entregas(sub, "harvest.confirmation_reverted")), 1)
        # Volver a «pendiente de confirmar» no es una confirmación nueva.
        self.assertFalse(self._entregas(sub_req, "harvest.confirmation_required"))
