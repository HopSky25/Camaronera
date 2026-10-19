import json
import re

from odoo.exceptions import ValidationError
from odoo.addons.shrimp_user_registry.tests.legal_utils import signup_post
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
        # D-15: una lista publicada no se edita; la bonificación se prueba en
        # un borrador de la misma empacadora.
        cls.borrador = env["shrimp.price.list"].create({
            "name": "Borrador pk", "issuer_partner_id": cls.emp.id,
            "recipient_ids": [(6, 0, cls.s["cam"].ids)]})
        cls.bono = env["shrimp.price.list.bonus"].create({
            "price_list_id": cls.borrador.id, "name": "Bono pk", "amount": 1.0})

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
        self.url_open("/register/submit", data=signup_post(
            self.env, ["empacadora"], "nueva.pk@prueba.test", name="Empacadora nueva pk",
            csrf_token=token))
        socio = self.env["res.partner"].sudo().search([("email", "=", "nueva.pk@prueba.test")])
        self.assertTrue(socio)
        self.assertEqual(socio.shrimp_account_state, "pending")
        self.assertFalse(socio.shrimp_is_operational())

    def test_alta_empacadora_sin_planta_ni_mercados(self):
        """shrimp_packer 19.0.1.14.1: el bloque ya no pide planta ni mercados;
        la capacidad es referencial y opcional."""
        html = self.url_open("/register/packer").text
        bloque = re.search(r'id="section_empacadora".*?<div id="shrimp_prof_sections_end"', html, re.S).group(0)
        self.assertNotIn('name="emp_planta_nombre"', bloque)
        self.assertNotIn("emp_mercado_", bloque)
        self.assertIn('name="emp_capacidad_lb_dia"', bloque)
        self.assertIn("Capacidad referencial", bloque)
        self.assertIn("Solo como referencia para los productores", bloque)
        self.assertNotRegex(bloque, r'name="emp_capacidad_lb_dia"[^>]*data-req')
        # Alta sin planta, sin mercados y sin capacidad: entra y queda pendiente.
        token = csrf_de(html)
        datos = signup_post(self.env, ["empacadora"], "sinplanta.pk@prueba.test",
                            name="Exportadora sin planta pk", csrf_token=token)
        r = self.url_open("/register/submit", data=datos, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio = self.env["res.partner"].sudo().search([("email", "=", "sinplanta.pk@prueba.test")])
        self.assertTrue(socio)
        self.assertEqual(socio.shrimp_account_state, "pending")
        self.assertFalse(socio.emp_planta_nombre)
        self.assertFalse(socio.shrimp_capacity_value)
        self.assertFalse(socio.emp_mercado_asia or socio.emp_mercado_europa
                         or socio.emp_mercado_norteamerica or socio.emp_mercado_local)
        # Con capacidad referencial: se guarda en lb/día.
        datos = signup_post(self.env, ["empacadora"], "concap.pk@prueba.test",
                            name="Exportadora con capacidad pk", csrf_token=token,
                            emp_capacidad_lb_dia="50000")
        r = self.url_open("/register/submit", data=datos, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303), r.text[:500])
        socio2 = self.env["res.partner"].sudo().search([("email", "=", "concap.pk@prueba.test")])
        self.assertEqual((socio2.shrimp_capacity_value, socio2.shrimp_capacity_unit), (50000.0, "lb_day"))
        self.assertEqual(socio2.shrimp_account_state, "pending")
        # Aprobada, su ficha pública usa el nombre de la empresa como planta y
        # no muestra la sección de mercados.
        socio.action_shrimp_approve_account()
        r = self.url_open("/marketplace/packers/%s" % socio.uuid_ref)
        self.assertEqual(r.status_code, 200, r.url)
        self.assertIn("Planta de proceso", r.text)
        self.assertIn(socio.commercial_partner_id.name, r.text)
        self.assertNotIn("Mercados a los que exporta", r.text)
        self.assertNotIn("No ha declarado sus mercados", r.text)

    # ---------------- A1: borrar bonificación por id ----------------
    def test_bonificacion_por_codigo(self):
        self.authenticate("emp.pk", CLAVE)
        pagina = self.url_open("/marketplace/price-lists/%s/edit" % self.borrador.uuid_ref).text
        token = csrf_de(pagina)
        self.assertNotIn("bonuses/%d/delete" % self.bono.id, pagina)
        base = "/marketplace/price-lists/%s/bonuses/" % self.borrador.uuid_ref
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
