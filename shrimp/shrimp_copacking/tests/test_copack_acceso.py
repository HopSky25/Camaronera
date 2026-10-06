from datetime import date, timedelta

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestCopackAccesoHttp(HttpCase):
    """Aislamiento por controlador (no solo por ir.rule) y redireccion del
    sitio de empaque.

    Las reglas del modulo solo valen para base.group_portal: un usuario
    INTERNO cuyo contacto es camaronera no tiene ninguna, asi que lo que le
    protege es el dominio explicito del controlador.
    """

    CLAVE = "Clave-de-prueba-123"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Socio = cls.env["res.partner"]
        cls.maq = Socio.create({
            "name": "Maquiladora acceso", "is_company": True,
            "email": "maq.acceso@prueba.test", "vat_or_id": "0955600001001",
            "shrimp_user_type": "maquilador",
            "pack_razon_social": "Maquiladora acceso S.A.",
            "pack_ubicacion": "Guayaquil", "pack_codigo_establecimiento": "99777",
            "pack_capacidad_lb_semana": 200000, "pack_lote_minimo_lb": 5000,
        })
        cls.oculta = Socio.create({
            "name": "Planta fuera del directorio", "is_company": True,
            "email": "oculta@prueba.test", "vat_or_id": "0955600004001",
            "shrimp_user_type": "maquilador", "pack_en_directorio": False,
            "pack_razon_social": "Planta oculta S.A.",
            "pack_ubicacion": "Machala", "pack_codigo_establecimiento": "99778",
        })

        def camaronera(nombre, correo, ruc):
            return Socio.create({
                "name": nombre, "is_company": True, "email": correo,
                "vat_or_id": ruc, "shrimp_user_type": "camaronera",
                "farm_razon_social": nombre + " S.A.",
                "farm_representante": "Rep", "farm_telefono": "04-0000000",
                "farm_ubicacion": "Guayas",
            })

        cls.cli = camaronera("Camaronera dueña", "duena@prueba.test", "0955600002001")
        cls.interna = camaronera("Camaronera interna", "interna@prueba.test",
                                 "0955600003001")

        cls.u_maq = cls.env["res.users"].create({
            "name": cls.maq.name, "login": "maq.acceso", "partner_id": cls.maq.id,
            "password": cls.CLAVE,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])],
        })
        # Usuario INTERNO: sin las reglas del portal. Operador del backoffice
        # (group_shrimp_user): desde 19.0.1.4.0 un interno sin ese grupo no
        # tiene acceso a los modelos del marketplace.
        cls.u_interno = cls.env["res.users"].create({
            "name": cls.interna.name, "login": "interno.acceso",
            "partner_id": cls.interna.id, "password": cls.CLAVE,
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("shrimp_marketplace.group_shrimp_user").id])],
        })

        sol = cls.env["shrimp.copack.request"].create({
            "client_partner_id": cls.cli.id, "quantity_lb": 40000,
            "presentation": "entero", "needed_from": date.today(),
            "needed_to": date.today() + timedelta(days=7),
            "copacker_partner_id": cls.maq.id,
        })
        sol.action_publish()
        oferta = cls.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": cls.maq.id,
            "rate_per_lb": 0.18, "capacity_lb": 40000,
            "available_from": date.today(),
            "available_to": date.today() + timedelta(days=6),
        })
        cls.orden = oferta.action_accept(actor=cls.cli)
        cls.sitio_maq = cls.env["website"].sudo()._shrimp_copacker_site()

    # ---------------- (3) usuario interno ----------------
    def test_interno_no_ve_ordenes_ajenas(self):
        self.authenticate("interno.acceso", self.CLAVE)
        r = self.url_open("/marketplace/copacking/orders")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.orden.name, r.text,
                         "un usuario interno no puede ver las ordenes de otro cliente")

    def test_interno_no_abre_orden_ajena(self):
        self.authenticate("interno.acceso", self.CLAVE)
        r = self.url_open("/marketplace/copacking/orders/%s" % self.orden.uuid_ref)
        self.assertEqual(r.status_code, 403)

    def test_interno_no_ve_planta_fuera_del_directorio(self):
        self.authenticate("interno.acceso", self.CLAVE)
        r = self.url_open("/marketplace/copacking/plants/%s" % self.oculta.uuid_ref)
        self.assertEqual(r.status_code, 404)
        r = self.url_open("/marketplace/copacking/plants/%s" % self.maq.uuid_ref)
        self.assertEqual(r.status_code, 200)
        # El id numérico ya no se acepta (ni siquiera el de una planta visible).
        r = self.url_open("/marketplace/copacking/plants/%d" % self.maq.id)
        self.assertEqual(r.status_code, 404)
        r = self.url_open("/marketplace/copacking/requests/new?a=%d" % self.maq.id)
        self.assertEqual(r.status_code, 404)
        r = self.url_open("/marketplace/copacking")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.oculta.name, r.text)

    # ---------------- (5) redireccion al sitio de empaque ----------------
    def test_get_maquilador_redirige_al_sitio_de_empaque(self):
        if not self.sitio_maq:
            self.skipTest("sin sitio de empaque")
        self.sitio_maq.domain = "http://empaque.example.test"
        self.authenticate("maq.acceso", self.CLAVE)
        r = self.url_open("/copacker/orders?f=firmar", allow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers.get("Location"),
                         "http://empaque.example.test/copacker/orders?f=firmar")

    def test_sin_dominio_se_sirve_normal(self):
        if not self.sitio_maq:
            self.skipTest("sin sitio de empaque")
        self.sitio_maq.domain = False
        self.authenticate("maq.acceso", self.CLAVE)
        r = self.url_open("/copacker/orders", allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.orden.name, r.text)

    # ---------------- (4) menu del sitio de empaque ----------------
    def test_menu_maquilador_oculto_a_otros_roles(self):
        menu = self.env["website.menu"].sudo().search(
            [("url", "=like", "/copacker/%")], limit=1)
        if not menu:
            self.skipTest("sin menu del maquilador")
        menu.invalidate_recordset(["is_visible"])
        self.assertTrue(menu.with_user(self.u_maq).is_visible)
        menu.invalidate_recordset(["is_visible"])
        self.assertFalse(menu.with_user(self.u_interno).is_visible)
        menu.invalidate_recordset(["is_visible"])
        self.assertFalse(menu.with_user(self.env.ref("base.public_user")).is_visible)
