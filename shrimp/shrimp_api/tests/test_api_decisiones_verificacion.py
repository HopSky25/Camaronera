"""Decisiones de la verificación por la API (lo que usa la app de campo):

* la verificadora acepta o declina la orden (:respond, firmada);
* rechazo por «Producto no presentado» (sin campo, con foto y hora de
  llegada) frente al rechazo por el informe;
* segunda opinión: el vendedor ve el plazo y las verificadoras elegibles y
  la pide (GET /second-opinion, POST :second-opinion, firmada);
* lo que cada uno puede hacer (can_verdict, can_respond, can_decline,
  can_request_second_opinion) y los plazos visibles;
* en una segunda opinión, quien cambia de verificadora es el vendedor.
"""
import base64
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_marketplace.tests.common import CLAVE, producto
from odoo.addons.shrimp_verification.tests.common_seguridad import montar_verificacion

from .common import ApiCase

PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5E"
       "rkJggg==")


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiDecisionesVerificacion(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "apidv")
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("shrimp_verification.contest_hours", "24")
        ICP.set_param("shrimp_verification.verifier_response_hours", "24")
        portal = cls.env.ref("base.group_portal")
        cls.apoyo = cls.env["res.partner"].create({
            "name": "Apoyo dv", "parent_id": cls.s["verif"].id,
            "shrimp_is_field_tech": True, "email": "apoyo.apidv@prueba.test"})
        cls.u_apoyo = cls.env["res.users"].create({
            "name": "Apoyo dv", "login": "apoyo.apidv", "partner_id": cls.apoyo.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        Key = cls.env["shrimp.api.key"]
        cls.raw_verif = Key.api_create_key(cls.s["u_verif"], "verif", SCOPE_CODES)[1]
        cls.raw_tec = Key.api_create_key(cls.s["u_tec"], "tec", SCOPE_CODES)[1]
        cls.raw_apoyo = Key.api_create_key(cls.u_apoyo, "apoyo", SCOPE_CODES)[1]
        cls.raw_vend = Key.api_create_key(cls.s["u_lab"], "vend", SCOPE_CODES)[1]
        cls.raw_comp = Key.api_create_key(cls.s["u_cam"], "comp", SCOPE_CODES)[1]

    def nueva(self, nombre):
        lote = producto(self.env, self.s["lab"], nombre=nombre)
        return lote.start_verified_purchase(self.s["cam"], 10.0, self.s["verif"], fee=50.0)

    def en_campo(self, v, cumple=True):
        v.sudo().action_assign_technician(self.s["tec"])
        v.sudo().action_add_support_technician(self.apoyo)
        v.sudo().action_start_field()
        v.sudo().write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                        "larvae_avg_size_mg": 5.0,
                        "larvae_health_status": "good" if cumple else "rejected"})
        return v

    def get(self, v, raw):
        r = self.api("GET", "/verifications/%s" % v.uuid_ref, raw=raw)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ------------------------------------------------------------------
    def test_openapi_firma_las_rutas_nuevas(self):
        paths = self.url_open("/api/v1/openapi.json").json()["paths"]
        for ruta in ("/api/v1/verifications/{id}:respond", "/api/v1/verifications/{id}:second-opinion",
                     "/api/v1/verifications/{id}:support-technicians"):
            self.assertTrue(paths[ruta]["post"].get("x-signed"), ruta)
        self.assertIn("get", paths["/api/v1/verifications/{id}/second-opinion"])

    def test_aceptar_y_declinar_la_orden(self):
        v = self.nueva("Larva responde apidv")["verification"]
        d = self.get(v, self.raw_verif)
        self.assertEqual(d["verifier_response"], "pending")
        self.assertTrue(d["can_respond"])
        self.assertTrue(d["can_decline"])
        self.assertTrue(d["verifier_response_deadline"])
        self.assertTrue(d["deadline_assign"])
        self.assertTrue(d["deadline_verdict"])
        url = "/verifications/%s:respond" % v.uuid_ref
        # Un técnico no responde por la empresa.
        v.sudo().action_assign_technician(self.s["tec"])
        v.sudo().write({"verifier_response": "pending"})
        self.assertProblem(self.api("POST", url, raw=self.raw_tec, body={"decision": "accept"}), 403)
        r = self.api("POST", url, raw=self.raw_verif, body={"decision": "accept"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["verifier_response"], "accepted")
        self.assertFalse(r.json()["can_respond"])
        self.assertTrue(r.json()["can_decline"], "aceptada, todavía puede declinar dentro del plazo")
        # Declinar exige motivo; con motivo la orden vuelve sin técnico.
        self.assertProblem(self.api("POST", url, raw=self.raw_verif, body={"decision": "decline"}), 422)
        r = self.api("POST", url, raw=self.raw_verif,
                     body={"decision": "decline", "reason": "Sin capacidad esa semana"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["verifier_response"], "declined")
        self.assertEqual(data["verifier_decline_reason"], "Sin capacidad esa semana")
        self.assertEqual(data["deadline_overdue"], "declined")
        self.assertFalse(data["can_decline"])
        self.assertIsNone(data["technician"])
        # El comprador ve el motivo, no los botones de la verificadora.
        c = self.get(v, self.raw_comp)
        self.assertEqual(c["verifier_decline_reason"], "Sin capacidad esa semana")
        self.assertFalse(c["can_respond"])

    def test_apoyo_carga_pero_no_firma(self):
        v = self.en_campo(self.nueva("Larva apoyo apidv")["verification"])
        apoyo = self.get(v, self.raw_apoyo)
        self.assertEqual(apoyo["my_role"], "support_technician")
        self.assertFalse(apoyo["can_verdict"])
        self.assertIsInstance(apoyo["warnings"], list)
        self.assertEqual(apoyo["contest_hours"], 24)
        self.assertTrue(self.get(v, self.raw_tec)["can_verdict"])
        self.assertTrue(self.get(v, self.raw_verif)["can_verdict"])
        self.assertIsNone(self.get(v, self.raw_vend)["contest_hours"], "solo la verificadora")
        # Tampoco el «no presentado».
        self.assertProblem(self.api("POST", "/verifications/%s:verdict" % v.uuid_ref, raw=self.raw_apoyo,
                                    body={"verdict": "reject", "reject_reason": "not_presented",
                                          "notes": "No vino", "arrival_at": "2026-01-01T10:00:00Z",
                                          "photos": [{"data": PNG}]}), 403)

    def test_no_presentado_sin_campo(self):
        r = self.nueva("Larva no presentada apidv")
        v, tx = r["verification"], r["transaction"]
        v.sudo().action_assign_technician(self.s["tec"])
        url = "/verifications/%s:verdict" % v.uuid_ref
        llegada = (fields.Datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Sin campo no se rechaza «por el informe».
        self.assertProblem(self.api("POST", url, raw=self.raw_tec,
                                    body={"verdict": "reject", "reject_reason": "field",
                                          "notes": "No cumple"}), 422)
        # Sin foto ni hora: 422 y nada cambia.
        self.assertProblem(self.api("POST", url, raw=self.raw_tec,
                                    body={"verdict": "reject", "reject_reason": "not_presented",
                                          "notes": "No llegó el camión"}), 422)
        self.assertProblem(self.api("POST", url, raw=self.raw_tec,
                                    body={"verdict": "reject", "reject_reason": "not_presented",
                                          "notes": "No llegó el camión", "photos": [{"data": PNG}]}), 422)
        self.assertEqual(v.state, "assigned")
        r = self.api("POST", url, raw=self.raw_tec,
                     body={"verdict": "reject", "reject_reason": "not_presented",
                           "notes": "Esperé dos horas y el camión no llegó", "arrival_at": llegada,
                           "photos": [{"data": PNG, "name": "porton.png"}]})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["state"], "rejected")
        self.assertEqual(data["rejection_reason"], "not_presented")
        self.assertEqual(data["not_presented_arrival"][:16], llegada[:16])
        self.assertIsNone(data["contest_state"], "sin segunda opinión")
        self.assertEqual(data["photo_count"], 1)
        self.assertEqual(tx.state, "cancel")
        vend = self.get(v, self.raw_vend)
        self.assertFalse(vend["can_request_second_opinion"])
        so = self.api("GET", "/verifications/%s/second-opinion" % v.uuid_ref, raw=self.raw_vend).json()
        self.assertFalse(so["can_request"])
        self.assertIn("no presentado", so["problem"].lower())
        self.assertEqual(so["options"], [])

    def test_rechazo_y_segunda_opinion(self):
        r = self.nueva("Larva impugnada apidv")
        v, tx = self.en_campo(r["verification"], cumple=False), r["transaction"]
        res = self.api("POST", "/verifications/%s:verdict" % v.uuid_ref, raw=self.raw_tec,
                       body={"verdict": "reject", "notes": "Estado sanitario inaceptable"})
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["rejection_reason"], "field")
        self.assertEqual(data["contest_state"], "open")
        self.assertTrue(data["contest_deadline"])
        self.assertEqual(tx.state, "pending_verification", "la compra queda retenida")
        self.assertTrue(self.get(v, self.raw_vend)["can_request_second_opinion"])
        self.assertFalse(self.get(v, self.raw_comp)["can_request_second_opinion"])

        url = "/verifications/%s/second-opinion" % v.uuid_ref
        so = self.api("GET", url, raw=self.raw_vend).json()
        self.assertTrue(so["can_request"])
        self.assertEqual(so["contest_state"], "open")
        elegibles = [o["verifier"]["id"] for o in so["options"]]
        self.assertIn(self.s["verif2"].uuid_ref, elegibles)
        self.assertNotIn(self.s["verif"].uuid_ref, elegibles, "nunca la que rechazó")
        self.assertTrue(all(o["fee"] for o in so["options"]))
        comp = self.api("GET", url, raw=self.raw_comp).json()
        self.assertFalse(comp["can_request"])
        self.assertEqual(comp["options"], [])
        self.assertProblem(self.api("GET", url, raw=self.raw_tec), 403)

        pedir = "/verifications/%s:second-opinion" % v.uuid_ref
        self.assertProblem(self.api("POST", pedir, raw=self.raw_comp,
                                    body={"verifier": self.s["verif2"].uuid_ref}), 403)
        self.assertProblem(self.api("POST", pedir, raw=self.raw_vend,
                                    body={"verifier": self.s["verif"].uuid_ref}), 422)
        self.assertProblem(self.api("POST", pedir, raw=self.raw_vend, body={}), 422)
        res = self.api("POST", pedir, raw=self.raw_vend, body={"verifier": self.s["verif2"].uuid_ref})
        self.assertEqual(res.status_code, 201, res.text)
        segunda = res.json()
        self.assertEqual(segunda["second_opinion_of"], v.uuid_ref)
        self.assertEqual(segunda["verifier"]["id"], self.s["verif2"].uuid_ref)
        self.assertEqual(segunda["state"], "received")
        primera = self.get(v, self.raw_vend)
        self.assertTrue(primera["superseded"])
        self.assertEqual(primera["contest_state"], "contested")
        self.assertEqual(primera["second_opinion"], segunda["id"])
        self.assertFalse(primera["can_request_second_opinion"])
        # Una segunda vez no: ya está impugnado.
        self.assertProblem(self.api("POST", pedir, raw=self.raw_vend,
                                    body={"verifier": self.s["verif2"].uuid_ref}), 422)

        # La segunda verificadora declina: cambia de verificadora el vendedor.
        ver2 = self.env["shrimp.verification"].sudo().search([("second_opinion_of_id", "=", v.id)])
        ver2.action_verifier_decline("Conflicto de interés")
        tx_url = "/transactions/%s" % tx.uuid_ref
        t = self.api("GET", tx_url, raw=self.raw_vend).json()
        self.assertTrue(t["cancellation"]["can_switch_verifier"])
        self.assertFalse(self.api("GET", tx_url, raw=self.raw_comp).json()["cancellation"]["can_switch_verifier"])
        self.assertProblem(self.api("POST", tx_url + ":switch-verifier", raw=self.raw_comp,
                                    body={"verifier": self.s["verif"].uuid_ref}), 403)
        res = self.api("POST", tx_url + ":switch-verifier", raw=self.raw_vend,
                       body={"verifier": self.s["verif"].uuid_ref})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(ver2.verifier_partner_id, self.s["verif"])

    def test_foto_invalida_en_no_presentado(self):
        v = self.nueva("Larva foto mala apidv")["verification"]
        v.sudo().action_assign_technician(self.s["tec"])
        self.assertProblem(self.api(
            "POST", "/verifications/%s:verdict" % v.uuid_ref, raw=self.raw_tec,
            body={"verdict": "reject", "reject_reason": "not_presented", "notes": "No vino",
                  "arrival_at": "2026-01-01T10:00:00Z",
                  "photos": [{"data": base64.b64encode(b"no es una imagen").decode()}]}), 422)
