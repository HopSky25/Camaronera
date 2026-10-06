"""El verificador NO es un perfil exclusivo: una cuenta puede verificar y
además comprar o vender. El conflicto de interés se controla en cada
verificación: nunca verifica una compra en la que su empresa es parte."""
import base64
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, PDF_MIN, crear_socios, producto


@tagged("post_install", "-at_install")
class TestMultiPerfilVerificacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "mpv")
        # La camaronera de la cadena también es verificadora acreditada.
        cls.multi = cls.s["cam"]
        cls.multi._shrimp_request_role("verificador")
        att = env["ir.attachment"].create({
            "name": "acred.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        env["shrimp.user.certificate.line"].create({
            "partner_id": cls.multi.id,
            "certificate_id": env.ref("shrimp_verification.cert_acreditacion_verificador").id,
            "file_attachment_id": att.id, "status": "approved", "certificate_number": "MPV-1",
            "issue_date": date.today() - timedelta(days=30),
            "expiry_date": date.today() + timedelta(days=365)})
        cls.tec = env["res.partner"].create({
            "name": "Técnico MPV", "parent_id": cls.multi.id, "shrimp_is_field_tech": True,
            "email": "tec.mpv@prueba.test"})
        cls.lote = producto(env, cls.s["lab"], nombre="Larva MPV")

    def test_verificador_combinable_con_otros_perfiles(self):
        self.assertEqual(self.multi._shrimp_roles(), ["camaronera", "verificador"])
        self.assertEqual(self.multi.shrimp_user_type, "camaronera")
        self.assertTrue(self.multi.verifier_is_accredited)
        # Sale en el selector aunque navegue como camaronera.
        self.assertIn(self.multi, self.env["res.partner"].available_verifiers())
        # El técnico trabaja para el perfil verificador de su empresa.
        self.assertEqual(self.tec.shrimp_verifier_company(), self.multi)
        self.multi._shrimp_set_active_role("verificador")
        self.assertTrue(self.multi.shrimp_is_verifier_admin())

    def test_conflicto_de_interes(self):
        # Compra como camaronera: no puede elegirse a sí misma como verificadora.
        with self.assertRaises(ValidationError):
            self.lote.start_verified_purchase(self.multi, 5.0, self.multi, fee=10.0)
        # El selector del comprador no le ofrece su propia empresa.
        u_multi = self.s["u_cam"]
        propios = self.env["res.partner"].with_user(u_multi).sudo().available_verifiers()
        self.assertNotIn(self.multi, propios)
        # Para otros, sí verifica (y la restricción del modelo lo permite).
        lote_lab2 = producto(self.env, self.s["lab2"], nombre="Larva MPV 2")
        otra_cam = self.env["res.partner"].create({
            "name": "Camaronera compradora MPV", "is_company": True,
            "email": "cc.mpv@prueba.test", "vat_or_id": "0955500001001",
            "shrimp_user_type": "camaronera", "shrimp_razon_social": "CC S.A.",
            "shrimp_representante": "Rep", "shrimp_telefono": "04-1", "shrimp_ubicacion": "Guayas"})
        res = lote_lab2.start_verified_purchase(otra_cam, 5.0, self.multi, fee=10.0)
        self.assertEqual(res["verification"].verifier_partner_id, self.multi)
        # La misma ENTIDAD comercial cuenta como la misma parte (sus
        # contactos y técnicos también).
        self.assertTrue(self.multi.shrimp_same_entity(self.tec))
        self.assertFalse(self.multi.shrimp_same_entity(otra_cam))
        with self.assertRaises(ValidationError):
            res["verification"].transaction_id.sudo().write({"buyer_partner_id": self.multi.id})
            res["verification"]._check_verifier_independence()
