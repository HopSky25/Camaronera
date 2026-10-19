"""Equipo de campo: un técnico RESPONSABLE y técnicos de APOYO (19.0.1.22.0).

Decisiones del usuario (finales): todos los técnicos asignados cargan datos de
campo; solo el responsable (o la empresa) firma el veredicto; no hay pago por
técnico; el plazo «asignar técnico» se cumple con el responsable.
"""
import re

from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE

from .common_seguridad import montar_verificacion


@tagged("post_install", "-at_install")
class TestEquipoCampo(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "eqc")
        portal = cls.env.ref("base.group_portal")
        cls.apoyo = cls.env["res.partner"].create({
            "name": "Técnico de apoyo eqc", "parent_id": cls.s["verif"].id,
            "shrimp_is_field_tech": True, "email": "apoyo.eqc@prueba.test"})
        cls.u_apoyo = cls.env["res.users"].create({
            "name": cls.apoyo.name, "login": "apoyo.eqc", "partner_id": cls.apoyo.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        sitio = cls.env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False

    def _armar(self):
        v = self.v.sudo()
        v.action_assign_technician(self.s["tec"])
        v.action_add_support_technician(self.apoyo)
        return v

    def _csrf(self, url):
        html = self.url_open(url).text
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertTrue(m, "la página trae el token CSRF")
        return m.group(1)

    # ------------------------------------------------------------------
    # Modelo
    # ------------------------------------------------------------------
    def test_responsable_y_apoyo(self):
        v = self.v.sudo()
        with self.assertRaises(UserError, msg="sin responsable no hay apoyo"):
            v.action_add_support_technician(self.apoyo)
        v.action_assign_technician(self.s["tec"])
        self.assertEqual(v.state, "assigned", "el responsable cumple el plazo de asignación")
        self.assertFalse(v.deadline_overdue)
        v.action_add_support_technician(self.apoyo)
        self.assertEqual(v.support_technician_ids, self.apoyo)
        self.assertEqual(v.participant_partner_ids, self.s["tec"] | self.apoyo)
        self.assertEqual(v.shrimp_tech_role_of(self.s["tec"]), "responsible")
        self.assertEqual(v.shrimp_tech_role_of(self.apoyo), "support")
        self.assertIn("apoyo", v.shrimp_team_text())
        # Cambiar el responsable: el anterior pasa a apoyo, el nuevo deja de serlo.
        v.action_make_responsible(self.apoyo)
        self.assertEqual(v.technician_partner_id, self.apoyo)
        self.assertEqual(v.support_technician_ids, self.s["tec"])
        v.action_remove_support_technician(self.s["tec"])
        self.assertFalse(v.support_technician_ids)

    def test_reglas_del_apoyo(self):
        v = self.v.sudo()
        v.action_assign_technician(self.s["tec"])
        for ajeno in (self.s["tec_ajeno"], self.s["verif"], self.s["cam"]):
            with self.assertRaises(ValidationError, msg=ajeno.name):
                v.write({"support_technician_ids": [(4, ajeno.id)]})
        with self.assertRaises(ValidationError, msg="no puede ser responsable y apoyo"):
            v.write({"support_technician_ids": [(4, self.s["tec"].id)]})

    def test_solo_el_responsable_firma(self):
        v = self._armar()
        self.assertTrue(v._puede_dictaminar(self.s["tec"]))
        self.assertTrue(v._puede_dictaminar(self.s["verif"]))
        self.assertFalse(v._puede_dictaminar(self.apoyo))
        self.assertTrue(v.shrimp_can_load_field(self.apoyo))

    def test_reasignar_verificadora_vacia_el_equipo(self):
        v = self._armar()
        v._shrimp_reassign_verifier(self.s["verif2"], "prueba", "plataforma")
        self.assertFalse(v.technician_partner_id)
        self.assertFalse(v.support_technician_ids)

    def test_regla_de_lectura_del_apoyo(self):
        v = self._armar()
        V = self.env["shrimp.verification"].with_user(self.u_apoyo)
        self.assertIn(v, V.search([]), "el apoyo lee la verificación por ORM")
        self.assertIn(v.dispatch_id, self.env["shrimp.dispatch"].with_user(self.u_apoyo).search([]))

    # ------------------------------------------------------------------
    # Portal
    # ------------------------------------------------------------------
    def test_portal_apoyo_carga_pero_no_firma(self):
        v = self._armar()
        url = "/verifier/verifications/%s" % v.uuid_ref
        self.authenticate("apoyo.eqc", CLAVE)
        r = self.url_open("/verifier/inbox")
        self.assertIn(v.name, r.text, "la bandeja del apoyo trae la orden")
        self.assertIn("Apoyo", r.text)
        tok = self._csrf(url)
        # Inicia la inspección (cualquier asignado).
        r = self.url_open(url + "/start", data={"csrf_token": tok})
        self.assertEqual(r.status_code, 200)
        v.invalidate_recordset()
        self.assertEqual(v.state, "in_field")
        # Carga datos de campo.
        tok = self._csrf(url)
        self.url_open(url + "/save", data={"csrf_token": tok, "larvae_qty_verified": "9",
                                           "larvae_survival_rate": "88"})
        v.invalidate_recordset()
        self.assertEqual(v.larvae_qty_verified, 9.0)
        html = self.url_open(url).text
        self.assertIn("vr-locked", html, "el apoyo ve el veredicto bloqueado")
        self.assertNotIn('/verdict"', html)
        r = self.url_open(url + "/verdict", data={"csrf_token": tok, "verdict": "approve"})
        self.assertEqual(r.status_code, 403, "el apoyo no firma")

    def test_portal_admin_arma_el_equipo(self):
        v = self.v.sudo()
        v.action_assign_technician(self.s["tec"])
        url = "/verifier/verifications/%s" % v.uuid_ref
        self.authenticate("verif.eqc", CLAVE)
        tok = self._csrf(url)
        self.url_open(url + "/team/add", data={"csrf_token": tok, "technician_ref": self.apoyo.uuid_ref})
        v.invalidate_recordset()
        self.assertEqual(v.support_technician_ids, self.apoyo)
        self.url_open(url + "/team/assign", data={"csrf_token": tok, "technician_ref": self.apoyo.uuid_ref})
        v.invalidate_recordset()
        self.assertEqual(v.technician_partner_id, self.apoyo)
        self.assertEqual(v.support_technician_ids, self.s["tec"])
        self.url_open(url + "/team/delete", data={"csrf_token": tok, "technician_ref": self.s["tec"].uuid_ref,
                                                   "next": "inbox"})
        v.invalidate_recordset()
        self.assertFalse(v.support_technician_ids)
        # Un técnico de otra empresa no entra.
        self.url_open(url + "/team/add", data={"csrf_token": tok, "technician_ref": self.s["tec_ajeno"].uuid_ref})
        v.invalidate_recordset()
        self.assertFalse(v.support_technician_ids)
        # El técnico (no administrador) no arma el equipo.
        self.authenticate("tec.eqc", CLAVE)
        tok = self._csrf(url)
        r = self.url_open(url + "/team/add", data={"csrf_token": tok, "technician_ref": self.s["tec"].uuid_ref})
        self.assertEqual(r.status_code, 403)
