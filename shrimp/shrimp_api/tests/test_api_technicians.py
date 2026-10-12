"""GET /me/technicians: los técnicos de campo que admite :assign."""

from odoo.tests import tagged

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiTechnicians(ApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        P = env["res.partner"]
        cls.verifier = P.create({
            "name": "Verificadora Técnicos", "is_company": True, "email": "ver.tec@prueba.test",
            "vat_or_id": "0999900007001", "shrimp_user_type": "verificador",
            "ver_bank_account_number": "2200112244", "ver_bank_holder_id": "0912345679",
        })
        cargo = env["shrimp.tech.role"].create({"name": "Supervisor de prueba"})
        cls.tech1 = P.create({"name": "Zoila Técnica", "parent_id": cls.verifier.id,
                              "shrimp_is_field_tech": True, "tech_role_id": cargo.id})
        cls.tech2 = P.create({"name": "Andrés Técnico", "parent_id": cls.verifier.id,
                              "shrimp_is_field_tech": True})
        cls.tech_baja = P.create({"name": "Técnico de baja", "parent_id": cls.verifier.id,
                                  "shrimp_is_field_tech": True, "active": False})
        cls.secretaria = P.create({"name": "Contacto sin campo", "parent_id": cls.verifier.id})
        portal = env.ref("base.group_portal")

        def user(partner, login):
            return env["res.users"].create({
                "name": partner.name, "login": login, "partner_id": partner.id,
                "password": "Clave-api-123", "group_ids": [(6, 0, [portal.id])]})

        cls.u_verifier = user(cls.verifier, "ver.tec")
        cls.u_tech1 = user(cls.tech1, "tec1.tec")
        Key = env["shrimp.api.key"]
        cls.key_verifier, cls.raw_verifier = Key.api_create_key(cls.u_verifier, "ver", SCOPE_CODES)
        cls.key_tech1, cls.raw_tech1 = Key.api_create_key(cls.u_tech1, "tec", ["verifications:read"])

    def test_lista_tecnicos_de_la_empresa(self):
        resp = self.api("GET", "/me/technicians", raw=self.raw_verifier)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual([t["name"] for t in data], ["Andrés Técnico", "Zoila Técnica"])
        zoila = data[1]
        self.assertEqual(set(zoila), {"id", "name", "role", "active"})
        self.assertEqual(zoila["id"], self.tech1.uuid_ref)
        self.assertEqual(zoila["role"], "Supervisor de prueba")
        self.assertTrue(zoila["active"])
        self.assertIsNone(data[0]["role"])
        # Ni el contacto sin marcar ni el técnico archivado.
        self.assertNotIn(self.secretaria.uuid_ref, [t["id"] for t in data])
        self.assertNotIn(self.tech_baja.uuid_ref, [t["id"] for t in data])

    def test_tecnico_ve_a_sus_companeros(self):
        # El titular de los perfiles del técnico es su empresa.
        resp = self.api("GET", "/me/technicians", raw=self.raw_tech1)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()["data"]), 2)

    def test_sin_perfil_verificador_403(self):
        # Una camaronera (sin perfil verificador) recibe 403 forbidden.
        self.assertProblem(self.api("GET", "/me/technicians", raw=self.raw_a), 403, "forbidden")
        # Y sin el scope de verificaciones, 403 insufficient-scope antes de llegar al handler.
        _key, raw = self.env["shrimp.api.key"].api_create_key(self.u_a, "solo catálogo", ["catalog:read"])
        self.assertProblem(self.api("GET", "/me/technicians", raw=raw), 403, "insufficient-scope")
