"""Varios perfiles por cuenta (shrimp.partner.role).

Solo con los roles de este módulo (semillero, laboratorio, camaronera); los
perfiles con aprobación (empacadora, maquilador) y el verificador se prueban
en sus módulos.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_user_registry.migration_utils import crear_perfiles_desde_tipo


@tagged("post_install", "-at_install")
class TestMultiPerfil(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["res.partner"]
        cls.lab = P.create({
            "name": "Lab multiperfil", "is_company": True, "email": "lab.mp@prueba.test",
            "vat_or_id": "0912300001001", "shrimp_user_type": "laboratorio",
            "shrimp_razon_social": "Lab MP S.A.", "shrimp_ubicacion": "Durán"})

    def test_alta_crea_su_primer_perfil(self):
        linea = self.lab.shrimp_role_ids
        self.assertEqual(linea.mapped("role"), ["laboratorio"])
        self.assertEqual(linea.state, "approved")
        self.assertTrue(linea.is_active_profile)
        self.assertEqual(self.lab._shrimp_roles(), ["laboratorio"])
        self.assertEqual(self.lab.shrimp_role_count, 1)

    def test_agregar_perfil_sin_aprobacion_y_actuar_con_el(self):
        # Laboratorio que también es camaronera: la camaronera exige
        # representante y teléfono en el perfil común.
        with self.assertRaises(ValidationError):
            self.lab._shrimp_request_role("camaronera")
        linea = self.lab._shrimp_request_role("camaronera", {
            "shrimp_representante": "Ana Pérez", "shrimp_telefono": "04-2222222"})
        self.assertEqual(linea.state, "approved", "camaronera no requiere aprobación")
        self.assertEqual(self.lab._shrimp_roles(), ["laboratorio", "camaronera"])
        self.assertEqual(self.lab.shrimp_user_type, "laboratorio", "agregar no cambia el activo")
        # Capacidades por perfil: como laboratorio compra nauplio de
        # semillero; como camaronera no. (La larva de laboratorio la compran
        # los dos: la camaronera y, al mismo nivel, otro laboratorio.)
        self.assertTrue(self.lab._shrimp_can("buy_from_semillero"))
        self.assertFalse(self.lab._shrimp_can("buy_from_semillero", role="camaronera"))
        self.assertTrue(self.lab._shrimp_can("buy_from_laboratorio", role="camaronera"))
        self.assertTrue(self.lab._shrimp_can_any("buy_from_laboratorio"))
        self.assertEqual(self.lab._shrimp_roles_with("buy_from_semillero"), ["laboratorio"])
        # Cambio de perfil activo.
        self.lab._shrimp_set_active_role("camaronera")
        self.assertEqual(self.lab.shrimp_user_type, "camaronera")
        self.assertTrue(self.lab._shrimp_can("buy_from_laboratorio"))
        self.assertEqual(self.lab.shrimp_account_state, "approved")
        self.assertEqual(len(self.lab.shrimp_role_ids), 2, "cambiar no crea perfiles")
        # No se puede pedir dos veces el mismo perfil.
        with self.assertRaises(ValidationError):
            self.lab._shrimp_request_role("camaronera")

    def test_no_se_activa_un_perfil_que_no_se_tiene(self):
        with self.assertRaises(UserError):
            self.lab._shrimp_set_active_role("semillero")
        self.assertFalse(self.lab._shrimp_can("sell_products", role="semillero"))
        self.assertFalse(self.lab._shrimp_has_role("semillero"))

    def test_perfil_rechazado_no_cuenta(self):
        linea = self.lab._shrimp_request_role("semillero")
        linea.action_reject()
        self.assertNotIn("semillero", self.lab._shrimp_roles(include_pending=True))
        with self.assertRaises(UserError):
            self.lab._shrimp_set_active_role("semillero")
        # Se puede volver a pedir.
        self.lab._shrimp_request_role("semillero")
        self.assertIn("semillero", self.lab._shrimp_roles())

    def test_estado_del_perfil_activo_se_refleja_en_la_cuenta(self):
        self.lab._shrimp_request_role("semillero")
        self.lab._shrimp_set_active_role("semillero")
        linea = self.lab.shrimp_role_ids.filtered(lambda r: r.role == "semillero")
        linea.action_reject()
        self.assertEqual(self.lab.shrimp_account_state, "rejected")
        self.assertFalse(self.lab.shrimp_is_operational())
        self.assertTrue(self.lab.shrimp_is_operational(role="laboratorio"))
        # Y al revés: escribir el estado de la cuenta lo copia al perfil activo.
        self.lab.shrimp_account_state = "approved"
        self.assertEqual(linea.state, "approved")

    def test_tipo_escrito_en_backoffice_agrega_perfil(self):
        self.lab.write({"shrimp_user_type": "semillero"})
        self.assertEqual(sorted(self.lab.shrimp_role_ids.mapped("role")), ["laboratorio", "semillero"])
        self.assertEqual(self.lab.shrimp_user_type, "semillero")

    def test_quitar_el_perfil_activo(self):
        activo = self.lab.shrimp_role_ids
        with self.assertRaises(UserError):
            activo.unlink()
        self.lab._shrimp_request_role("semillero")
        activo.unlink()
        self.assertEqual(self.lab.shrimp_user_type, "semillero", "pasa al otro perfil aprobado")

    def test_contacto_hijo_usa_los_perfiles_de_su_empresa(self):
        hijo = self.env["res.partner"].create({"name": "Empleado MP", "parent_id": self.lab.id})
        self.assertEqual(hijo._shrimp_roles(), ["laboratorio"])
        self.assertTrue(hijo._shrimp_has_role("laboratorio"))
        self.assertIsNone(hijo._shrimp_profile_switcher_info())

    def test_dominio_por_perfil(self):
        P = self.env["res.partner"]
        self.assertNotIn(self.lab, P.search(P._shrimp_role_domain("camaronera")))
        self.lab._shrimp_request_role("camaronera", {
            "shrimp_representante": "Ana", "shrimp_telefono": "04-1"})
        self.assertIn(self.lab, P.search(P._shrimp_role_domain("camaronera")))
        self.assertIn(self.lab, P.search(P._shrimp_role_domain(["semillero", "laboratorio"])))

    def test_usuario_perfil_activo(self):
        user = self.env["res.users"].create({
            "name": "Lab MP", "login": "lab.mp.user", "partner_id": self.lab.id,
            "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertEqual(user.shrimp_active_role, "laboratorio")
        self.lab._shrimp_request_role("semillero")
        user.shrimp_active_role = "semillero"
        self.assertEqual(self.lab.shrimp_user_type, "semillero")

    def test_migracion_desde_tipo_unico(self):
        """Una base anterior: socios con shrimp_user_type y sin líneas."""
        pendiente = self.env["res.partner"].create({
            "name": "Semillero viejo", "is_company": True, "email": "sem.viejo@prueba.test",
            "vat_or_id": "0912300009001", "shrimp_user_type": "semillero",
            "shrimp_account_state": "pending"})
        self.env.flush_all()
        self.env.cr.execute("DELETE FROM shrimp_partner_role WHERE partner_id IN %s",
                            (tuple([self.lab.id, pendiente.id]),))
        self.env.cr.execute("UPDATE res_partner SET shrimp_role_count = 0 WHERE id IN %s",
                            (tuple([self.lab.id, pendiente.id]),))
        self.env.invalidate_all()
        self.assertFalse(self.lab.shrimp_role_ids)
        creadas = crear_perfiles_desde_tipo(self.env.cr)
        self.assertGreaterEqual(creadas, 2)
        self.env.invalidate_all()
        self.assertEqual(self.lab.shrimp_role_ids.mapped("role"), ["laboratorio"])
        self.assertEqual(self.lab.shrimp_role_ids.state, "approved")
        self.assertEqual(pendiente.shrimp_role_ids.state, "pending")
        self.assertEqual(self.lab.shrimp_role_count, 1)
        # Idempotente.
        self.assertEqual(crear_perfiles_desde_tipo(self.env.cr), 0)
