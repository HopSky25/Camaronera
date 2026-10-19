"""D-20: cuenta suspendida y perfil rechazado.

* Suspender exige motivo, avisa al socio y retira sus lotes publicados.
* Una cuenta suspendida no publica ni empieza compras (ni como compradora
  ni como vendedora); lo que ya estaba en curso no se toca.
* Rechazar un perfil retira lo publicado con ESE perfil.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import crear_socios, producto


@tagged("post_install", "-at_install")
class TestSuspension(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "sus")
        cls.lab = cls.s["lab"]
        cls.cam = cls.s["cam"]
        cls.lote = producto(cls.env, cls.lab, nombre="Larva suspensión")

    def test_suspender_exige_motivo_y_retira_lotes(self):
        with self.assertRaises(UserError):
            self.lab.action_shrimp_suspend_account(reason="")
        accion = self.lab.action_shrimp_open_suspend_wizard()
        self.env[accion["res_model"]].with_context(**accion["context"]).create(
            {"reason": "Incumplimientos reiterados"}).action_confirm()
        self.assertTrue(self.lab.shrimp_suspended)
        self.assertEqual(self.lab.shrimp_suspension_reason, "Incumplimientos reiterados")
        self.assertEqual(self.lote.state, "draft", "el lote se retira de la venta")
        self.assertFalse(self.lab.shrimp_is_operational())
        self.assertTrue(self.lab.message_ids.filtered(
            lambda m: "Incumplimientos reiterados" in (m.body or "")))
        # No se vuelve a publicar mientras dure.
        with self.assertRaises(ValidationError):
            self.lote.action_publish()
        # Nadie le compra ni compra ella.
        self.assertTrue(self.lote.motivo_no_comprable(self.cam))
        otro = producto(self.env, self.s["lab2"], nombre="Larva de otro")
        self.assertIn("suspendida", otro.motivo_no_comprable(self.lab) or self.env[
            "shrimp.transaction"]._shrimp_buyer_problem(self.s["lab2"], self.lab,
                                                         buyer_role="laboratorio"))
        # Se levanta: vuelve a operar (sus lotes quedan en borrador).
        self.lab.action_shrimp_unsuspend_account()
        self.assertFalse(self.lab.shrimp_suspended)
        self.assertTrue(self.lab.shrimp_is_operational())
        self.assertEqual(self.lote.state, "draft")

    def test_compras_en_curso_no_se_tocan(self):
        tx = self.lote.execute_purchase_flow(self.cam, 1.0)["transaction"]
        estado = tx.state
        self.lab.action_shrimp_suspend_account(reason="Revisión")
        self.assertEqual(tx.state, estado)
        # Escribir en la compra en curso sigue permitido (la regla de partes
        # solo se mira al crear).
        tx.sudo().write({"seller_invoice_number": False})

    def test_rechazar_un_perfil_retira_lo_publicado_con_el(self):
        self.cam._shrimp_request_role("laboratorio", {
            "shrimp_razon_social": "Cam y Lab sus", "shrimp_ubicacion": "Guayas"})
        del_lab = producto(self.env, self.cam, nombre="Larva de la camaronera",
                           seller_role="laboratorio")
        de_cam = producto(self.env, self.cam, nombre="Juvenil de la camaronera",
                          etapa="shrimp_marketplace.shrimp_stage_juvenil")
        linea = self.cam.shrimp_role_ids.filtered(lambda r: r.role == "laboratorio")
        linea.action_reject(reason="Sin permiso de funcionamiento")
        self.assertEqual(del_lab.state, "draft")
        self.assertEqual(de_cam.state, "published", "lo del otro perfil sigue publicado")
