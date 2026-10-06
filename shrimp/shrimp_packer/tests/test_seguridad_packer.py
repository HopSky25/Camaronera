import json

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import (
    CLAVE, crear_socios, csrf_de, producto)


@tagged("post_install", "-at_install")
class TestSeguridadPacker(HttpCase):
    """A1 (lista de precios ajena, ids), A2, B2 (GET sin efectos), M4."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "pk")
        Socio = env["res.partner"]
        portal = env.ref("base.group_portal")
        cls.cam2 = Socio.create({
            "name": "Camaronera ajena pk", "is_company": True,
            "email": "cam2.pk@prueba.test", "vat_or_id": "0922200001001",
            "shrimp_user_type": "camaronera", "farm_razon_social": "C2 S.A.",
            "farm_representante": "R", "farm_telefono": "04-1", "farm_ubicacion": "Guayas"})
        cls.u_cam2 = env["res.users"].create({
            "name": cls.cam2.name, "login": "cam2.pk", "partner_id": cls.cam2.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        cls.emp = Socio.create({
            "name": "Empacadora pk", "is_company": True, "email": "emp.pk@prueba.test",
            "vat_or_id": "0922200002001", "shrimp_user_type": "empacadora"})
        cls.u_emp = env["res.users"].create({
            "name": cls.emp.name, "login": "emp.pk", "partner_id": cls.emp.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        talla = env.ref("shrimp_marketplace.size_entero_3040")
        cls.lista = env["shrimp.price.list"].create({
            "name": "Lista confidencial pk", "issuer_partner_id": cls.emp.id,
            "recipient_ids": [(6, 0, cls.s["cam"].ids)],
            "line_ids": [(0, 0, {"size_grade_id": talla.id, "uom": "kg",
                                 "quality": "ab", "price": 4.40})],
        })
        cls.lista.action_publish()
        cls.bono = env["shrimp.price.list.bonus"].create({
            "price_list_id": cls.lista.id, "name": "Bono pk", "amount": 1.0})

    def _rpc(self, url, params):
        r = self.url_open(url, data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                                "params": params, "id": 1}),
                          headers={"Content-Type": "application/json"})
        return r.json().get("result")

    # ---------------- A1: /marketplace/list-price ----------------
    def test_precio_de_lista_solo_si_la_lista_es_visible(self):
        params = {"price_list_ref": self.lista.uuid_ref, "presentation": "entero",
                  "size_grade_id": self.env.ref("shrimp_marketplace.size_entero_3040").id}
        self.authenticate("cam2.pk", CLAVE)
        self.assertEqual(self._rpc("/marketplace/list-price", params), {})
        # El id numérico de la lista ya no se acepta en ningún caso.
        self.assertEqual(self._rpc("/marketplace/list-price", dict(
            params, price_list_ref=str(self.lista.id))), {})
        self.authenticate(self.s["u_cam"].login, CLAVE)
        res = self._rpc("/marketplace/list-price", params)
        self.assertTrue(res and res.get("precio"))

    def test_producto_no_se_ata_a_una_lista_ajena(self):
        lote = producto(self.env, self.cam2, nombre="Lote pk",
                        etapa="shrimp_marketplace.shrimp_stage_engorde", publicado=False,
                        presentation="entero",
                        size_grade_id=self.env.ref("shrimp_marketplace.size_entero_3040").id,
                        uom_id=self.env.ref("shrimp_marketplace.uom_libra").id)
        with self.assertRaises(ValidationError):
            lote.write({"price_list_id": self.lista.id})
        propio = producto(self.env, self.s["cam"], nombre="Lote propio pk",
                          etapa="shrimp_marketplace.shrimp_stage_engorde", publicado=False,
                          presentation="entero",
                          size_grade_id=self.env.ref("shrimp_marketplace.size_entero_3040").id,
                          uom_id=self.env.ref("shrimp_marketplace.uom_libra").id)
        propio.write({"price_list_id": self.lista.id})
        self.assertAlmostEqual(propio.price, round(4.40 / 2.20462, 4), places=2)

    # ---------------- M4: empacadora pendiente ----------------
    def test_empacadora_pendiente_no_opera(self):
        self.emp.sudo().shrimp_account_state = "pending"
        self.authenticate("emp.pk", CLAVE)
        r = self.url_open("/marketplace/price-lists/new")
        self.assertEqual(r.status_code, 403)
        self.assertIn("revisión", r.text)
        self.emp.sudo().action_shrimp_approve_account()
        listas = self.env["shrimp.price.list"].search_count([])
        r = self.url_open("/marketplace/price-lists/new")
        self.assertEqual(r.status_code, 200)
        # B2: el GET ya no crea un borrador.
        self.assertEqual(self.env["shrimp.price.list"].search_count([]), listas)

    def test_alta_web_de_empacadora_queda_pendiente(self):
        token = csrf_de(self.url_open("/register/packer").text)
        self.url_open("/register/submit", data={
            "csrf_token": token, "shrimp_user_type": "empacadora",
            "name": "Empacadora nueva pk", "email": "nueva.pk@prueba.test",
            "password": "Clave-segura-123", "vat_or_id": "0922200009001"})
        socio = self.env["res.partner"].sudo().search([("email", "=", "nueva.pk@prueba.test")])
        self.assertTrue(socio)
        self.assertEqual(socio.shrimp_account_state, "pending")
        self.assertFalse(socio.shrimp_is_operational())

    # ---------------- A1: borrar bonificación por id ----------------
    def test_bonificacion_por_codigo(self):
        self.authenticate("emp.pk", CLAVE)
        pagina = self.url_open("/marketplace/price-lists/%s/edit" % self.lista.uuid_ref).text
        token = csrf_de(pagina)
        self.assertNotIn("bonuses/%d/delete" % self.bono.id, pagina)
        base = "/marketplace/price-lists/%s/bonuses/" % self.lista.uuid_ref
        self.url_open(base + "%d/delete" % self.bono.id, data={"csrf_token": token})
        self.assertTrue(self.bono.exists())
        self.url_open(base + "%s/delete" % self.bono.uuid_ref, data={"csrf_token": token})
        self.assertFalse(self.bono.exists())

    # ---------------- A2: XSS en "publicar" ----------------
    def test_sugerencia_de_precio_sin_innerhtml(self):
        self.authenticate(self.s["u_cam"].login, CLAVE)
        r = self.url_open("/marketplace/products/new")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("txt.innerHTML", r.text)
        self.assertIn('name="price_list_ref"', r.text)

    # ---------------- Reserva anticipada por el portal ----------------
    def test_reserva_con_piscina_y_destinatarias_por_codigo(self):
        from datetime import date, timedelta
        piscina = self.env["shrimp.partner.pond"].create({
            "partner_id": self.s["cam"].id, "name": "P1 pk", "pond_type": "earth",
            "capacity_mode": "volume", "manual_volume_m3": 100})
        ajena = self.env["shrimp.partner.pond"].create({
            "partner_id": self.cam2.id, "name": "P ajena pk", "pond_type": "earth",
            "capacity_mode": "volume", "manual_volume_m3": 100})
        self.authenticate(self.s["u_cam"].login, CLAVE)
        token = csrf_de(self.url_open("/marketplace/reservations/new").text)
        base = {"csrf_token": token, "expected_date": (date.today() + timedelta(days=20)).isoformat(),
                "expected_lb": "10000", "presentation": "entero",
                "size_grade_id": str(self.env.ref("shrimp_marketplace.size_entero_3040").id),
                "recipient_refs": self.emp.uuid_ref}
        # Con la piscina de otro (o con ids) no se graba.
        self.url_open("/marketplace/reservations/new", data=dict(base, pond_ref=ajena.uuid_ref))
        self.url_open("/marketplace/reservations/new", data=dict(base, pond_ref=str(piscina.id)))
        F = self.env["shrimp.harvest.forecast"]
        self.assertFalse(F.search([("farmer_partner_id", "=", self.s["cam"].id)]))
        self.url_open("/marketplace/reservations/new", data=dict(base, pond_ref=piscina.uuid_ref))
        reserva = F.search([("farmer_partner_id", "=", self.s["cam"].id)])
        self.assertEqual(len(reserva), 1)
        self.assertEqual(reserva.pond_id, piscina)
        self.assertEqual(reserva.recipient_ids, self.emp)
        self.assertEqual(reserva.state, "published")
