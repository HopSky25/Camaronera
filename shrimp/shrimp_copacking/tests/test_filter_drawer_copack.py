"""Cajón de filtros en el empaque: órdenes del maquilador y directorio.

/copacker/orders: búsqueda + «Situación» (el ?f= de siempre) y «Cliente»
(solo con más de uno); tabla que en móvil pasa a tarjetas.
/marketplace/copacking (cliente): búsqueda + servicio, habilitación, ubicación.
"""
import json
from datetime import date, timedelta

from odoo.tests import HttpCase, tagged

CLAVE = "Clave-de-prueba-123"


@tagged("post_install", "-at_install")
class TestFilterDrawerCopack(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Socio = env["res.partner"]
        portal = env.ref("base.group_portal")

        def maquilador(nombre, ruc, **extra):
            vals = {"name": nombre, "is_company": True, "email": "%s@prueba.test" % ruc,
                    "vat_or_id": ruc, "shrimp_user_type": "maquilador",
                    "pack_razon_social": nombre + " S.A.",
                    "pack_codigo_establecimiento": ruc[-5:], "pack_capacidad_lb_semana": 200000,
                    "pack_lote_minimo_lb": 5000}
            vals.update(extra)
            return Socio.create(vals)

        def camaronera(nombre, ruc):
            return Socio.create({
                "name": nombre, "is_company": True, "email": "%s@prueba.test" % ruc,
                "vat_or_id": ruc, "shrimp_user_type": "camaronera",
                "farm_razon_social": nombre + " S.A.", "farm_representante": "Rep",
                "farm_telefono": "04-0000000", "farm_ubicacion": "Guayas"})

        def usuario(socio, login):
            return env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})

        cls.maq = maquilador("FDCP Planta Uno", "0955800001001", pack_desde_entero=0.15,
                             pack_ubicacion="Durán")
        cls.maq2 = maquilador("FDCP Planta Dos", "0955800002001", pack_desde_cola=0.22,
                              pack_ubicacion="Machala")
        cls.cli = camaronera("FDCP Cliente Uno", "0955800003001")
        cls.cli2 = camaronera("FDCP Cliente Dos", "0955800004001")
        usuario(cls.maq, "maq.fdcp")
        usuario(cls.cli, "cli.fdcp")

        cls.ordenes = env["shrimp.copack.order"]
        for cliente in (cls.cli, cls.cli2):
            sol = env["shrimp.copack.request"].create({
                "client_partner_id": cliente.id, "quantity_lb": 40000,
                "presentation": "entero", "needed_from": date.today(),
                "needed_to": date.today() + timedelta(days=7),
                "copacker_partner_id": cls.maq.id,
            })
            sol.action_publish()
            oferta = env["shrimp.copack.offer"].create({
                "request_id": sol.id, "copacker_partner_id": cls.maq.id,
                "rate_per_lb": 0.18, "capacity_lb": 40000,
                "available_from": date.today(),
                "available_to": date.today() + timedelta(days=6),
            })
            cls.ordenes |= oferta.action_accept(actor=cliente)
        # Sin dominio propio el sitio de empaque no redirige: se sirve aquí.
        sitio = env["website"].sudo()._shrimp_copacker_site()
        if sitio:
            sitio.domain = False

    def _count(self, url):
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200, url)
        return json.loads(r.text)["count"]

    def test_ordenes_maquilador_cajon(self):
        self.authenticate("maq.fdcp", CLAVE)
        r = self.url_open("/copacker/orders")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn('id="sFilterDrawer"', html)
        self.assertIn('id="sf_sec_situacion"', html)
        self.assertIn('id="sf_sec_cliente"', html)
        self.assertIn("s-stack-table", html)
        self.assertIn('data-label="Cliente"', html)
        for orden in self.ordenes:
            self.assertIn(orden.name, html)

    def test_ordenes_maquilador_conteo_y_etiquetas(self):
        self.authenticate("maq.fdcp", CLAVE)
        self.assertEqual(self._count("/copacker/orders/count"), 2)
        self.assertEqual(self._count("/copacker/orders/count?cliente=%s" % self.cli.uuid_ref), 1)
        self.assertEqual(self._count("/copacker/orders/count?f=cerradas"), 0)
        self.assertEqual(self._count("/copacker/orders/count?q=FDCP Cliente Dos"), 1)
        # Un id numérico de cliente no vale (solo el código, y de mis órdenes).
        self.assertEqual(self._count("/copacker/orders/count?cliente=%d" % self.cli.id), 0)
        html = self.url_open("/copacker/orders?q=OEM&f=firmar&cliente=%s" % self.cli.uuid_ref).text
        self.assertIn("Actas por firmar", html)
        self.assertIn("Cliente: FDCP Cliente Uno", html)
        # Quitar «Situación» conserva cliente y búsqueda.
        self.assertIn('href="/copacker/orders?cliente=%s&amp;q=OEM#listado"' % self.cli.uuid_ref, html)

    def test_ordenes_requieren_maquilador(self):
        r = self.url_open("/copacker/orders", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.authenticate("cli.fdcp", CLAVE)
        self.assertEqual(self.url_open("/copacker/orders/count").status_code, 403)

    def test_directorio_empaque_cajon_y_conteo(self):
        self.authenticate("cli.fdcp", CLAVE)
        r = self.url_open("/marketplace/copacking?q=FDCP")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sFilterDrawer"', r.text)
        self.assertIn('id="sf_sec_servicio"', r.text)
        self.assertEqual(self._count("/marketplace/copacking/count?q=FDCP"), 2)
        self.assertEqual(self._count("/marketplace/copacking/count?q=FDCP&servicio=entero"), 1)
        self.assertEqual(self._count("/marketplace/copacking/count?q=FDCP&servicio=cola"), 1)
        self.assertEqual(self._count("/marketplace/copacking/count?q=FDCP&ubicacion=Machala"), 1)
        html = self.url_open("/marketplace/copacking?q=FDCP&orden=tarifa").text
        self.assertLess(html.index("FDCP Planta Uno"), html.index("FDCP Planta Dos"))

    def test_directorio_empaque_solo_clientes(self):
        self.authenticate("maq.fdcp", CLAVE)
        self.assertEqual(self.url_open("/marketplace/copacking").status_code, 403)
        self.assertEqual(self.url_open("/marketplace/copacking/count").status_code, 403)
