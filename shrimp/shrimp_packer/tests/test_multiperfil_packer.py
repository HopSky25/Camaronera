"""Camaronera que también es empacadora: el perfil de empacadora se pide,
queda pendiente, no sirve hasta que la administración lo aprueba y, una vez
aprobado, la cuenta actúa con uno u otro perfil según el activo."""
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios, csrf_de


def _adulto(env, vendedor, nombre):
    return env["shrimp.product"].create({
        "name": nombre, "seller_partner_id": vendedor.id, "seller_role": "camaronera",
        "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
        "presentation": "entero", "size_grade_id": env.ref("shrimp_marketplace.size_entero_3040").id,
        "initial_qty": 1000.0, "price": 2.5, "state": "published"})


@tagged("post_install", "-at_install")
class TestMultiPerfilPacker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "mpk")
        cls.cam = cls.s["cam"]
        cls.otra = cls.env["res.partner"].create({
            "name": "Otra camaronera mpk", "is_company": True, "email": "otra.mpk@prueba.test",
            "vat_or_id": "0944400001001", "shrimp_user_type": "camaronera",
            "shrimp_razon_social": "Otra S.A.", "shrimp_representante": "Rep",
            "shrimp_telefono": "04-1", "shrimp_ubicacion": "El Oro"})
        cls.adulto = _adulto(cls.env, cls.otra, "Engorde mpk")

    def test_perfil_pendiente_no_se_puede_usar(self):
        linea = self.cam._shrimp_request_role("empacadora", {"emp_planta_nombre": "Planta MPK"})
        self.assertEqual(linea.state, "pending", "la empacadora requiere aprobación")
        self.assertEqual(self.cam.emp_planta_nombre, "Planta MPK")
        self.assertEqual(self.cam.shrimp_user_type, "camaronera")
        self.assertEqual(self.cam.shrimp_account_state, "approved",
                         "el perfil pendiente no bloquea la cuenta")
        self.assertNotIn("empacadora", self.cam._shrimp_roles())
        self.assertIn("empacadora", self.cam._shrimp_roles(include_pending=True))
        with self.assertRaises(UserError):
            self.cam._shrimp_set_active_role("empacadora")
        self.assertFalse(self.cam._shrimp_can("commit_harvest", role="empacadora"))
        self.assertTrue(self.adulto.motivo_no_comprable(self.cam, role="empacadora"))
        self.assertNotIn(self.cam, self.env["res.partner"].empacadoras_activas())
        info = self.cam._shrimp_profile_switcher_info()
        self.assertEqual([r[0] for r in info["roles"]], ["camaronera"])
        self.assertEqual([r[0] for r in info["pending"]], ["empacadora"])

    def test_perfil_aprobado_actua_como_empacadora(self):
        linea = self.cam._shrimp_request_role("empacadora")
        linea.action_approve()
        self.assertEqual(self.cam._shrimp_roles(), ["camaronera", "empacadora"])
        # Aún como camaronera: es empacadora para quien la busca como tal.
        self.assertIn(self.cam, self.env["res.partner"].empacadoras_activas())
        self.assertTrue(self.cam._shrimp_can_any("commit_harvest"))
        self.assertFalse(self.cam._shrimp_can("commit_harvest"))
        # Como camaronera también puede comprarlo: compra al mismo nivel
        # (camaronera -> camaronera) y queda con ese tipo.
        self.assertFalse(self.adulto.motivo_no_comprable(self.cam))
        self.assertEqual(self.env["shrimp.transaction"]._shrimp_tx_type(
            "camaronera", self.cam._shrimp_active_role()), "camaronera_to_camaronera")
        self.assertFalse(self.adulto.motivo_no_comprable(self.cam, role="empacadora"))
        # Cambia de perfil.
        self.cam._shrimp_set_active_role("empacadora")
        self.assertTrue(self.cam._shrimp_can("commit_harvest"))
        self.assertTrue(self.cam.shrimp_is_operational())
        self.assertFalse(self.adulto.motivo_no_comprable(self.cam))
        # Una lista de precios la emite la cuenta con perfil empacadora y va a
        # camaroneras (la propia cuenta camaronera no, que es ella misma).
        lista = self.env["shrimp.price.list"].create({
            "name": "Lista MPK", "issuer_partner_id": self.cam.id,
            "recipient_ids": [(6, 0, self.otra.ids)]})
        self.assertTrue(lista)

    def test_rechazo_de_un_perfil_no_toca_los_demas(self):
        linea = self.cam._shrimp_request_role("empacadora")
        linea.action_reject()
        self.assertEqual(self.cam.shrimp_account_state, "approved")
        self.assertTrue(self.cam.shrimp_is_operational())
        self.assertFalse(self.cam.shrimp_is_operational(role="empacadora"))


@tagged("post_install", "-at_install")
class TestMultiPerfilPackerPortal(HttpCase):
    """Los permisos del portal siguen al perfil activo."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "mpkh")
        cls.cam = cls.s["cam"]

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def test_agregar_empacadora_y_cambiar_permisos(self):
        self.authenticate(self.s["u_cam"].login, CLAVE)
        self.assertEqual(self.url_open("/marketplace/supply").status_code, 403)
        pagina = self.url_open("/marketplace/my-account").text
        token = csrf_de(pagina)
        r = self.url_open("/my/profile/add", data={
            "csrf_token": token, "role": "empacadora", "next": "/marketplace/my-account",
            "shrimp_razon_social": "Cam y Emp S.A.", "emp_planta_nombre": "Planta MPKH"},
            allow_redirects=False)
        self.assertIn("saved=perfil_pendiente", r.headers.get("Location", ""))
        linea = self.cam.shrimp_role_ids.filtered(lambda l: l.role == "empacadora")
        self.assertEqual(linea.state, "pending")
        # Pendiente: no puede activarlo.
        self.url_open("/my/profile/activate", data={
            "csrf_token": token, "role": "empacadora", "next": "/marketplace/my-account"})
        self.assertEqual(self.cam.shrimp_user_type, "camaronera")
        self.assertIn("pendiente de aprobación", self.url_open("/marketplace/my-account").text)
        # La administración lo aprueba en «Perfiles por aprobar».
        linea.action_approve()
        self.url_open("/my/profile/activate", data={
            "csrf_token": token, "role": "empacadora", "next": "/marketplace/my-account"})
        self.assertEqual(self.cam.shrimp_user_type, "empacadora")
        self.assertEqual(self.url_open("/marketplace/supply").status_code, 200)
        self.assertIn("Perfil: Empacadora", self.url_open("/marketplace/my-account").text)
        # Y de vuelta: como camaronera la oferta vuelve a estar cerrada.
        self.url_open("/my/profile/activate", data={
            "csrf_token": token, "role": "camaronera", "next": "/marketplace/my-account"})
        self.assertEqual(self.url_open("/marketplace/supply").status_code, 403)
