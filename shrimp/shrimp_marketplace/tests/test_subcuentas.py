"""Cuenta de empresa con administrador y subusuarios (shrimp_user_registry
19.0.1.6.0): invitaciones, permisos por perfil (intersección con la matriz
de capacidades), selector de perfil, reglas de acceso, desactivación,
transferencia de la administración y documentos de la empresa."""
import re
from urllib.parse import urlsplit

from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, producto


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


def subusuario(env, empresa, login, permisos):
    contacto = env["res.partner"].sudo().create({
        "name": "Sub %s" % login, "email": "%s@prueba.test" % login,
        "parent_id": empresa.id, "type": "contact"})
    user = env["res.users"].sudo().create({
        "name": contacto.name, "login": login, "partner_id": contacto.id, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    env["shrimp.account.member"].sudo()._shrimp_ensure(user, empresa, permissions=permisos)
    return user


@tagged("post_install", "-at_install")
class TestSubcuentas(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "sc")
        cls.emp = cls.s["cam"]
        cls.emp._shrimp_request_role("laboratorio", {
            "shrimp_razon_social": "Cam y Lab S.A.", "shrimp_ubicacion": "Guayas"})
        cls.admin = cls.s["u_cam"]
        cls.Member = cls.env["shrimp.account.member"].sudo()
        cls.Member._shrimp_ensure(cls.admin, cls.emp, is_admin=True)

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def test_invitar_subusuario(self):
        self.authenticate(self.admin.login, CLAVE)
        pagina = self.url_open("/my/team")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Mi equipo", pagina.text)
        r = self.url_open("/my/team/invite", data={
            "csrf_token": _csrf(pagina.text), "name": "Ana Compras", "email": "ana.sc@prueba.test",
            "perm_camaronera_view": "1", "perm_camaronera_buy": "1"}, allow_redirects=False)
        self.assertIn("saved=invited", r.headers.get("Location", ""))
        user = self.env["res.users"].sudo().search([("login", "=", "ana.sc@prueba.test")])
        self.assertTrue(user)
        self.assertTrue(user.share)
        self.assertEqual(user.partner_id.parent_id, self.emp)
        m = user._shrimp_member()
        self.assertFalse(m.is_admin)
        self.assertEqual(m.company_id, self.emp)
        self.assertEqual(m._permissions_dict(), {"camaronera": {"view", "buy"}})
        self.assertEqual(m.state, "invited")
        # Un correo ya usado no se invita dos veces.
        r = self.url_open("/my/team/invite", data={
            "csrf_token": _csrf(pagina.text), "name": "Otra", "email": "ana.sc@prueba.test"},
            allow_redirects=False)
        self.assertIn("error=email_taken", r.headers.get("Location", ""))

    def test_permisos_interseccion_y_actor(self):
        sub = subusuario(self.env, self.emp, "compras.sc", {"camaronera": {"view", "buy"}})
        self.assertEqual(sub._shrimp_actor_partner(), self.emp, "actúa en nombre de la empresa")
        self.assertEqual(sub.shrimp_actor_partner_id, self.emp)
        emp_sub = self.emp.with_user(sub)
        emp_adm = self.emp.with_user(self.admin)
        self.assertTrue(emp_sub._shrimp_can("buy_from_laboratorio"))
        self.assertFalse(emp_sub._shrimp_can("sell_products"), "sin permiso «vender»")
        self.assertTrue(emp_adm._shrimp_can("sell_products"))
        # Perfil sin permiso: nada.
        self.assertFalse(emp_sub._shrimp_can("buy_from_semillero", role="laboratorio"))
        self.assertTrue(emp_adm._shrimp_can("buy_from_semillero", role="laboratorio"))
        # Lo estructural no depende del usuario.
        self.assertTrue(emp_sub._shrimp_can("add_profile"))
        # La contraparte (otra empresa) no se ve afectada por los permisos del subusuario.
        self.assertTrue(self.s["lab"].with_user(sub)._shrimp_can("sell_products"))

    def test_selector_de_perfil_del_subusuario(self):
        sub = subusuario(self.env, self.emp, "solo.cam.sc", {"camaronera": {"view"}})
        info = self.emp.with_user(sub)._shrimp_profile_switcher_info()
        self.assertEqual([r[0] for r in info["roles"]], ["camaronera"])
        self.assertFalse(info["can_add"])
        info_admin = self.emp.with_user(self.admin)._shrimp_profile_switcher_info()
        self.assertEqual([r[0] for r in info_admin["roles"]], ["laboratorio", "camaronera"])
        with self.assertRaises(UserError):
            self.emp.with_user(sub)._shrimp_set_active_role("laboratorio")
        self.emp.with_user(self.admin)._shrimp_set_active_role("laboratorio")
        # Perfil activo POR USUARIO (CR-12): cambia el del admin, no el de la empresa.
        self.assertEqual(self.admin.shrimp_active_role, "laboratorio")
        self.assertEqual(self.emp.shrimp_user_type, "camaronera")

    def test_reglas_de_acceso_del_subusuario(self):
        sub = subusuario(self.env, self.emp, "reglas.sc", {"camaronera": {"view"}})
        propio = producto(self.env, self.emp, nombre="Lote propio sc", publicado=False,
                          etapa="shrimp_marketplace.shrimp_stage_juvenil")
        ajeno = producto(self.env, self.s["lab2"], nombre="Lote ajeno sc", publicado=False)
        Prod = self.env["shrimp.product"].with_user(sub)
        self.assertEqual(Prod.search([("id", "in", (propio | ajeno).ids)]), propio.with_user(sub),
                         "el subusuario ve lo de su empresa y nada de otra")

    def test_subusuario_no_administra(self):
        sub = subusuario(self.env, self.emp, "noadmin.sc", {"camaronera": {"view", "buy", "sell"}})
        self.authenticate("noadmin.sc", CLAVE)
        self.assertEqual(self.url_open("/my/team").status_code, 403)
        pagina = self.url_open("/my/data").text
        r = self.url_open("/my/profile/add", data={
            "csrf_token": _csrf(pagina), "role": "semillero", "next": "/my/data"},
            allow_redirects=False)
        # CR-12: agregar perfiles es solo del administrador -> 403 de permisos.
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("semillero", self.emp._shrimp_roles(include_pending=True))
        # Acepta lo personal, no el contrato de la empresa.
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        priv = self.env.ref("shrimp_user_registry.legal_doc_privacy").current_version_id
        self.assertTrue(Acc._shrimp_record(sub, priv, "login_gate", generate_receipt=False))
        contrato = self.env.ref("shrimp_user_registry.legal_doc_contract").current_version_id
        with self.assertRaises(ValidationError):
            Acc._shrimp_record(sub, contrato, "login_gate", generate_receipt=False)

    def test_contrato_mayor_bloquea_al_subusuario_hasta_que_acepte_el_admin(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        sub = subusuario(self.env, self.emp, "bloq.sc", {"camaronera": {"view"}})
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        Docs = self.env["shrimp.legal.document"].sudo()
        personales = Docs.search([("scope", "=", "user")]).mapped("current_version_id")
        Acc._shrimp_record(sub, personales, "signup", generate_receipt=False)
        contrato = self.env.ref("shrimp_user_registry.legal_doc_contract")
        v = self.env["shrimp.legal.document.version"].browse(contrato.action_new_version()["res_id"])
        v.write({"change_summary": "Cláusula nueva", "change_type": "major"})
        v.action_publish()
        self.authenticate("bloq.sc", CLAVE)
        r = self.url_open("/my/account", allow_redirects=False)
        self.assertEqual(urlsplit(r.headers["Location"]).path, "/my/legal/accept")
        pagina = self.url_open(r.headers["Location"]).text
        self.assertIn("El administrador de tu cuenta tiene que aceptar", pagina)
        Acc._shrimp_record(self.admin, v, "login_gate", generate_receipt=False)
        self.assertEqual(self.url_open("/my/account", allow_redirects=False).status_code, 200)

    def test_desactivar_y_transferir_administracion(self):
        sub = subusuario(self.env, self.emp, "baja.sc", {"camaronera": {"view"}})
        otro = subusuario(self.env, self.emp, "nuevo.admin.sc", {"camaronera": {"view"}})
        self.authenticate(self.admin.login, CLAVE)
        token = _csrf(self.url_open("/my/team").text)
        m = sub._shrimp_member()
        self.url_open("/my/team/%s/toggle" % m.uuid_ref, data={"csrf_token": token})
        self.assertFalse(sub.with_context(active_test=False).active)
        self.assertEqual(m.with_context(active_test=False).state, "inactive")
        self.url_open("/my/team/%s/toggle" % m.uuid_ref, data={"csrf_token": token})
        self.assertTrue(sub.active)
        # Transferir la administración.
        m2 = otro._shrimp_member()
        self.url_open("/my/team/%s/make-admin" % m2.uuid_ref, data={"csrf_token": token, "confirm": "1"})
        self.assertTrue(m2.is_admin)
        viejo = self.admin._shrimp_member()
        self.assertFalse(viejo.is_admin)
        self.assertIn("camaronera", viejo._permissions_dict())
        self.assertFalse(self.admin._shrimp_is_account_admin())
        self.assertTrue(otro._shrimp_is_account_admin())
