import re

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestTecnicoAcceso(HttpCase):
    """Un tecnico de campo (no admin de la empresa) no ve ni usa lo que es
    solo del administrador: «Mi equipo» y la edicion del perfil."""

    CLAVE = "Clave-de-prueba-123"
    CUENTA = "2100548837"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        portal = cls.env.ref("base.group_portal")
        cls.empresa = cls.env["res.partner"].create({
            "name": "Verificadora de prueba", "is_company": True,
            "shrimp_user_type": "verificador", "vat_or_id": "0955700001001",
            "email": "verif@prueba.test",
            "ver_bank_name": "Banco de prueba",
            "ver_bank_account_number": cls.CUENTA,
        })
        cls.tecnico = cls.env["res.partner"].create({
            "name": "Tecnico de prueba", "parent_id": cls.empresa.id,
            "shrimp_is_field_tech": True, "email": "tecnico@prueba.test",
        })

        def usuario(socio, login):
            return cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "password": cls.CLAVE, "group_ids": [(6, 0, [portal.id])],
            })

        cls.u_admin = usuario(cls.empresa, "verif.admin")
        cls.u_tec = usuario(cls.tecnico, "verif.tecnico")
        # Sin dominio no hay salto al sitio de verificadores: la peticion se
        # sirve en el sitio por el que entra el test.
        sitio = cls.env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False

    def test_tecnico_no_entra_a_mi_equipo(self):
        self.authenticate("verif.tecnico", self.CLAVE)
        self.assertEqual(self.url_open("/verifier/technicians").status_code, 403)
        self.authenticate("verif.admin", self.CLAVE)
        self.assertEqual(self.url_open("/verifier/technicians").status_code, 200)

    def test_perfil_solo_lectura_para_el_tecnico(self):
        self.authenticate("verif.tecnico", self.CLAVE)
        r = self.url_open("/verifier/profile")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("/verifier/profile/save", r.text)
        self.assertNotIn(self.CUENTA, r.text, "el tecnico no ve la cuenta completa")
        self.assertIn(self.CUENTA[-4:], r.text)
        self.authenticate("verif.admin", self.CLAVE)
        r = self.url_open("/verifier/profile")
        self.assertIn("/verifier/profile/save", r.text)
        self.assertIn(self.CUENTA, r.text)

    def test_tecnico_no_guarda_el_perfil(self):
        self.authenticate("verif.tecnico", self.CLAVE)
        pagina = self.url_open("/verifier/profile").text
        m = re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fA-Fo]+)["\']', pagina)
        if not m:
            self.skipTest("no se encontro el token CSRF en la pagina")
        r = self.url_open("/verifier/profile/save",
                          data={"csrf_token": m.group(1),
                                "ver_bank_account_number": "999"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.empresa.ver_bank_account_number, self.CUENTA)

    def test_menu_mi_equipo_oculto_al_tecnico(self):
        menu = self.env["website.menu"].sudo().search(
            [("url", "=", "/verifier/technicians")], limit=1)
        if not menu:
            self.skipTest("sin menu del sitio de verificadores")
        menu.invalidate_recordset(["is_visible"])
        self.assertFalse(menu.with_user(self.u_tec).is_visible)
        menu.invalidate_recordset(["is_visible"])
        self.assertTrue(menu.with_user(self.u_admin).is_visible)
