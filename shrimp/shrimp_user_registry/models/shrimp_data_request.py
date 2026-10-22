"""Derechos del titular de datos personales (LOPDP Ecuador) — hueco #7.

Tres piezas:

1. ``shrimp.data.request``: cada solicitud de /my/data/request es un
   registro con tipo, solicitante, estado, plazo legal (15 días por defecto,
   Ajustes › CamaronMarket), responsable, respuesta y chatter. Un cron avisa
   antes del vencimiento y cuando vence.
2. Servicios sobre la cuenta (``res.partner``):
   * ``_shrimp_lopdp_export``: copia portable de los datos (ZIP con JSON y
     CSV), la que descarga el titular desde /my/data;
   * ``_shrimp_lopdp_open_operations``: operaciones abiertas que impiden
     anonimizar;
   * ``_shrimp_lopdp_anonymize``: anonimiza la cuenta conservando lo que la
     ley obliga a guardar.
3. ``shrimp.data.retention.run``: el cron de retención que cumple la
   Política de privacidad §5 (altas rechazadas y registros técnicos de
   acceso a los 12 meses), con modo simulación (dry-run) e informe.

Decisiones del usuario (auditoría de procesos, D-09, 14-10-2026):

* La solicitud de un SUBUSUARIO se refiere a sus propios datos personales;
  solo el administrador de la cuenta puede pedir la eliminación o la
  portabilidad de los datos de la EMPRESA.
* Anonimizar = se borran los datos personales de contacto y de identidad;
  se CONSERVAN 7 años (normativa tributaria) la razón social / nombre
  fiscal y el RUC o cédula del titular en compras, cobros y facturas, y las
  aceptaciones de documentos legales (con su IP), que son prueba.
* Con operaciones abiertas la anonimización NO se ejecuta: la solicitud
  queda «En espera» con el motivo «operaciones abiertas».
* La purga de 12 meses anonimiza las cuentas con el perfil activo
  RECHAZADO hace más de 12 meses y SIN ninguna operación (si operaron, rige
  la retención de 7 años) y borra los registros técnicos de acceso de más
  de 12 meses. Por defecto corre en simulación.
"""

import base64
import csv
import io
import json
import logging
import secrets
import zipfile
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.shrimp_user_registry.authz import shrimp_require_platform_admin

_logger = logging.getLogger(__name__)

REQUEST_TYPES = [
    ("acceso", "Acceso (copia de mis datos)"),
    ("rectificacion", "Rectificación"),
    ("eliminacion", "Eliminación"),
    ("oposicion", "Oposición"),
    ("portabilidad", "Portabilidad"),
]
STATES = [
    ("received", "Recibida"),
    ("in_progress", "En trámite"),
    ("on_hold", "En espera"),
    ("done", "Atendida"),
    ("rejected", "Denegada"),
]
OPEN_STATES = ("received", "in_progress", "on_hold")

# Parámetros (Ajustes › CamaronMarket › Protección de datos; los guarda
# shrimp_marketplace con _shrimp_param_specs). Clave -> valor por defecto.
P_DEADLINE = "shrimp_user_registry.lopdp_deadline_days"
P_REMINDER = "shrimp_user_registry.lopdp_reminder_days"
P_RETENTION_MONTHS = "shrimp_user_registry.lopdp_retention_months"
P_FISCAL_YEARS = "shrimp_user_registry.lopdp_fiscal_retention_years"
P_DRY_RUN = "shrimp_user_registry.lopdp_purge_dry_run"
DEFAULTS = {P_DEADLINE: 15, P_REMINDER: 3, P_RETENTION_MONTHS: 12, P_FISCAL_YEARS: 7,
            P_DRY_RUN: True}

ANON_DOMAIN = "anonimizado.invalid"
ANON_TEXT = "Anonimizado"

# Campos de res.partner que son datos personales de contacto o identidad
# (por nombre). Lo que NO está aquí se conserva: nombre / razón social, RUC o
# cédula (fiscal, 7 años), perfiles, capacidades, fechas.
_PERSONAL_KEYWORDS = (
    "email", "phone", "mobile", "telefono", "celular", "whatsapp", "street", "direccion",
    "zip", "representante", "contact_name", "cedula", "gps", "latitud", "longitud",
    "latitude", "longitude", "ubicacion", "website", "function", "comment",
)
# Nunca se tocan (fiscales, técnicos o normalizados que recalcula el modelo).
_KEEP_FIELDS = {
    "name", "vat", "vat_or_id", "x_vat_or_id_normalized", "x_name_normalized",
    "shrimp_razon_social", "shrimp_nombre_comercial", "lab_razon_social", "farm_razon_social",
    "emp_razon_social", "complete_name", "commercial_company_name", "company_name",
    "x_email_normalized", "email_normalized", "phone_sanitized", "email_formatted",
    "shrimp_privacy_ip",
}

# Operaciones abiertas: (modelo, campos de socio, dominio de "abierta", etiqueta).
# Cada módulo puede ampliar la lista con _shrimp_lopdp_open_operation_specs.
_OPEN_OPERATION_SPECS = [
    ("shrimp.transaction", ("buyer_partner_id", "seller_partner_id"),
     [("state", "not in", ("done", "cancel"))], "compras o ventas en curso"),
    ("shrimp.check.request", ("buyer_partner_id", "seller_partner_id"),
     [("state", "in", ("requested", "under_review"))], "solicitudes de chequeo abiertas"),
    ("shrimp.copack.order", ("client_partner_id", "copacker_partner_id"),
     [("state", "not in", ("closed", "cancelled"))], "órdenes de empaque (copacking) abiertas"),
    ("shrimp.copack.request", ("client_partner_id",),
     [("state", "in", ("draft", "published", "assigned"))], "solicitudes de copacking abiertas"),
    ("shrimp.harvest.commitment", ("packer_partner_id", "farmer_partner_id"),
     [("state", "in", ("sent", "accepted", "to_confirm"))], "reservas de cosecha vigentes"),
    ("shrimp.charge", ("partner_id",),
     [("state", "in", ("pending", "error"))], "cobros de la plataforma sin facturar"),
]

# Operaciones que obligan a conservar (si existió alguna, la cuenta operó).
_ANY_OPERATION_MODELS = [
    ("shrimp.transaction", ("buyer_partner_id", "seller_partner_id")),
    ("shrimp.charge", ("partner_id",)),
    ("shrimp.copack.order", ("client_partner_id", "copacker_partner_id")),
]


def lopdp_param(env, key):
    """Parámetro LOPDP con su valor por defecto (enteros o booleano)."""
    default = DEFAULTS[key]
    raw = env["ir.config_parameter"].sudo().get_param(key)
    if raw in (None, False, ""):
        return default
    if isinstance(default, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "si", "sí")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def dpo_email(env):
    return (env["ir.config_parameter"].sudo().get_param("shrimp.dpo_email") or "").strip() or False


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    if isinstance(value, models.BaseModel):
        return value.display_name if len(value) == 1 else value.mapped("display_name")
    return value


class ShrimpDataRequest(models.Model):
    _name = "shrimp.data.request"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _description = "Solicitud de derechos del titular (LOPDP)"
    _order = "deadline asc, id desc"

    name = fields.Char(string="Número", readonly=True, copy=False, default="/")
    request_type = fields.Selection(REQUEST_TYPES, string="Tipo", required=True, tracking=True)
    origin = fields.Selection(
        [("portal", "Portal (Mis datos)"), ("self_export", "Descarga directa desde Mis datos"),
         ("backoffice", "Registrada por la plataforma"),
         ("retention", "Purga de retención (Política §5)")],
        string="Origen", default="backoffice", required=True, readonly=True)
    user_id = fields.Many2one("res.users", string="Usuario solicitante", readonly=True,
                              index=True, ondelete="set null",
                              context={"active_test": False})
    requester_name = fields.Char(string="Solicitante", readonly=True,
                                 help="Nombre y correo al recibir la solicitud (se conserva aunque "
                                      "la cuenta se anonimice).")
    partner_id = fields.Many2one("res.partner", string="Contacto del solicitante", readonly=True,
                                 index=True, ondelete="set null", context={"active_test": False})
    account_id = fields.Many2one("res.partner", string="Cuenta (empresa)", readonly=True,
                                 index=True, ondelete="set null", context={"active_test": False})
    subject_scope = fields.Selection(
        [("own", "Sus propios datos personales"),
         ("company", "Datos de la cuenta (empresa) y propios")],
        string="Alcance", required=True, default="own", tracking=True,
        help="Decisión del usuario (D-09): un subusuario solo puede pedir sobre sus propios "
             "datos; el administrador de la cuenta, también sobre los de la empresa.")
    detail = fields.Text(string="Detalle del titular", readonly=True)
    state = fields.Selection(STATES, string="Estado", default="received", required=True,
                             tracking=True, index=True)
    received_at = fields.Datetime(string="Recibida el", default=fields.Datetime.now,
                                  readonly=True, required=True)
    deadline = fields.Date(string="Vence el", tracking=True, index=True,
                           help="Plazo legal de respuesta (LOPDP): días calendario desde la "
                                "recepción (Ajustes › CamaronMarket › Protección de datos).")
    days_left = fields.Integer(string="Días restantes", compute="_compute_days_left")
    is_overdue = fields.Boolean(string="Vencida", compute="_compute_days_left",
                                search="_search_is_overdue")
    responsible_id = fields.Many2one("res.users", string="Responsable", tracking=True,
                                     domain=[("share", "=", False)])
    response = fields.Html(string="Respuesta al titular", sanitize=True)
    response_date = fields.Datetime(string="Respondida el", readonly=True)
    hold_reason = fields.Text(string="Motivo de la espera", readonly=True)
    export_attachment_id = fields.Many2one("ir.attachment", string="Copia de los datos",
                                           readonly=True, ondelete="set null")
    anonymized_at = fields.Datetime(string="Anonimizada el", readonly=True)
    retention_until = fields.Date(
        string="Conservar datos fiscales hasta", readonly=True,
        help="Fin de la retención tributaria (7 años) de los identificadores mínimos que se "
             "conservaron al anonimizar.")
    anonymization_report = fields.Text(string="Informe de anonimización", readonly=True)
    last_reminder_date = fields.Date(string="Último recordatorio", readonly=True)
    dpo_configured = fields.Boolean(compute="_compute_dpo_configured")

    # ------------------------------------------------------------------
    @api.depends("deadline", "state")
    def _compute_days_left(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            if rec.deadline and rec.state in OPEN_STATES:
                rec.days_left = (rec.deadline - hoy).days
                rec.is_overdue = rec.deadline < hoy
            else:
                rec.days_left = 0
                rec.is_overdue = False

    def _search_is_overdue(self, operator, value):
        hoy = fields.Date.context_today(self)
        vencidas = [("state", "in", OPEN_STATES), ("deadline", "<", hoy)]
        positivo = (operator in ("=", "in") and bool(value)) or (operator == "!=" and not value)
        if positivo:
            return vencidas
        return ["|", ("state", "not in", OPEN_STATES), ("deadline", ">=", hoy)]

    def _compute_dpo_configured(self):
        ok = bool(dpo_email(self.env))
        for rec in self:
            rec.dpo_configured = ok

    @api.model_create_multi
    def create(self, vals_list):
        plazo = lopdp_param(self.env, P_DEADLINE)
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "/":
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code(
                    "shrimp.data.request") or "/"
            recibida = fields.Datetime.to_datetime(vals.get("received_at")) or fields.Datetime.now()
            vals.setdefault("deadline", fields.Date.to_date(recibida) + timedelta(days=plazo))
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Alta desde el portal
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_from_portal(self, user, request_type, detail, origin="portal"):
        """Crea la solicitud de `user`, avisa al responsable (correo en cola
        si hay DPO configurado; si no, actividad a los administradores con el
        aviso de que falta configurarlo) y la registra en el chatter de la
        cuenta."""
        user = user.sudo()
        empresa = user._shrimp_account_company() or user.partner_id
        admin = user._shrimp_is_account_admin()
        alcance = "company" if admin else "own"
        rec = self.sudo().create({
            "request_type": request_type,
            "origin": origin,
            "user_id": user.id,
            "partner_id": user.partner_id.id,
            "account_id": empresa.id if empresa else False,
            "subject_scope": alcance,
            "requester_name": "%s <%s>" % (user.name, user.login),
            "detail": (detail or "").strip()[:4000] or False,
        })
        etiqueta = dict(REQUEST_TYPES)[request_type]
        cuerpo = Markup("<p><b>%s</b></p><p>%s</p><p>%s</p>") % (
            _("Solicitud LOPDP %(n)s: %(t)s") % {"n": rec.name, "t": etiqueta},
            rec.detail or _("Sin detalle"),
            _("Solicitante: %(nombre)s (%(login)s). Alcance: %(a)s. Vence el %(v)s.") % {
                "nombre": user.name, "login": user.login,
                "a": dict(rec._fields["subject_scope"].selection)[alcance],
                "v": rec.deadline.strftime("%d/%m/%Y")},
        )
        if origin != "self_export":
            rec.message_post(body=cuerpo, message_type="comment", subtype_xmlid="mail.mt_note")
            if empresa:
                empresa.message_post(body=cuerpo, subject=_("Solicitud de derechos: %s") % etiqueta,
                                     message_type="comment", subtype_xmlid="mail.mt_note")
            rec._shrimp_notify_dpo(_("[CamaronMarket] Solicitud LOPDP %(n)s de %(nombre)s: %(t)s") % {
                "n": rec.name, "nombre": user.name, "t": etiqueta}, cuerpo)
        return rec

    def _shrimp_managers(self):
        """Usuarios internos que atienden las solicitudes: el grupo
        Administrador de CamaronMarket si existe; si no, Ajustes."""
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False) \
            or self.env.ref("base.group_system")
        return grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal())

    def _shrimp_notify_dpo(self, subject, body, activity_summary=None):
        """Correo al DPO (en cola) o, si no está configurado, actividad a los
        administradores avisando de que falta. Nunca una dirección inventada."""
        destino = dpo_email(self.env)
        if destino:
            self.env["mail.mail"].sudo().create({
                "subject": subject, "email_to": destino, "body_html": str(body),
                "auto_delete": True})
        for rec in self:
            usuarios = rec.responsible_id or rec._shrimp_managers()[:5]
            nota = body
            if not destino:
                nota = Markup("%s<p><b>%s</b></p>") % (body, _(
                    "Falta configurar el correo del responsable de datos personales (DPO) en "
                    "Ajustes › CamaronMarket › Protección de datos: hasta entonces nadie recibe "
                    "estas solicitudes por correo."))
            for usuario in usuarios:
                try:
                    rec.sudo().activity_schedule(
                        "mail.mail_activity_data_todo", date_deadline=rec.deadline,
                        user_id=usuario.id, summary=activity_summary or _("Atender solicitud LOPDP"),
                        note=nota)
                except Exception:  # noqa: BLE001 - el aviso nunca debe impedir registrar
                    _logger.warning("No se pudo crear la actividad LOPDP %s", rec.id, exc_info=True)

    # ------------------------------------------------------------------
    # Acciones del back-office
    # ------------------------------------------------------------------
    def _check_open(self):
        for rec in self:
            if rec.state not in OPEN_STATES:
                raise UserError(_("La solicitud %s ya está cerrada.") % rec.name)

    def action_start(self):
        shrimp_require_platform_admin(self.env)
        self._check_open()
        for rec in self:
            rec.write({"state": "in_progress", "responsible_id": rec.responsible_id.id or self.env.uid})
        return True

    def _shrimp_reply(self, state):
        for rec in self:
            texto = rec.response
            if not texto or not Markup(texto).striptags().strip():
                raise UserError(_("Escribe la respuesta al titular antes de cerrar la solicitud."))
            destinatario = rec.partner_id if rec.partner_id and rec.partner_id.email and \
                not (rec.partner_id.email or "").endswith(ANON_DOMAIN) else None
            cuerpo = Markup("<p><b>%s</b></p>%s") % (
                _("Respuesta a tu solicitud %(n)s (%(t)s)") % {
                    "n": rec.name, "t": dict(REQUEST_TYPES)[rec.request_type]}, texto)
            adjuntos = rec.export_attachment_id.ids if rec.export_attachment_id else []
            if destinatario:
                rec.message_post(body=cuerpo, partner_ids=destinatario.ids,
                                 message_type="comment", subtype_xmlid="mail.mt_comment",
                                 attachment_ids=adjuntos)
            else:
                rec.message_post(body=cuerpo, message_type="comment", subtype_xmlid="mail.mt_note")
            rec.write({"state": state, "response_date": fields.Datetime.now(),
                       "responsible_id": rec.responsible_id.id or self.env.uid})
            rec.activity_ids.sudo().action_feedback(feedback=_("Solicitud cerrada."))
        return True

    def action_done(self):
        shrimp_require_platform_admin(self.env)
        self._check_open()
        return self._shrimp_reply("done")

    def action_reject(self):
        shrimp_require_platform_admin(self.env)
        self._check_open()
        return self._shrimp_reply("rejected")

    def action_reopen(self):
        shrimp_require_platform_admin(self.env)
        for rec in self.filtered(lambda r: r.state in ("done", "rejected")):
            rec.write({"state": "in_progress"})
        return True

    def action_generate_export(self):
        """Genera la copia portable (ZIP) y la adjunta a la solicitud."""
        # Back-office (LOPDP): por RPC un usuario del portal no puede generar
        # ni anonimizar nada; el ZIP lleva los datos personales del titular.
        shrimp_require_platform_admin(self.env)
        for rec in self:
            if not rec.account_id and not rec.partner_id:
                raise UserError(_("La solicitud no tiene titular."))
            contenido, nombre = rec._shrimp_build_export()
            att = self.env["ir.attachment"].sudo().create({
                "name": nombre, "datas": base64.b64encode(contenido), "res_model": rec._name,
                "res_id": rec.id, "mimetype": "application/zip"})
            rec.write({"export_attachment_id": att.id})
            rec.message_post(body=_("Copia de los datos generada: %s") % nombre,
                             attachment_ids=att.ids, subtype_xmlid="mail.mt_note")
        return True

    def _shrimp_build_export(self):
        self.ensure_one()
        user = self.user_id.sudo().with_context(active_test=False)
        empresa = self.account_id or self.partner_id
        return empresa.sudo()._shrimp_lopdp_export_zip(
            user=user if user else None, scope=self.subject_scope)

    def action_anonymize(self):
        """Ejecuta la eliminación (anonimización). Con operaciones abiertas
        queda «En espera» con el motivo."""
        shrimp_require_platform_admin(self.env)
        self._check_open()
        for rec in self:
            if rec.request_type != "eliminacion":
                raise UserError(_("Solo se anonimiza en una solicitud de eliminación."))
            rec._shrimp_run_anonymization()
        return True

    def _shrimp_run_anonymization(self, dry_run=False):
        """Devuelve (hecho, informe:list[str])."""
        self.ensure_one()
        if self.subject_scope == "company" and self.account_id:
            objetivo = self.account_id.sudo()
            abiertas = objetivo._shrimp_lopdp_open_operations()
            if abiertas:
                motivo = _("Operaciones abiertas: %s. Se podrá anonimizar cuando se cierren.") % \
                    "; ".join(abiertas)
                if not dry_run:
                    self.write({"state": "on_hold", "hold_reason": motivo})
                    self.message_post(body=motivo, subtype_xmlid="mail.mt_note")
                return False, [motivo]
            informe = objetivo._shrimp_lopdp_anonymize(dry_run=dry_run)
        else:
            usuario = self.user_id.sudo().with_context(active_test=False)
            if not usuario:
                raise UserError(_("La solicitud no tiene usuario solicitante."))
            if usuario._shrimp_is_account_admin() and usuario.partner_id == self.account_id:
                raise UserError(_(
                    "El solicitante es el titular de la cuenta: cambia el alcance a «Datos de la "
                    "cuenta» para anonimizarla entera."))
            informe = self.env["res.partner"].sudo()._shrimp_lopdp_anonymize_users(
                usuario, dry_run=dry_run)
        if not dry_run:
            anios = lopdp_param(self.env, P_FISCAL_YEARS)
            self.write({
                "anonymized_at": fields.Datetime.now(),
                "retention_until": fields.Date.context_today(self) + relativedelta(years=anios),
                "anonymization_report": "\n".join(informe),
                "state": "in_progress" if self.state == "on_hold" else self.state,
                "hold_reason": False,
            })
            self.message_post(body=Markup("<p>%s</p><pre>%s</pre>") % (
                _("Anonimización ejecutada."), "\n".join(informe)), subtype_xmlid="mail.mt_note")
        return True, informe

    # ------------------------------------------------------------------
    # Recordatorios del plazo legal
    # ------------------------------------------------------------------
    @api.model
    def _cron_reminders(self):
        """Avisa al responsable (o a los administradores) cuando faltan los
        días de aviso para el vencimiento, el día del vencimiento y cada día
        mientras siga vencida. Una vez por día y solicitud."""
        hoy = fields.Date.context_today(self)
        aviso = lopdp_param(self.env, P_REMINDER)
        limite = hoy + timedelta(days=aviso)
        pendientes = self.sudo().search([("state", "in", OPEN_STATES), ("deadline", "<=", limite),
                                         "|", ("last_reminder_date", "=", False),
                                         ("last_reminder_date", "<", hoy)])
        for rec in pendientes:
            if rec.deadline < hoy:
                texto = _("La solicitud LOPDP %(n)s VENCIÓ el %(d)s (plazo legal).") % {
                    "n": rec.name, "d": rec.deadline.strftime("%d/%m/%Y")}
            else:
                texto = _("La solicitud LOPDP %(n)s vence el %(d)s (quedan %(k)s días).") % {
                    "n": rec.name, "d": rec.deadline.strftime("%d/%m/%Y"),
                    "k": (rec.deadline - hoy).days}
            rec.message_post(body=texto, subtype_xmlid="mail.mt_note")
            rec._shrimp_notify_dpo("[CamaronMarket] %s" % texto, Markup("<p>%s</p>") % texto,
                                   activity_summary=_("Plazo LOPDP: %s") % rec.name)
            rec.last_reminder_date = hoy
        return len(pendientes)


class ShrimpDataRetentionRun(models.Model):
    """Una ejecución del cron de retención (o su simulación) con su informe."""
    _name = "shrimp.data.retention.run"
    _description = "Purga de retención de datos (LOPDP)"
    _order = "id desc"

    name = fields.Char(string="Ejecución", readonly=True)
    run_at = fields.Datetime(string="Fecha", default=fields.Datetime.now, readonly=True)
    dry_run = fields.Boolean(string="Simulación", readonly=True)
    cutoff = fields.Datetime(string="Corte (anterior a)", readonly=True)
    accounts_count = fields.Integer(string="Cuentas rechazadas anonimizadas", readonly=True)
    accounts_skipped = fields.Integer(string="Cuentas rechazadas conservadas", readonly=True)
    logs_count = fields.Integer(string="Registros técnicos borrados", readonly=True)
    report = fields.Text(string="Informe", readonly=True)

    @api.model
    def _shrimp_retention_cutoff(self):
        meses = lopdp_param(self.env, P_RETENTION_MONTHS)
        return fields.Datetime.now() - relativedelta(months=meses)

    @api.model
    def _shrimp_rejected_candidates(self, cutoff):
        """Cuentas con el perfil activo rechazado desde antes de `cutoff`.
        Fecha del rechazo: la decisión del perfil activo (decision_date) o,
        sin ella, la última modificación de la cuenta."""
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        cuentas = Partner.search([("shrimp_account_state", "=", "rejected"),
                                  ("shrimp_user_type", "!=", False)])
        out = []
        ya = set(self.env["shrimp.data.request"].sudo().search(
            [("origin", "=", "retention"), ("anonymized_at", "!=", False)]).mapped("account_id").ids)
        for p in cuentas:
            if p.id in ya:
                continue
            linea = p.shrimp_role_ids.filtered(lambda r: r.role == p.shrimp_user_type)[:1]
            fecha = (linea.decision_date if linea and linea.decision_date else p.write_date)
            if fecha and fecha < cutoff:
                out.append(p)
        return out

    @api.model
    def _shrimp_log_specs(self):
        """[(modelo, campo fecha, etiqueta)] de registros técnicos de acceso
        que la Política de privacidad §5 conserva 12 meses. Los módulos
        amplían (shrimp_api: idempotencia y entregas de webhooks).
        Las aceptaciones legales (con su IP) NO están: se conservan como
        prueba (Política §5)."""
        return [
            ("res.users.log", "create_date", _("inicios de sesión (res.users.log)")),
            ("res.device.log", "last_activity", _("sesiones: IP y navegador (res.device.log)")),
            ("shrimp.rate.limit", "ts", _("limitador de intentos (IP)")),
        ]

    @api.model
    def _cron_retention(self, dry_run=None):
        if dry_run is None:
            dry_run = lopdp_param(self.env, P_DRY_RUN)
        return self._shrimp_run(dry_run=dry_run)

    @api.model
    def _shrimp_run(self, dry_run=True):
        cutoff = self._shrimp_retention_cutoff()
        meses = lopdp_param(self.env, P_RETENTION_MONTHS)
        lineas = [
            _("Purga de retención %(m)s — %(f)s") % {
                "m": _("SIMULACIÓN (no se cambió nada)") if dry_run else _("EJECUCIÓN REAL"),
                "f": fields.Datetime.now().strftime("%Y-%m-%d %H:%M UTC")},
            _("Corte: registros anteriores a %(c)s (%(n)s meses, Política de privacidad §5).") % {
                "c": cutoff.strftime("%Y-%m-%d"), "n": meses},
            "",
            _("1) Altas rechazadas (se anonimizan si no operaron nunca):"),
        ]
        hechas = conservadas = 0
        Request = self.env["shrimp.data.request"].sudo()
        for p in self._shrimp_rejected_candidates(cutoff):
            if p._shrimp_lopdp_has_operations():
                conservadas += 1
                lineas.append("   - %s [%s]: %s" % (p.name, p.vat_or_id or "-", _(
                    "CONSERVADA: tiene operaciones registradas (retención tributaria de 7 años)")))
                continue
            hechas += 1
            if dry_run:
                informe = p._shrimp_lopdp_anonymize(dry_run=True)
                lineas.append("   - %s [%s]: %s" % (p.name, p.vat_or_id or "-",
                                                    _("se anonimizaría")))
                lineas += ["       %s" % x for x in informe]
                continue
            req = Request.create({
                "request_type": "eliminacion", "origin": "retention", "account_id": p.id,
                "partner_id": p.id, "subject_scope": "company",
                "requester_name": _("Purga automática (Política de privacidad §5)"),
                "detail": _("Alta rechazada hace más de %s meses.") % meses,
                "state": "in_progress"})
            ok, informe = req._shrimp_run_anonymization()
            req.write({"state": "done", "response_date": fields.Datetime.now()})
            lineas.append("   - %s [%s]: %s" % (p.name, p.vat_or_id or "-", _("anonimizada")))
            lineas += ["       %s" % x for x in informe]
        if not hechas and not conservadas:
            lineas.append("   " + _("(ninguna)"))
        lineas += ["", _("2) Registros técnicos de acceso de más de %s meses:") % meses]
        borrados = 0
        for modelo, campo, etiqueta in self._shrimp_log_specs():
            if modelo not in self.env:
                continue
            Model = self.env[modelo].sudo().with_context(active_test=False)
            if campo not in Model._fields:
                continue
            viejos = Model.search([(campo, "<", cutoff)])
            borrados += len(viejos)
            lineas.append("   - %s: %s" % (etiqueta, len(viejos)))
            if viejos and not dry_run:
                viejos.unlink()
        lineas += ["", _("Se conservan: aceptaciones de documentos legales (con su IP) como prueba; "
                         "compras, cobros, facturas y certificados 7 años.")]
        run = self.sudo().create({
            "name": "%s %s" % (_("Simulación") if dry_run else _("Purga"),
                               fields.Datetime.now().strftime("%Y-%m-%d %H:%M")),
            "dry_run": dry_run, "cutoff": cutoff, "accounts_count": hechas,
            "accounts_skipped": conservadas, "logs_count": borrados, "report": "\n".join(lineas)})
        _logger.info("LOPDP retención (%s): %s cuentas, %s conservadas, %s registros técnicos",
                     "simulación" if dry_run else "real", hechas, conservadas, borrados)
        return run

    def action_run_dry(self):
        shrimp_require_platform_admin(self.env)
        run = self._shrimp_run(dry_run=True)
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": run.id,
                "view_mode": "form", "target": "current"}


class ResPartnerLopdp(models.Model):
    _inherit = "res.partner"

    # ------------------------------------------------------------------
    # Operaciones
    # ------------------------------------------------------------------
    def _shrimp_lopdp_partner_ids(self):
        self.ensure_one()
        socio = self.sudo().with_context(active_test=False)
        return (socio | socio.child_ids).ids

    @api.model
    def _shrimp_lopdp_open_operation_specs(self):
        return list(_OPEN_OPERATION_SPECS)

    def _shrimp_lopdp_open_operations(self):
        """['3 compras o ventas en curso', ...] de la cuenta (vacío = ninguna)."""
        self.ensure_one()
        ids = self._shrimp_lopdp_partner_ids()
        out = []
        for modelo, campos, dominio, etiqueta in self._shrimp_lopdp_open_operation_specs():
            if modelo not in self.env:
                continue
            Model = self.env[modelo].sudo().with_context(active_test=False)
            campos = [c for c in campos if c in Model._fields]
            if not campos or any(d[0] not in Model._fields for d in dominio):
                continue
            quien = ["|"] * (len(campos) - 1) + [(c, "in", ids) for c in campos]
            n = Model.search_count(quien + list(dominio))
            if n:
                out.append("%s %s" % (n, etiqueta))
        return out

    def _shrimp_lopdp_has_operations(self):
        self.ensure_one()
        ids = self._shrimp_lopdp_partner_ids()
        for modelo, campos in _ANY_OPERATION_MODELS:
            if modelo not in self.env:
                continue
            Model = self.env[modelo].sudo().with_context(active_test=False)
            campos = [c for c in campos if c in Model._fields]
            if not campos:
                continue
            quien = ["|"] * (len(campos) - 1) + [(c, "in", ids) for c in campos]
            if Model.search_count(quien, limit=1):
                return True
        return False

    def _shrimp_lopdp_users(self):
        """Usuarios de la cuenta: miembros y usuarios de sus contactos."""
        self.ensure_one()
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        users = Users.search([("partner_id", "in", self._shrimp_lopdp_partner_ids())])
        if "shrimp.account.member" in self.env:
            miembros = self.env["shrimp.account.member"].sudo().with_context(
                active_test=False).search([("company_id", "=", self.id)])
            users |= miembros.mapped("user_id")
        return users

    # ------------------------------------------------------------------
    # Exportación (acceso / portabilidad)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_lopdp_partner_fields(self):
        Partner = self.env["res.partner"]
        out = []
        for nombre, campo in Partner._fields.items():
            if not campo.store or campo.type in ("binary", "one2many", "many2many", "html") \
                    or nombre.startswith(("message_", "activity_", "website_message", "x_")) \
                    or nombre in ("id", "create_uid", "write_uid", "__last_update", "uuid_ref",
                                  "signup_token", "signup_type", "signup_expiration",
                                  "commercial_partner_id", "user_id", "company_id",
                                  "parent_id", "team_id", "property_account_payable_id",
                                  "property_account_receivable_id"):
                continue
            out.append(nombre)
        return out

    def _shrimp_lopdp_record_dict(self, record, field_names):
        fila = {}
        for nombre in field_names:
            try:
                fila[nombre] = _json_value(record[nombre])
            except Exception:  # noqa: BLE001 - un campo que no se puede leer no rompe la copia
                continue
        return fila

    def _shrimp_lopdp_export(self, user=None, scope="own"):
        """{sección: [filas]} con los datos del titular.

        scope «own»: el usuario y su contacto. scope «company»: además la
        cuenta (empresa), sus perfiles, usuarios, certificados y lo que añaden
        los demás módulos (_shrimp_lopdp_export_extra)."""
        self.ensure_one()
        cuenta = self.sudo().with_context(active_test=False)
        campos = self._shrimp_lopdp_partner_fields()
        datos = {"_info": [{
            "generated_at": fields.Datetime.now().isoformat(),
            "platform": "CamaronMarket",
            "scope": scope,
            "note": "Copia de los datos personales (LOPDP: acceso y portabilidad).",
        }]}
        if user:
            u = user.sudo()
            datos["user"] = [{"login": u.login, "name": u.name, "lang": u.lang, "tz": u.tz,
                              "create_date": _json_value(u.create_date),
                              "login_date": _json_value(u.login_date),
                              "active": u.active}]
            datos["contact"] = [self._shrimp_lopdp_record_dict(u.partner_id, campos)]
            Acc = self.env["shrimp.legal.acceptance"].sudo()
            datos["legal_acceptances"] = [{
                "document": a.document_name, "version": a.version_code, "accepted_at":
                _json_value(a.accepted_at), "ip": a.ip, "channel": a.channel,
                "content_sha256": a.content_hash}
                for a in Acc.search([("user_id", "=", u.id)])]
            Req = self.env["shrimp.data.request"].sudo()
            datos["data_requests"] = [{
                "number": r.name, "type": r.request_type, "state": r.state,
                "received_at": _json_value(r.received_at), "deadline": _json_value(r.deadline)}
                for r in Req.search([("user_id", "=", u.id)])]
        if scope == "company":
            datos["account"] = [self._shrimp_lopdp_record_dict(cuenta, campos)]
            datos["profiles"] = [{"role": r.role, "state": r.state,
                                  "decision_date": _json_value(r.decision_date)}
                                 for r in cuenta.shrimp_role_ids]
            datos["contacts"] = [self._shrimp_lopdp_record_dict(c, campos) for c in cuenta.child_ids]
            datos["users"] = [{"login": u.login, "name": u.name, "active": u.active,
                               "login_date": _json_value(u.login_date)}
                              for u in cuenta._shrimp_lopdp_users()]
            datos["certificates"] = [{
                "certificate": c.certificate_id.display_name, "issuer": c.issuer,
                "number": c.certificate_number, "issue_date": _json_value(c.issue_date),
                "expiry_date": _json_value(c.expiry_date), "status": c.status,
                "file": c.file_attachment_id.name if c.file_attachment_id else None}
                for c in cuenta.certificate_line_ids]
            Acc = self.env["shrimp.legal.acceptance"].sudo()
            datos["company_legal_acceptances"] = [{
                "document": a.document_name, "version": a.version_code,
                "accepted_by": a.user_name, "accepted_at": _json_value(a.accepted_at),
                "ip": a.ip, "channel": a.channel}
                for a in Acc.search([("commercial_partner_id", "=", cuenta.id)])]
            cuenta._shrimp_lopdp_export_extra(datos)
        return datos

    def _shrimp_lopdp_export_extra(self, datos):
        """Gancho: cada módulo añade sus secciones (productos, compras...)."""
        return datos

    def _shrimp_lopdp_export_zip(self, user=None, scope="own"):
        """(bytes del ZIP, nombre): datos.json + un CSV por sección + LEEME.txt."""
        self.ensure_one()
        datos = self._shrimp_lopdp_export(user=user, scope=scope)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("datos.json", json.dumps(datos, ensure_ascii=False, indent=2, default=str))
            for seccion, filas in datos.items():
                if not filas or not isinstance(filas, list):
                    continue
                columnas = []
                for fila in filas:
                    for k in fila:
                        if k not in columnas:
                            columnas.append(k)
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=columnas, extrasaction="ignore")
                w.writeheader()
                for fila in filas:
                    w.writerow({k: (json.dumps(v, ensure_ascii=False, default=str)
                                    if isinstance(v, (list, dict)) else v)
                                for k, v in fila.items()})
                zf.writestr("%s.csv" % seccion.strip("_"), out.getvalue().encode("utf-8-sig"))
            zf.writestr("LEEME.txt", (
                "Copia de tus datos en CamaronMarket (LOPDP: acceso y portabilidad).\n"
                "datos.json contiene todo; cada CSV es una sección (UTF-8).\n"
                "Los archivos adjuntos (certificados, fotos) no se incluyen: se listan por nombre "
                "y puedes descargarlos desde tu cuenta.\n").encode("utf-8"))
        nombre = "mis-datos-camaronmarket-%s.zip" % fields.Date.context_today(self).isoformat()
        return buf.getvalue(), nombre

    # ------------------------------------------------------------------
    # Anonimización
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_lopdp_personal_fields(self):
        """Campos de res.partner con datos personales de contacto/identidad."""
        Partner = self.env["res.partner"]
        out = []
        for nombre, campo in Partner._fields.items():
            if nombre in _KEEP_FIELDS or not campo.store or campo.related or campo.compute:
                continue
            if campo.type not in ("char", "text", "float", "html"):
                continue
            if any(k in nombre for k in _PERSONAL_KEYWORDS):
                out.append(nombre)
        return out

    def _shrimp_lopdp_scrub_vals(self, record, keep_name=True):
        """Valores que borran los datos personales de `record` (res.partner)."""
        vals = {}
        for nombre in self._shrimp_lopdp_personal_fields():
            campo = record._fields[nombre]
            valor = record[nombre]
            if not valor:
                continue
            if "email" in nombre:
                vals[nombre] = "anon-%s@%s" % (record.id, ANON_DOMAIN)
            elif campo.type == "float":
                vals[nombre] = 0.0
            elif campo.type == "html":
                vals[nombre] = False
            else:
                # Marcador (no vacío): los obligatorios por perfil
                # (representante, teléfono, ubicación) no rompen la validación.
                vals[nombre] = ANON_TEXT
        for img in ("image_1920",):
            if img in record._fields and record[img]:
                vals[img] = False
        if not keep_name:
            vals["name"] = _("Titular anonimizado #%s") % record.id
        return vals

    def _shrimp_lopdp_identity_attachments(self):
        """Adjuntos de identidad de la cuenta: fotos del registro y archivos
        de sus certificados (pueden llevar nombres, cédulas y firmas). De los
        certificados se conservan los metadatos (número, emisor, fechas)."""
        self.ensure_one()
        socio = self.sudo().with_context(active_test=False)
        atts = self.env["ir.attachment"].sudo().browse()
        for nombre, campo in socio._fields.items():
            if campo.type == "many2many" and campo.comodel_name == "ir.attachment":
                atts |= socio[nombre]
        atts |= socio.certificate_line_ids.mapped("file_attachment_id")
        return atts

    def _shrimp_lopdp_anonymize(self, dry_run=False):
        """Anonimiza la cuenta `self` (empresa titular). Devuelve el informe.

        Se borra: correo, teléfonos, dirección, representante y su cédula,
        ubicación y GPS, fotos, archivos de certificados, contactos y
        usuarios (desactivados con login aleatorio), y lo que añadan los
        módulos (claves API revocadas, webhooks, coordenadas de piscinas...).
        Se conserva (retención tributaria de 7 años): nombre/razón social y
        RUC o cédula de la cuenta, compras, cobros, facturas, certificados de
        trazabilidad y aceptaciones legales. La cuenta queda archivada y con
        el perfil fuera de operación."""
        self.ensure_one()
        cuenta = self.sudo().with_context(active_test=False, tracking_disable=True,
                                          shrimp_lopdp_anonymize=True)
        informe = []
        vals = cuenta._shrimp_lopdp_scrub_vals(cuenta, keep_name=True)
        informe.append(_("Cuenta %(n)s [%(v)s]: %(k)s campos personales borrados (%(c)s); se conservan "
                         "nombre fiscal y RUC/cédula.") % {
            "n": cuenta.name, "v": cuenta.vat_or_id or "-", "k": len(vals),
            "c": ", ".join(sorted(vals)) or "-"})
        contactos = cuenta.child_ids
        users = cuenta._shrimp_lopdp_users()
        informe.append(_("Contactos anonimizados: %s") % len(contactos.filtered(
            lambda c: c not in users.mapped("partner_id"))))
        atts = cuenta._shrimp_lopdp_identity_attachments()
        informe.append(_("Adjuntos de identidad borrados (fotos y archivos de certificados): %s")
                       % len(atts))
        if "shrimp.product" in self.env:
            Product = self.env["shrimp.product"].sudo().with_context(active_test=False)
            productos = Product.search([("seller_partner_id", "in", cuenta._shrimp_lopdp_partner_ids()),
                                        ("active", "=", True)])
            informe.append(_("Productos archivados (salen del catálogo): %s") % len(productos))
        else:
            productos = None
        informe += self.env["res.partner"]._shrimp_lopdp_anonymize_users(users, dry_run=True,
                                                                          header=False)
        informe += cuenta._shrimp_lopdp_anonymize_extra(dry_run=True)
        if dry_run:
            return informe
        # --- ejecución -------------------------------------------------
        for c in contactos.filtered(lambda c: c not in users.mapped("partner_id")):
            cv = cuenta._shrimp_lopdp_scrub_vals(c, keep_name=False)
            cv["name"] = _("Contacto anonimizado #%s") % c.id
            cv["active"] = False
            # active_test=True: el chequeo de Odoo «no archivar un contacto con
            # usuario activo» busca usuarios con el contexto del registro.
            c.with_context(active_test=True).write(cv)
        if productos:
            for prod in productos:
                try:
                    with self.env.cr.savepoint():
                        prod.write({"active": False})
                except Exception as e:  # noqa: BLE001
                    informe.append(_("No se pudo archivar el producto %(p)s: %(e)s") % {
                        "p": prod.display_name, "e": e})
        lineas = cuenta.certificate_line_ids
        if lineas:
            lineas.write({"file_attachment_id": False})
        atts.unlink()
        cuenta._shrimp_lopdp_anonymize_extra(dry_run=False)
        self.env["res.partner"]._shrimp_lopdp_anonymize_users(users, dry_run=False)
        if "comment" in cuenta._fields:
            vals["comment"] = Markup("<p>%s</p>") % _(
                "Datos personales anonimizados el %(f)s (LOPDP). Se conservan nombre fiscal y "
                "RUC/cédula por la retención tributaria (7 años).") % {
                "f": fields.Date.context_today(self).strftime("%d/%m/%Y")}
        vals["active"] = False
        cuenta.with_context(active_test=True).write(vals)
        cuenta.message_post(body=_("Cuenta anonimizada (LOPDP)."), subtype_xmlid="mail.mt_note")
        return informe

    def _shrimp_lopdp_anonymize_extra(self, dry_run=False):
        """Gancho de los demás módulos. Devuelve líneas de informe."""
        return []

    @api.model
    def _shrimp_lopdp_anonymize_users(self, users, dry_run=False, header=True):
        """Desactiva y anonimiza `users` (login aleatorio, contraseña nueva,
        contacto propio sin datos). Si el contacto del usuario es la cuenta
        titular, el contacto no se toca aquí (lo hace la anonimización de la
        cuenta). Borra sus registros técnicos de sesión."""
        users = users.sudo().with_context(active_test=False)
        informe = [_("Usuarios desactivados y anonimizados: %s") % len(users)]
        if dry_run:
            return informe
        for u in users:
            partner = u.partner_id
            titular = partner.shrimp_user_type and not partner.parent_id
            if "shrimp.api.key" in self.env:
                claves = self.env["shrimp.api.key"].sudo().with_context(active_test=False).search(
                    [("user_id", "=", u.id), ("revoked_at", "=", False)])
                if claves and hasattr(claves, "action_revoke"):
                    claves.action_revoke()
            if "shrimp.account.member" in self.env:
                self.env["shrimp.account.member"].sudo().with_context(active_test=False).search(
                    [("user_id", "=", u.id)]).write({"active": False})
            u.write({
                "login": "anon-%s-%s@%s" % (u.id, secrets.token_hex(3), ANON_DOMAIN),
                "password": secrets.token_urlsafe(24),
                "active": False,
            })
            for modelo, campo in (("res.users.log", "create_uid"), ("res.device.log", "user_id")):
                if modelo in self.env:
                    self.env[modelo].sudo().search([(campo, "=", u.id)]).unlink()
            if not titular:
                pv = self._shrimp_lopdp_scrub_vals(partner, keep_name=False)
                pv["name"] = _("Usuario anonimizado #%s") % u.id
                partner.with_context(tracking_disable=True).write(pv)
        return informe


# ---------------------------------------------------------------------------
# Política de privacidad: el texto publicado y lo que hace el sistema
# ---------------------------------------------------------------------------
PRIVACY_MARKER = "se anonimizan a los"
PRIVACY_SUMMARY = (
    "BORRADOR generado por el sistema (derechos LOPDP y retención) — REVISAR CON ABOGADO antes "
    "de publicar. §5: las altas rechazadas se anonimizan a los 12 meses (no se «eliminan» "
    "registros que la ley obliga a conservar) y se detalla qué registros técnicos de acceso se "
    "borran a los 12 meses. §6: descarga inmediata de la copia portable, alcance de la solicitud "
    "de un usuario de una cuenta de empresa y eliminación diferida si hay operaciones abiertas.")
_PRIVACY_REPLACEMENTS = [
    ("Las solicitudes de registro rechazadas se eliminan a los 12 meses.",
     "Los datos personales de las solicitudes de registro rechazadas se anonimizan a los 12 meses "
     "de la decisión; si la cuenta llegó a operar, se conservan solo el nombre fiscal y el RUC o "
     "cédula durante la retención tributaria."),
    ("Los registros técnicos de acceso se conservan 12 meses.",
     "Los registros técnicos de acceso (inicios de sesión, sesiones con IP y navegador, límites de "
     "intentos, registros de uso de la API y de entregas de webhooks) se borran a los 12 meses."),
    ("Respondemos en un máximo de 15 días.",
     "Respondemos en un máximo de 15 días. Desde esa misma página puedes descargar al momento una "
     "copia portable de tus datos (ZIP con JSON y CSV). Si usas una cuenta de empresa como usuario "
     "invitado, tu solicitud se refiere a tus propios datos; los datos de la empresa los pide su "
     "administrador. La eliminación anonimiza tus datos personales y se ejecuta cuando la cuenta "
     "no tiene operaciones abiertas (compras, verificaciones, empaques o cobros en curso)."),
]


class ShrimpLegalDocumentLopdp(models.Model):
    _inherit = "shrimp.legal.document"

    @api.model
    def _shrimp_lopdp_privacy_draft(self):
        """Crea (o completa) un BORRADOR de la Política de privacidad que
        describe lo que el sistema hace de verdad (retención y derechos). Nunca
        modifica una versión publicada. Devuelve el borrador o un recordset
        vacío si ya estaba al día o no hay política."""
        Version = self.env["shrimp.legal.document.version"].sudo()
        doc = self.sudo().search([("doc_type", "=", "privacy"), ("active", "=", True)], limit=1)
        if not doc or not doc.current_version_id:
            return Version.browse()
        borrador = doc.version_ids.filtered(lambda v: v.state == "draft")[:1]
        base = str((borrador or doc.current_version_id).content or "")
        if PRIVACY_MARKER in base:
            return Version.browse()
        texto = base
        for viejo, nuevo in _PRIVACY_REPLACEMENTS:
            texto = texto.replace(viejo, nuevo)
        if texto == base:
            _logger.warning("LOPDP: el texto de la Política de privacidad no tiene las frases "
                            "esperadas; no se generó el borrador.")
            return Version.browse()
        if borrador:
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + PRIVACY_SUMMARY).strip()})
            return borrador
        return Version.create({
            "document_id": doc.id, "name": Version._suggest_code(doc), "content": Markup(texto),
            "change_type": "minor", "change_summary": PRIVACY_SUMMARY, "state": "draft"})
