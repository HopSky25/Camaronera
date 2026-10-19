"""Hueco #7: derechos del titular (LOPDP) — solicitudes con plazo, copia
portable, anonimización con retención y purga de 12 meses."""
import io
import json
import zipfile
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestLopdp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "lpd")
        cls.Req = cls.env["shrimp.data.request"].sudo()
        cls.ICP = cls.env["ir.config_parameter"].sudo()

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    # ------------------------------------------------------------ solicitudes
    def test_solicitud_portal_crea_registro_con_plazo(self):
        self.ICP.set_param("shrimp.dpo_email", "dpo@empresa.test")
        self.authenticate("lab.seclpd", CLAVE)
        token = csrf_de(self.url_open("/my/data").text)
        r = self.url_open("/my/data/request", data={"csrf_token": token, "tipo": "eliminacion",
                                                    "detalle": "Borrar todo"}, allow_redirects=False)
        self.assertIn("/my/data?ok=1", r.headers["Location"])
        req = self.Req.search([("user_id", "=", self.s["u_lab"].id)])
        self.assertEqual(len(req), 1)
        self.assertEqual(req.request_type, "eliminacion")
        self.assertEqual(req.state, "received")
        self.assertEqual(req.subject_scope, "company", "el titular de la cuenta pide por la empresa")
        self.assertEqual(req.account_id, self.s["lab"])
        self.assertEqual(req.deadline, fields.Date.to_date(req.received_at) + timedelta(days=15))
        self.assertTrue(req.name.startswith("LOPDP/"))
        self.assertTrue(self.env["mail.mail"].sudo().search(
            [("email_to", "=", "dpo@empresa.test"), ("subject", "like", req.name)]))
        # Se ve en Mis datos.
        self.assertIn(req.name, self.url_open("/my/data").text)

    def test_sin_dpo_avisa_a_los_administradores(self):
        self.ICP.set_param("shrimp.dpo_email", False)
        req = self.Req._shrimp_from_portal(self.s["u_cam"], "acceso", "x")
        self.assertFalse(req.dpo_configured)
        notas = " ".join(req.activity_ids.mapped(lambda a: str(a.note or "")))
        self.assertIn("Falta configurar el correo del responsable", notas)
        self.assertFalse(self.env["mail.mail"].sudo().search(
            [("email_to", "!=", False), ("subject", "like", req.name)]))

    def test_subusuario_pide_sobre_sus_datos(self):
        Member = self.env["shrimp.account.member"].sudo()
        portal = self.env.ref("base.group_portal")
        sub = self.env["res.users"].create({"name": "Empleado lpd", "login": "empleado.lpd",
                                            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        Member.create({"company_id": self.s["cam"].id, "user_id": self.s["u_cam"].id, "is_admin": True})
        Member.create({"company_id": self.s["cam"].id, "user_id": sub.id, "is_admin": False})
        req = self.Req._shrimp_from_portal(sub, "eliminacion", "mis datos")
        self.assertEqual(req.subject_scope, "own")
        self.assertEqual(req.account_id, self.s["cam"])
        req.action_start()
        req.action_anonymize()
        self.assertFalse(sub.with_context(active_test=False).active)
        self.assertTrue(sub.with_context(active_test=False).login.endswith("anonimizado.invalid"))
        self.assertTrue(self.s["cam"].active, "la empresa no se toca por la solicitud de un empleado")
        self.assertTrue(self.s["u_cam"].active)

    def test_recordatorios_de_plazo(self):
        self.ICP.set_param("shrimp.dpo_email", "dpo@empresa.test")
        req = self.Req._shrimp_from_portal(self.s["u_sem"], "acceso", "x")
        lejos = self.Req._shrimp_from_portal(self.s["u_lab2"], "acceso", "y")
        hoy = fields.Date.context_today(req)
        req.write({"deadline": hoy + timedelta(days=2)})
        n = self.Req._cron_reminders()
        self.assertEqual(n, 1, "solo la que vence dentro de los días de aviso")
        self.assertEqual(req.last_reminder_date, hoy)
        self.assertFalse(lejos.last_reminder_date)
        self.assertEqual(self.Req._cron_reminders(), 0, "una vez por día")
        req.write({"deadline": hoy - timedelta(days=1), "last_reminder_date": False})
        self.assertTrue(req.is_overdue)
        self.assertIn(req, self.Req.search([("is_overdue", "=", True)]))
        self.assertEqual(self.Req._cron_reminders(), 1)

    def test_cerrar_exige_respuesta(self):
        req = self.Req._shrimp_from_portal(self.s["u_sem"], "rectificacion", "x")
        req.action_start()
        with self.assertRaises(UserError):
            req.action_done()
        req.response = "<p>Corregimos el teléfono.</p>"
        req.action_done()
        self.assertEqual(req.state, "done")
        self.assertTrue(req.response_date)

    # ------------------------------------------------------------ portabilidad
    def test_descarga_de_datos(self):
        producto(self.env, self.s["lab"], nombre="Lote export lpd")
        self.authenticate("lab.seclpd", CLAVE)
        token = csrf_de(self.url_open("/my/data").text)
        r = self.url_open("/my/data/export", data={"csrf_token": token, "scope": "company"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("zip", r.headers.get("Content-Type", ""))
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        nombres = zf.namelist()
        self.assertIn("datos.json", nombres)
        self.assertIn("products.csv", nombres)
        datos = json.loads(zf.read("datos.json"))
        self.assertEqual(datos["user"][0]["login"], "lab.seclpd")
        self.assertEqual(datos["account"][0]["name"], self.s["lab"].name)
        self.assertTrue(any(p["name"] == "Lote export lpd" for p in datos["products"]))
        self.assertIn("api_keys", datos)
        self.assertNotIn("password", json.dumps(datos))
        req = self.Req.search([("user_id", "=", self.s["u_lab"].id), ("origin", "=", "self_export")])
        self.assertEqual(req.state, "done")
        self.assertEqual(req.request_type, "portabilidad")

    # ------------------------------------------------------------ anonimización
    def test_anonimizar_con_operaciones_abiertas_queda_en_espera(self):
        lote = producto(self.env, self.s["sem"], nombre="Nauplio lpd")
        lote.execute_purchase_flow(self.s["lab"], 3.0)
        req = self.Req._shrimp_from_portal(self.s["u_lab"], "eliminacion", "x")
        req.action_start()
        req.action_anonymize()
        self.assertEqual(req.state, "on_hold")
        self.assertIn("Operaciones abiertas", req.hold_reason)
        self.assertTrue(self.s["u_lab"].active)
        self.assertTrue(self.s["lab"].email.startswith("lab.sec"))

    def test_anonimizar_conserva_lo_fiscal(self):
        cam, u_cam = self.s["cam"], self.s["u_cam"]
        cam.sudo().write({"phone": "+593 99 999 9999", "street": "Km 5 vía a Durán"})
        Fac = self.env["shrimp.partner.facility"].sudo()
        fac = Fac.create({"partner_id": cam.id, "name": "Finca lpd", "latitude": -2.1,
                          "longitude": -79.9, "address": "Recinto X"})
        lote = producto(self.env, cam, nombre="Adulto lpd",
                        etapa="shrimp_marketplace.shrimp_stage_pl12")
        docs = self.env["shrimp.legal.document"].sudo()._shrimp_general_documents()
        acc = self.env["shrimp.legal.acceptance"].sudo()._shrimp_record(
            u_cam, docs.mapped("current_version_id"), "signup", ip="10.0.0.9",
            generate_receipt=False)
        nombre, ruc = cam.name, cam.vat_or_id
        req = self.Req._shrimp_from_portal(u_cam, "eliminacion", "x")
        req.action_start()
        req.action_anonymize()
        cam = cam.with_context(active_test=False)
        self.assertFalse(cam.active)
        self.assertEqual(cam.name, nombre, "el nombre fiscal se conserva")
        self.assertEqual(cam.vat_or_id, ruc, "el RUC/cédula se conserva")
        self.assertTrue(cam.email.endswith("anonimizado.invalid"))
        self.assertNotEqual(cam.phone, "+593 99 999 9999")
        self.assertFalse((cam.street or "").startswith("Km 5"))
        self.assertEqual(cam.farm_representante, "Anonimizado")
        u = u_cam.with_context(active_test=False)
        self.assertFalse(u.active)
        self.assertTrue(u.login.endswith("anonimizado.invalid"))
        self.assertFalse(fac.latitude)
        self.assertFalse(fac.address)
        self.assertFalse(lote.with_context(active_test=False).active)
        self.assertTrue(acc.exists(), "las aceptaciones legales se conservan")
        self.assertEqual(acc[0].ip, "10.0.0.9")
        self.assertTrue(req.anonymized_at)
        self.assertEqual(req.retention_until.year, fields.Date.today().year + 7)
        self.assertIn("Usuarios desactivados", req.anonymization_report)

    # ------------------------------------------------------------ retención
    def test_purga_simulacion_y_real(self):
        rechazada = self.s["lab2"]
        rechazada.sudo().write({"shrimp_account_state": "rejected"})
        linea = rechazada.shrimp_role_ids.filtered(lambda r: r.role == "laboratorio")
        hace = fields.Datetime.now() - timedelta(days=400)
        linea.sudo().with_context(shrimp_role_sync=True).write({"decision_date": hace})
        # Rechazada pero con operaciones: se conserva.
        con_ops = self.s["cam"]
        con_ops.sudo().write({"shrimp_account_state": "rejected"})
        con_ops.shrimp_role_ids.sudo().with_context(shrimp_role_sync=True).write({"decision_date": hace})
        producto(self.env, self.s["lab"], nombre="PL lpd").execute_purchase_flow(con_ops, 1.0)
        RL = self.env["shrimp.rate.limit"].sudo()
        viejo = RL.create({"key": "signup:1.2.3.4", "ts": hace})
        nuevo = RL.create({"key": "signup:1.2.3.5", "ts": fields.Datetime.now()})
        email = rechazada.email
        Run = self.env["shrimp.data.retention.run"].sudo()
        sim = Run._cron_retention()  # por defecto: simulación
        self.assertTrue(sim.dry_run)
        self.assertEqual(sim.accounts_count, 1)
        self.assertEqual(sim.accounts_skipped, 1)
        self.assertIn(rechazada.name, sim.report)
        self.assertIn("CONSERVADA", sim.report)
        self.assertEqual(rechazada.email, email, "la simulación no cambia nada")
        self.assertTrue(viejo.exists())
        self.ICP.set_param("shrimp_user_registry.lopdp_purge_dry_run", "False")
        real = Run._cron_retention()
        self.assertFalse(real.dry_run)
        self.assertTrue(rechazada.with_context(active_test=False).email.endswith("anonimizado.invalid"))
        self.assertFalse(viejo.exists())
        self.assertTrue(nuevo.exists())
        self.assertTrue(con_ops.email.startswith("cam.sec"))
        reg = self.Req.search([("origin", "=", "retention"), ("account_id", "=", rechazada.id)])
        self.assertEqual(reg.state, "done")
        # Idempotente: la siguiente pasada no la vuelve a contar.
        self.assertEqual(Run._cron_retention().accounts_count, 0)

    def test_borrador_de_privacidad(self):
        Doc = self.env["shrimp.legal.document"].sudo()
        pol = self.env.ref("shrimp_user_registry.legal_doc_privacy")
        publicada = pol.current_version_id
        texto = str(publicada.content)
        borrador = Doc._shrimp_lopdp_privacy_draft()
        self.assertTrue(borrador)
        self.assertEqual(borrador.state, "draft")
        self.assertIn("se anonimizan a los 12 meses", str(borrador.content))
        self.assertEqual(str(publicada.content), texto, "la publicada no se toca")
        self.assertFalse(Doc._shrimp_lopdp_privacy_draft(), "segunda vez: nada que hacer")

    def test_ajustes_avisan_sin_dpo(self):
        self.ICP.set_param("shrimp.dpo_email", False)
        vista = self.env.ref("shrimp_marketplace.res_config_settings_view_form_shrimp_lopdp")
        self.assertIn("shrimp_dpo_missing_alert", vista.arch_db)
        conf = self.env["res.config.settings"].create({})
        self.assertTrue(conf.shrimp_lopdp_purge_dry_run)
        self.assertEqual(conf.shrimp_lopdp_deadline_days, 15)
