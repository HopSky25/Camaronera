import json
import uuid

from odoo.tests import HttpCase

from odoo.addons.shrimp_api.models.api_scope import SCOPE_CODES

FORBIDDEN_KEYS = {
    # Nunca deben salir por la API (ver serializers.py).
    "commission_cents", "rate_cents", "platform_amount", "verifier_amount", "margin_pct",
    "platform_rate_per_lb", "vat", "vat_or_id", "key", "key_hash", "secret_hash",
    "ver_bank_account_number", "ver_bank_holder_id", "bank_account", "gps_latitude",
    "gps_longitude", "latitude", "longitude", "password", "trace_token",
}


def walk_keys(data):
    """Todas las claves de un JSON anidado."""
    out = set()
    if isinstance(data, dict):
        for k, v in data.items():
            out.add(k)
            out |= walk_keys(v)
    elif isinstance(data, list):
        for v in data:
            out |= walk_keys(v)
    return out


def int_ids(data, path=""):
    """Rutas donde aparece un "id" entero (no debería haber ninguna)."""
    found = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "id" and isinstance(v, int) and not isinstance(v, bool):
                found.append(path + "." + k)
            found += int_ids(v, path + "." + k)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found += int_ids(v, "%s[%s]" % (path, i))
    return found


class ApiCase(HttpCase):
    """Montaje común: un laboratorio, dos camaroneras, una empacadora y un
    maquilador, cada uno con su usuario de portal y su clave de API.

    Todo se crea aquí (no se usan los datos de demo) para que la batería
    corra en una base sin demo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        P = env["res.partner"]
        portal = env.ref("base.group_portal")

        cls.lab = P.create({
            "name": "Laboratorio API", "is_company": True, "email": "lab.api@prueba.test",
            "vat_or_id": "0999900001001", "shrimp_user_type": "laboratorio",
            "lab_razon_social": "Laboratorio API S.A.", "lab_ubicacion": "Santa Elena",
            "phone": "+593 4 000 0001",
        })

        def farm(name, mail, ruc):
            return P.create({
                "name": name, "is_company": True, "email": mail, "vat_or_id": ruc,
                "shrimp_user_type": "camaronera", "farm_razon_social": name + " S.A.",
                "farm_representante": "Rep", "farm_telefono": "04-0000000",
                "farm_ubicacion": "Guayas", "phone": "+593 4 000 0002",
            })

        cls.farm_a = farm("Camaronera A API", "a.api@prueba.test", "0999900002001")
        cls.farm_b = farm("Camaronera B API", "b.api@prueba.test", "0999900003001")
        cls.packer = P.create({
            "name": "Empacadora API", "is_company": True, "email": "emp.api@prueba.test",
            "vat_or_id": "0999900004001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora API S.A.", "emp_planta_nombre": "Planta Durán",
        })

        def user(partner, login):
            return env["res.users"].create({
                "name": partner.name, "login": login, "partner_id": partner.id,
                "password": "Clave-api-123", "group_ids": [(6, 0, [portal.id])],
            })

        cls.u_lab = user(cls.lab, "lab.api")
        cls.u_a = user(cls.farm_a, "a.api")
        cls.u_b = user(cls.farm_b, "b.api")
        cls.u_packer = user(cls.packer, "emp.api")

        Key = env["shrimp.api.key"]
        cls.key_lab, cls.raw_lab = Key.api_create_key(cls.u_lab, "lab", SCOPE_CODES)
        cls.key_a, cls.raw_a = Key.api_create_key(cls.u_a, "a", SCOPE_CODES)
        cls.key_b, cls.raw_b = Key.api_create_key(cls.u_b, "b", SCOPE_CODES)
        cls.key_packer, cls.raw_packer = Key.api_create_key(cls.u_packer, "emp", SCOPE_CODES)

        cls.uom_millar = env.ref("shrimp_marketplace.uom_millar")
        cls.stage_pl12 = env.ref("shrimp_marketplace.shrimp_stage_pl12")
        cls.species = env.ref("shrimp_marketplace.shrimp_species_vannamei")
        cls.product = env["shrimp.product"].create({
            "name": "PL12 API", "seller_partner_id": cls.lab.id, "seller_role": "laboratorio",
            "initial_qty": 1000.0, "price": 2.5, "uom_id": cls.uom_millar.id,
            "stage_id": cls.stage_pl12.id, "species_id": cls.species.id, "state": "published",
        })

    # ------------------------------------------------------------------
    def url_open(self, *args, **kwargs):
        # Lo escrito por el test tiene que estar en la base antes de la
        # petición, y lo que escriba la petición tiene que verse después.
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def api(self, method, path, raw=None, body=None, headers=None, idem=None, data=None):
        h = dict(headers or {})
        if raw:
            h["Authorization"] = "Bearer " + raw
        if method == "POST" and raw and idem is not False:
            h.setdefault("Idempotency-Key", idem or str(uuid.uuid4()))
        if body is not None:
            data = json.dumps(body)
            h["Content-Type"] = "application/json"
        return self.url_open("/api/v1" + path, data=data, headers=h, method=method,
                             allow_redirects=False)

    def assertProblem(self, resp, status, slug=None):
        self.assertEqual(resp.status_code, status, resp.text)
        self.assertTrue(resp.headers.get("Content-Type", "").startswith("application/problem+json"),
                        resp.headers.get("Content-Type"))
        body = resp.json()
        for k in ("type", "title", "status", "detail", "request_id"):
            self.assertIn(k, body)
        self.assertEqual(body["status"], status)
        self.assertNotIn("Traceback", resp.text)
        if slug:
            self.assertTrue(body["type"].endswith("problem-" + slug), body["type"])
        return body
