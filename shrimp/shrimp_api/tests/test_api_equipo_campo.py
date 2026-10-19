"""Equipo de campo por la API (aditivo): técnicos de apoyo, rol
«support_technician», lectura por la regla de registro y veredicto solo del
responsable o la empresa."""

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES
from odoo.addons.shrimp_marketplace.tests.common import CLAVE
from odoo.addons.shrimp_verification.tests.common_seguridad import montar_verificacion

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiEquipoCampo(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "apieq")
        portal = cls.env.ref("base.group_portal")
        cls.apoyo = cls.env["res.partner"].create({
            "name": "Apoyo API", "parent_id": cls.s["verif"].id,
            "shrimp_is_field_tech": True, "email": "apoyo.apieq@prueba.test"})
        cls.u_apoyo = cls.env["res.users"].create({
            "name": "Apoyo API", "login": "apoyo.apieq", "partner_id": cls.apoyo.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        Key = cls.env["shrimp.api.key"]
        _k, cls.raw_verif = Key.api_create_key(cls.s["u_verif"], "verif", SCOPE_CODES)
        _k, cls.raw_apoyo = Key.api_create_key(cls.u_apoyo, "apoyo", SCOPE_CODES)
        cls.v.sudo().action_assign_technician(cls.s["tec"])

    def test_sumar_apoyo_y_permisos(self):
        ref = self.v.uuid_ref
        # El apoyo todavía no la ve.
        self.assertProblem(self.api("GET", "/verifications/%s" % ref, raw=self.raw_apoyo), 404)
        r = self.api("POST", "/verifications/%s:support-technicians" % ref, raw=self.raw_verif,
                     body={"add": [{"technician": self.apoyo.uuid_ref}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([t["id"] for t in r.json()["support_technicians"]], [self.apoyo.uuid_ref])
        # Un técnico ajeno: 422.
        self.assertProblem(self.api("POST", "/verifications/%s:support-technicians" % ref,
                                    raw=self.raw_verif,
                                    body={"add": [{"technician": self.s["tec_ajeno"].uuid_ref}]}), 422)
        # El apoyo la ve, con su rol, y aparece en su lista de técnico.
        data = self.api("GET", "/verifications/%s" % ref, raw=self.raw_apoyo).json()
        self.assertEqual(data["my_role"], "support_technician")
        lista = self.api("GET", "/verifications?role=technician", raw=self.raw_apoyo).json()
        self.assertIn(ref, [x["id"] for x in lista["data"]])
        # Carga datos e inicia, pero no firma.
        self.assertEqual(self.api("POST", "/verifications/%s:start" % ref, raw=self.raw_apoyo).status_code, 200)
        r = self.api("PATCH", "/verifications/%s/report" % ref, raw=self.raw_apoyo,
                     body={"larvae_qty_verified": 9.0})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertProblem(self.api("POST", "/verifications/%s:verdict" % ref, raw=self.raw_apoyo,
                                    body={"verdict": "approve"}), 403)
        # Quitarlo: deja de verla.
        r = self.api("POST", "/verifications/%s:support-technicians" % ref, raw=self.raw_verif,
                     body={"remove": [{"technician": self.apoyo.uuid_ref}]})
        self.assertEqual(r.json()["support_technicians"], [])
        self.assertProblem(self.api("GET", "/verifications/%s" % ref, raw=self.raw_apoyo), 404)
