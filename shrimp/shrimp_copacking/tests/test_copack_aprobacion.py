from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestCopackAprobacion(HttpCase):
    """M4: un maquilador registrado por la web no opera hasta que la
    administración aprueba su cuenta."""

    CLAVE = "Clave-de-prueba-123"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maq = cls.env["res.partner"].create({
            "name": "Maquiladora pendiente", "is_company": True,
            "email": "maq.pend@prueba.test", "vat_or_id": "0955800001001",
            "shrimp_user_type": "maquilador", "pack_razon_social": "Pend S.A.",
            "pack_ubicacion": "Guayaquil", "shrimp_account_state": "pending"})
        cls.env["res.users"].create({
            "name": cls.maq.name, "login": "maq.pend", "partner_id": cls.maq.id,
            "password": cls.CLAVE,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        sitio = cls.env["website"].sudo()._shrimp_copacker_site()
        if sitio:
            sitio.domain = False

    def test_maquilador_pendiente_no_entra_a_su_bandeja(self):
        self.authenticate("maq.pend", self.CLAVE)
        self.assertEqual(self.url_open("/copacker/inbox").status_code, 403)
        self.maq.action_shrimp_approve_account()
        self.assertEqual(self.url_open("/copacker/inbox").status_code, 200)
