"""Aprobación de perfiles y avisos legales:

* D-18: rechazo con motivo obligatorio (correo al socio) y, como mucho, 3
  nuevas solicitudes tras un rechazo (parámetro configurable).
* D-19: aviso al equipo que aprueba y, pasadas 48 horas hábiles,
  recordatorio y escalamiento (una vez).
* D-36: una versión MAYOR se envía por correo al administrador de cada
  cuenta; una MENOR, no.
"""
from datetime import datetime, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAprobaciones(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["res.partner"]
        cls.lab = P.create({
            "name": "Lab aprobaciones", "is_company": True, "email": "lab.apr@prueba.test",
            "vat_or_id": "0912377001001", "shrimp_user_type": "laboratorio",
            "shrimp_razon_social": "Lab Apr S.A.", "shrimp_ubicacion": "Durán"})
        cls.user = cls.env["res.users"].create({
            "name": "Admin lab apr", "login": "lab.apr", "email": "lab.apr@prueba.test",
            "partner_id": cls.lab.id,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        cls.env["shrimp.account.member"].sudo()._shrimp_ensure(cls.user, cls.lab, is_admin=True)
        cls.Mail = cls.env["mail.mail"].sudo()
        cls.Role = cls.env["shrimp.partner.role"].sudo()

    def _correos(self, texto):
        return self.Mail.search([("subject", "ilike", texto)])

    # ------------------------------------------------------------------ D-18
    def test_rechazo_exige_motivo_y_avisa(self):
        linea = self.lab._shrimp_request_role("semillero")
        with self.assertRaises(UserError):
            linea.action_reject()
        with self.assertRaises(UserError):
            linea.action_reject(reason="   ")
        linea.action_reject(reason="Falta el permiso de funcionamiento")
        self.assertEqual(linea.state, "rejected")
        self.assertEqual(linea.reject_reason, "Falta el permiso de funcionamiento")
        self.assertEqual(linea.rejection_count, 1)
        msg = self.lab.message_ids.filtered(lambda m: "Falta el permiso" in (m.body or ""))[:1]
        self.assertTrue(msg, "constancia con el motivo en la cuenta")
        self.assertIn(self.lab, msg.partner_ids, "se le envía al socio")

    def test_asistente_de_rechazo(self):
        linea = self.lab._shrimp_request_role("semillero")
        accion = linea.action_open_reject_wizard()
        self.assertEqual(accion["res_model"], "shrimp.partner.role.reject")
        asistente = self.env["shrimp.partner.role.reject"].with_context(
            **accion["context"]).create({"reason": "RUC no coincide"})
        asistente.action_confirm()
        self.assertEqual(linea.state, "rejected")
        self.assertEqual(linea.reject_reason, "RUC no coincide")

    def test_tres_nuevas_solicitudes_como_maximo(self):
        linea = self.lab._shrimp_request_role("semillero")
        for i in range(3):
            linea.action_reject(reason="Motivo %s" % i)
            self.assertEqual(linea._shrimp_rerequests_left(), 3 - i)
            self.lab._shrimp_request_role("semillero")
            self.assertEqual(linea.rerequest_count, i + 1)
        linea.action_reject(reason="Cuarto rechazo")
        self.assertEqual(linea._shrimp_rerequests_left(), 0)
        with self.assertRaises(ValidationError):
            self.lab._shrimp_request_role("semillero")
        # Configurable.
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.role_max_rerequests", "5")
        self.lab._shrimp_request_role("semillero")
        self.assertEqual(linea.state, "approved")

    # ------------------------------------------------------------------ D-19
    def test_horas_habiles(self):
        Role = self.Role
        # Viernes 12:00 (Ecuador = UTC-5) -> lunes 12:00: 12 h del viernes + 12 h del lunes.
        viernes = datetime(2026, 10, 16, 17, 0)
        lunes = datetime(2026, 10, 19, 17, 0)
        self.assertAlmostEqual(Role._shrimp_business_hours_between(viernes, lunes), 24.0)
        self.assertEqual(Role._shrimp_business_hours_between(lunes, viernes), 0.0)

    def test_aviso_recordatorio_y_escalamiento(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.approval_escalation_emails", "gerencia@prueba.test")
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.approver_emails", "aprobaciones@prueba.test")
        linea = self.Role.create({"partner_id": self.lab.id, "role": "semillero",
                                  "state": "pending"})
        self.assertTrue(linea.sla_notified_at, "aviso al equipo al llegar la solicitud")
        self.assertTrue(self._correos("Perfil por aprobar: %s" % self.lab.name))
        # Dentro del plazo: nada.
        self.assertEqual(self.Role._cron_approval_sla(), 0)
        # Fuera de plazo (una semana atrás): recordatorio y escalamiento, una vez.
        linea.with_context(shrimp_role_sync=True).write(
            {"request_date": datetime.now() - timedelta(days=7)})
        self.assertEqual(self.Role._cron_approval_sla(), 1)
        self.assertTrue(linea.sla_escalated_at)
        escalado = self._correos("Escalamiento:")
        self.assertTrue(escalado)
        self.assertIn("gerencia@prueba.test", escalado[:1].email_to or "")
        self.assertTrue(self._correos("Recordatorio:"))
        self.assertEqual(self.Role._cron_approval_sla(), 0, "una sola vez por solicitud")
        # Volver a pendiente reinicia el plazo.
        linea.action_reject(reason="x")
        linea.action_reset()
        self.assertEqual(linea.state, "pending")
        self.assertFalse(linea.sla_escalated_at)
        self.assertTrue(linea.sla_notified_at, "nuevo aviso al equipo")

    # ------------------------------------------------------------------ D-36
    def _version(self, change_type):
        doc = self.env.ref("shrimp_user_registry.legal_doc_terms")
        v = self.env["shrimp.legal.document.version"].browse(doc.action_new_version()["res_id"])
        v.write({"change_summary": "Cambio de prueba", "change_type": change_type,
                 "name": "test-%s" % change_type})
        return v

    def test_version_mayor_por_correo_a_los_administradores(self):
        antes = self._correos("debes aceptarla")
        self._version("minor").action_publish()
        self.assertEqual(self._correos("debes aceptarla"), antes, "una MENOR no se envía")
        v = self._version("major")
        v.action_publish()
        nuevos = self._correos("debes aceptarla") - antes
        self.assertTrue(nuevos)
        destinatarios = nuevos.mapped("recipient_ids")
        self.assertIn(self.user.partner_id, destinatarios)
        self.assertEqual(len(nuevos), len(destinatarios), "un correo por administrador")
