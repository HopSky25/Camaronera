# -*- coding: utf-8 -*-
"""Documentos legales versionados ("contrato virtual") y su aceptación.

- shrimp.legal.document: un documento (Política de privacidad, Términos y
  condiciones, Política de cookies, Contrato de uso — sección general — y un
  ANEXO DE PERFIL por cada tipo de perfil).
- shrimp.legal.document.version: las versiones de cada documento. Borrador ->
  publicada -> sustituida. Una versión publicada es INMUTABLE (texto, código,
  tipo de cambio): para cambiar el texto se crea una versión nueva («Nueva
  versión»). Al publicar se guarda el SHA-256 del texto.
  Tipo de cambio: MAYOR (bloquea el portal hasta aceptarla) o MENOR (aviso no
  bloqueante; se acepta cuando convenga).
- shrimp.legal.acceptance: historial de aceptaciones. INMUTABLE: nadie lo
  escribe ni lo borra (ni por ACL ni por sudo); solo lo crea el código
  controlado (_shrimp_record). Cada aceptación lleva su comprobante PDF
  (ir.attachment); si el PDF no se pudo generar en el momento, la aceptación
  se guarda igual y el cron lo reintenta.

Alcance de cada documento:
- "user": Privacidad, Términos, Cookies. Los acepta CADA usuario (también
  los subusuarios de una empresa): son datos personales de quien entra.
- "company": Contrato de uso y anexos de perfil. Los acepta el ADMINISTRADOR
  de la cuenta en nombre de la empresa; un subusuario no puede aceptarlos.
"""
import hashlib
import json
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import html2plaintext
from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG
from odoo.addons.shrimp_user_registry.authz import shrimp_require_platform_admin

_logger = logging.getLogger(__name__)

DOC_TYPES = [
    ("privacy", "Política de privacidad"),
    ("terms", "Términos y condiciones"),
    ("cookies", "Política de cookies"),
    ("contract", "Contrato de uso (sección general)"),
    ("annex", "Anexo de perfil"),
]
# Documentos que acepta cada persona; el resto, la empresa (su administrador).
USER_SCOPE_TYPES = ("privacy", "terms", "cookies")
# Ruta pública de cada documento general (los anexos: /legal/contract/<perfil>).
PUBLIC_PATHS = {
    "privacy": "/privacy",
    "terms": "/legal/terms",
    "cookies": "/legal/cookies",
    "contract": "/legal/contract",
}
CHANNELS = [
    ("signup", "Registro"),
    ("login_gate", "Pantalla de aceptación (acceso)"),
    ("profile", "Activación / alta de perfil"),
    ("banner", "Aviso en el portal"),
    ("invite", "Invitación de subusuario"),
    ("migration", "Migración"),
]
# Etiqueta visible de cada perfil en los textos legales y el registro. La
# clave técnica no cambia ("semillero"); el nombre comercial sí: Naupliero.
PROFILE_LABELS = {
    "semillero": "Naupliero",
    "laboratorio": "Laboratorio",
    "camaronera": "Camaronera",
    "empacadora": "Empacadora",
    "maquilador": "Maquilador (empaque)",
    "verificador": "Verificador",
}


def profile_label(env, role):
    if role in PROFILE_LABELS:
        return PROFILE_LABELS[role]
    return env["res.partner"]._shrimp_type_label(role)


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _role_selection(self):
    return list(self.env["res.partner"]._fields["shrimp_user_type"].selection)


class ShrimpLegalDocument(models.Model):
    _name = "shrimp.legal.document"
    _description = "Documento legal"
    _order = "sequence, id"

    name = fields.Char(string="Documento", required=True, translate=False)
    code = fields.Char(
        string="Código", required=True, index=True, copy=False,
        help="Identificador técnico estable: privacy, terms, cookies, contract, "
             "annex_<perfil>.")
    doc_type = fields.Selection(DOC_TYPES, string="Tipo", required=True)
    role = fields.Selection(
        selection=_role_selection, string="Perfil (anexo)",
        help="Solo para anexos: el perfil al que aplica.")
    scope = fields.Selection(
        [("user", "Cada usuario"), ("company", "La empresa (administrador)")],
        string="Lo acepta", compute="_compute_scope", store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(string="Notas internas")
    version_ids = fields.One2many(
        "shrimp.legal.document.version", "document_id", string="Versiones")
    current_version_id = fields.Many2one(
        "shrimp.legal.document.version", string="Versión vigente",
        compute="_compute_current_version")
    current_version_code = fields.Char(
        string="Vigente", compute="_compute_current_version")
    public_url = fields.Char(string="Página pública", compute="_compute_public_url")
    acceptance_count = fields.Integer(compute="_compute_acceptance_count", string="Aceptaciones")

    _code_uniq = models.Constraint("unique(code)", "Ya existe un documento con ese código.")

    @api.depends("doc_type")
    def _compute_scope(self):
        for rec in self:
            rec.scope = "user" if rec.doc_type in USER_SCOPE_TYPES else "company"

    @api.depends("version_ids.state", "version_ids.published_date")
    def _compute_current_version(self):
        Version = self.env["shrimp.legal.document.version"].sudo()
        for rec in self:
            v = Version.search([("document_id", "=", rec.id), ("state", "=", "published")],
                               order="published_date desc, id desc", limit=1) if rec.id else Version
            rec.current_version_id = v
            rec.current_version_code = v.name or False

    @api.depends("doc_type", "role")
    def _compute_public_url(self):
        for rec in self:
            if rec.doc_type == "annex":
                rec.public_url = "/legal/contract/%s" % (rec.role or "")
            else:
                rec.public_url = PUBLIC_PATHS.get(rec.doc_type) or False

    def _compute_acceptance_count(self):
        data = self.env["shrimp.legal.acceptance"].sudo()._read_group(
            [("document_id", "in", self.ids)], ["document_id"], ["__count"])
        cuenta = {d.id: n for d, n in data}
        for rec in self:
            rec.acceptance_count = cuenta.get(rec.id, 0)

    @api.constrains("doc_type", "role")
    def _check_role(self):
        for rec in self:
            if rec.doc_type == "annex" and not rec.role:
                raise ValidationError(_("Un anexo de perfil tiene que indicar el perfil."))
            if rec.doc_type != "annex" and rec.role:
                raise ValidationError(_("Solo los anexos llevan perfil."))

    def history_url(self):
        self.ensure_one()
        return "%s/history" % self.public_url

    def display_label(self):
        """Nombre para el usuario (los anexos con el nombre comercial del perfil)."""
        self.ensure_one()
        if self.doc_type == "annex":
            return _("Anexo de perfil: %s") % profile_label(self.env, self.role)
        return self.name

    # ------------------------------------------------------------------
    # Acciones de back-office
    # ------------------------------------------------------------------
    def action_new_version(self):
        self.ensure_one()
        borrador = self.version_ids.filtered(lambda v: v.state == "draft")[:1]
        if not borrador:
            base = self.current_version_id or self.version_ids.sorted(
                lambda v: (v.published_date or v.create_date, v.id))[-1:]
            borrador = self.env["shrimp.legal.document.version"].create({
                "document_id": self.id,
                "name": self.env["shrimp.legal.document.version"]._suggest_code(self),
                "content": base.content if base else False,
                "change_type": "minor",
            })
        return {
            "type": "ir.actions.act_window", "res_model": "shrimp.legal.document.version",
            "res_id": borrador.id, "view_mode": "form", "target": "current",
        }

    def action_view_acceptances(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "shrimp_user_registry.action_shrimp_legal_acceptance")
        action["domain"] = [("document_id", "=", self.id)]
        return action

    # ------------------------------------------------------------------
    # Qué documentos aplican y en qué estado está cada uno
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_general_documents(self):
        return self.sudo().search([("doc_type", "!=", "annex"), ("active", "=", True)])

    @api.model
    def _shrimp_annex(self, role):
        if not role:
            return self.sudo().browse()
        return self.sudo().search([("doc_type", "=", "annex"), ("role", "=", role),
                                   ("active", "=", True)], limit=1)

    @api.model
    def _shrimp_signup_documents(self, roles=None):
        """[(documento, versión vigente)] que se aceptan al registrarse:
        los generales y los anexos de `roles`. Solo los que tienen versión
        publicada."""
        docs = self._shrimp_general_documents()
        for role in roles or []:
            docs |= self._shrimp_annex(role)
        return [(d, d.current_version_id) for d in docs if d.current_version_id]

    def _shrimp_status(self, user, company):
        """Estado de este documento para `user` / `company`.

        Devuelve un dict: version (vigente), status:
          ok     aceptó la vigente
          minor  aceptó alguna anterior y lo que falta es un cambio MENOR
          major  hay una versión MAYOR posterior a lo último que aceptó (o
                 nunca aceptó y existe alguna versión mayor): BLOQUEA
          none   nunca aceptó y todas las versiones son menores: aviso
        """
        self.ensure_one()
        vigente = self.current_version_id
        if not vigente:
            return {"document": self, "version": vigente, "status": "ok"}
        Acc = self.env["shrimp.legal.acceptance"].sudo()
        dominio = [("document_id", "=", self.id)]
        if self.scope == "user":
            dominio.append(("user_id", "=", user.id))
        else:
            dominio.append(("commercial_partner_id", "=", company.id))
        aceptadas = Acc.search(dominio).mapped("version_id")
        if vigente in aceptadas:
            return {"document": self, "version": vigente, "status": "ok"}
        clave = lambda v: (v.published_date or fields.Datetime.now(), v.id)  # noqa: E731
        ultima = max(aceptadas, key=clave) if aceptadas else None
        posteriores = self.version_ids.filtered(
            lambda v: v.state in ("published", "superseded")
            and (ultima is None or clave(v) > clave(ultima)))
        if any(v.change_type == "major" for v in posteriores):
            estado = "major"
        else:
            estado = "minor" if ultima else "none"
        return {"document": self, "version": vigente, "status": estado}

    @api.model
    def _shrimp_pending(self, user, roles=None):
        """Documentos pendientes de `user` (usuario portal).

        {'major': [status...], 'minor': [status...], 'company_major': [...]}
        - major: los que el propio usuario puede aceptar y bloquean.
        - minor: los que puede aceptar cuando quiera (aviso).
        - company_major: documentos de la empresa que bloquean pero que solo
          el administrador puede aceptar (el subusuario no puede seguir).
        """
        res = {"major": [], "minor": [], "company_major": []}
        if not user or user._is_public() or user._is_internal() or user._is_superuser():
            return res
        user = user.sudo()
        company = user._shrimp_account_company()
        es_admin = user._shrimp_is_account_admin()
        docs = self._shrimp_general_documents()
        if roles is None:
            roles = company._shrimp_roles(include_pending=True) if company.shrimp_user_type else []
        for role in roles:
            docs |= self._shrimp_annex(role)
        for doc in docs:
            st = doc._shrimp_status(user, company)
            if st["status"] == "ok":
                continue
            if doc.scope == "company" and not es_admin:
                if st["status"] == "major":
                    res["company_major"].append(st)
                continue
            res["major" if st["status"] == "major" else "minor"].append(st)
        return res

    @api.model
    def _shrimp_banner_request(self):
        """Aviso del portal: documentos con cambios MENORES pendientes del
        usuario de la petición (y que no descartó en esta sesión)."""
        from odoo.http import request
        try:
            if not request or not request.env.user or request.env.user._is_public() \
                    or request.env.user._is_internal():
                return []
            ruta = request.httprequest.path or ""
            if "/my/legal/accept" in ruta:
                return []
            from odoo.addons.shrimp_user_registry.controllers.main import legal_pending, _DISMISS_KEY
            pend = legal_pending(request.env.user)
            descartadas = set(request.session.get(_DISMISS_KEY) or [])
            menores = [st for st in pend["minor"] if st["version"].id not in descartadas]
            return menores
        except Exception:  # noqa: BLE001 - el aviso nunca debe romper una página
            _logger.exception("shrimp: aviso de documentos legales")
            return []


class ShrimpLegalDocumentVersion(models.Model):
    _name = "shrimp.legal.document.version"
    _description = "Versión de un documento legal"
    _order = "document_id, published_date desc, id desc"
    _rec_name = "name"

    document_id = fields.Many2one(
        "shrimp.legal.document", string="Documento", required=True,
        ondelete="restrict", index=True)
    doc_type = fields.Selection(related="document_id.doc_type", store=True)
    role = fields.Selection(related="document_id.role", store=True)
    name = fields.Char(
        string="Versión", required=True,
        help="Código de versión que decide la administración, p. ej. 2026-10.")
    content = fields.Html(string="Texto", sanitize=True, sanitize_style=True)
    change_summary = fields.Text(
        string="Resumen de cambios",
        help="Qué cambia respecto de la versión anterior. Se muestra en el historial público "
             "y en la pantalla de aceptación.")
    change_type = fields.Selection(
        [("major", "Mayor: bloquea el portal hasta aceptarla"),
         ("minor", "Menor: aviso, se acepta cuando convenga")],
        string="Tipo de cambio", required=True, default="minor")
    state = fields.Selection(
        [("draft", "Borrador"), ("published", "Publicada"), ("superseded", "Sustituida")],
        string="Estado", default="draft", required=True, index=True, readonly=True)
    published_date = fields.Datetime(string="Publicada el", readonly=True, index=True)
    published_by_id = fields.Many2one("res.users", string="Publicada por", readonly=True)
    superseded_date = fields.Datetime(string="Sustituida el", readonly=True)
    content_hash = fields.Char(
        string="SHA-256 del texto", readonly=True, size=64,
        help="Huella del texto publicado: cualquier cambio del texto la cambia.")
    acceptance_count = fields.Integer(compute="_compute_acceptance_count", string="Aceptaciones")
    display_label = fields.Char(compute="_compute_display_label", string="Documento y versión")

    _version_uniq = models.Constraint(
        "unique(document_id, name)", "Ese documento ya tiene una versión con ese código.")

    PROTECTED = {"document_id", "name", "content", "change_type", "change_summary",
                 "content_hash", "published_date", "published_by_id", "state",
                 "superseded_date"}

    @api.depends("document_id", "name")
    def _compute_display_label(self):
        for rec in self:
            rec.display_label = "%s · %s" % (
                rec.document_id.display_label() if rec.document_id else "", rec.name or "")

    def _compute_acceptance_count(self):
        data = self.env["shrimp.legal.acceptance"].sudo()._read_group(
            [("version_id", "in", self.ids)], ["version_id"], ["__count"])
        cuenta = {v.id: n for v, n in data}
        for rec in self:
            rec.acceptance_count = cuenta.get(rec.id, 0)

    @api.model
    def _suggest_code(self, document):
        hoy = fields.Date.context_today(self)
        base = hoy.strftime("%Y-%m")
        codigo, n = base, 1
        existentes = set(document.version_ids.mapped("name"))
        while codigo in existentes:
            n += 1
            codigo = "%s.%d" % (base, n)
        return codigo

    @api.model_create_multi
    def create(self, vals_list):
        publicar = []
        for vals in vals_list:
            # Solo los datos semilla / la migración crean versiones ya
            # publicadas; el formulario siempre crea borradores.
            if vals.get("state") == "published":
                publicar.append(True)
                vals["state"] = "draft"
            else:
                publicar.append(False)
                vals["state"] = "draft"
            for clave in ("content_hash", "published_date", "published_by_id", "superseded_date"):
                vals.pop(clave, None)
        recs = super().create(vals_list)
        for rec, pub in zip(recs, publicar):
            if pub:
                rec._shrimp_publish()
        return recs

    def write(self, vals):
        if not self.env.context.get("shrimp_legal_internal"):
            for rec in self:
                if rec.state != "draft":
                    tocados = set(vals) & self.PROTECTED
                    if tocados:
                        raise UserError(_(
                            "La versión %(v)s de «%(d)s» ya está publicada: no se puede "
                            "modificar. Crea una versión nueva con «Nueva versión».") % {
                            "v": rec.name, "d": rec.document_id.name})
                elif {"state", "content_hash", "published_date", "published_by_id",
                      "superseded_date"} & set(vals):
                    raise UserError(_("El estado de una versión se cambia con «Publicar»."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get(MODULE_UNINSTALL_FLAG):
            for rec in self:
                if rec.state != "draft":
                    raise UserError(_(
                        "Una versión publicada o sustituida no se puede borrar: es la "
                        "prueba de lo que aceptaron los usuarios."))
        return super().unlink()

    def action_publish(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Solo se publica un borrador."))
            if not html2plaintext(rec.content or "").strip():
                raise UserError(_("La versión no tiene texto."))
            if not (rec.change_summary or "").strip():
                raise UserError(_("Escribe el resumen de cambios antes de publicar."))
            rec._shrimp_publish()
        return True

    def _shrimp_publish(self):
        self.ensure_one()
        ahora = fields.Datetime.now()
        anteriores = self.document_id.version_ids.filtered(
            lambda v: v.state == "published" and v.id != self.id)
        interna = self.with_context(shrimp_legal_internal=True)
        anteriores.with_context(shrimp_legal_internal=True).write(
            {"state": "superseded", "superseded_date": ahora})
        interna.write({
            "state": "published",
            "published_date": ahora,
            "published_by_id": self.env.uid,
            "content_hash": sha256_text(self.content or ""),
        })
        if self.change_type == "major":
            # D-36: una versión MAYOR se avisa por correo al administrador de
            # cada cuenta a la que aplica; las MENORES solo en el portal.
            self._shrimp_notify_major()
        return True

    def _shrimp_major_recipients(self):
        """Contactos (res.partner) de los administradores de las cuentas a las
        que aplica esta versión: todas las cuentas del marketplace o, si es un
        anexo, las que tienen ese perfil (aprobado o pendiente)."""
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        dominio = [("shrimp_user_type", "!=", False), ("parent_id", "=", False)]
        if self.document_id.role:
            dominio += Partner._shrimp_role_domain([self.document_id.role])
        empresas = Partner.search(dominio)
        Member = self.env["shrimp.account.member"].sudo()
        admins = Member.search([("company_id", "in", empresas.ids), ("is_admin", "=", True),
                                ("active", "=", True)])
        destinatarios = admins.mapped("user_id").filtered("active").mapped("partner_id")
        # Cuentas anteriores a los miembros: su dueño (usuario portal del socio).
        sin_miembros = empresas - admins.mapped("company_id")
        if sin_miembros:
            duenos = self.env["res.users"].sudo().search(
                [("partner_id", "in", sin_miembros.ids), ("share", "=", True)])
            destinatarios |= duenos.mapped("partner_id")
        return destinatarios.filtered("email")

    def _shrimp_notify_major(self):
        """Correo (en la cola de salida) a cada administrador de cuenta."""
        self.ensure_one()
        destinatarios = self._shrimp_major_recipients()
        if not destinatarios:
            return 0
        base = self.get_base_url()
        etiqueta = self.document_id.display_label()
        asunto = _("CamaronMarket: nueva versión de «%s» (debes aceptarla)") % etiqueta
        cuerpo = Markup(
            "<p>%s</p><p><b>%s</b> %s</p><p>%s</p><p><a href='%s/my/legal/accept'>%s</a></p>") % (
            _("Publicamos la versión %(v)s de «%(d)s». Es un cambio MAYOR: hasta que la acepte "
              "el administrador de la cuenta, el portal y la API quedan bloqueados para los "
              "usuarios de tu empresa.") % {"v": self.name, "d": etiqueta},
            _("Qué cambia:"), self.change_summary or "",
            _("Revísala y acéptala desde el portal:"), base, _("Revisar y aceptar"))
        remitente = self.env.company.email_formatted or self.env.user.email_formatted
        Mail = self.env["mail.mail"].sudo()
        Mail.create([{
            "subject": asunto,
            "body_html": cuerpo,
            "email_from": remitente,
            "recipient_ids": [(6, 0, [p.id])],
            "auto_delete": True,
        } for p in destinatarios])
        self.document_id.message_post(body=_(
            "Versión MAYOR %(v)s avisada por correo a %(n)s administradores de cuenta.") % {
                "v": self.name, "n": len(destinatarios)}) \
            if hasattr(self.document_id, "message_post") else None
        _logger.info("shrimp: versión MAYOR %s de %s avisada a %s administradores",
                     self.name, self.document_id.code, len(destinatarios))
        return len(destinatarios)

    def action_new_version(self):
        self.ensure_one()
        return self.document_id.action_new_version()

    def action_view_acceptances(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "shrimp_user_registry.action_shrimp_legal_acceptance")
        action["domain"] = [("version_id", "=", self.id)]
        return action

    def check_integrity(self):
        """True si el texto guardado sigue dando el mismo hash que al publicar."""
        self.ensure_one()
        return bool(self.content_hash) and sha256_text(self.content or "") == self.content_hash


class ShrimpLegalAcceptance(models.Model):
    _name = "shrimp.legal.acceptance"
    _inherit = ["shrimp.uuid.mixin"]
    _description = "Aceptación de un documento legal"
    _order = "accepted_at desc, id desc"
    _rec_name = "display_label"

    user_id = fields.Many2one("res.users", string="Usuario", ondelete="set null",
                              index=True, readonly=True)
    user_login = fields.Char(string="Usuario (login)", readonly=True)
    user_name = fields.Char(string="Nombre del usuario", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Contacto", ondelete="restrict",
                                 readonly=True, index=True)
    commercial_partner_id = fields.Many2one(
        "res.partner", string="Empresa", ondelete="restrict", readonly=True, index=True)
    company_name = fields.Char(string="Empresa (nombre)", readonly=True)
    company_vat = fields.Char(string="RUC / Cédula", readonly=True)
    is_account_admin = fields.Boolean(string="Aceptó el administrador", readonly=True)
    role = fields.Selection(selection=_role_selection, string="Perfil", readonly=True)
    document_id = fields.Many2one("shrimp.legal.document", string="Documento",
                                  ondelete="restrict", required=True, readonly=True, index=True)
    version_id = fields.Many2one("shrimp.legal.document.version", string="Versión",
                                 ondelete="restrict", required=True, readonly=True, index=True)
    version_code = fields.Char(string="Código de versión", readonly=True)
    document_name = fields.Char(string="Documento (nombre)", readonly=True)
    content_hash = fields.Char(string="SHA-256 del texto aceptado", readonly=True)
    accepted_at = fields.Datetime(string="Aceptado el", readonly=True, index=True)
    ip = fields.Char(string="IP", readonly=True)
    user_agent = fields.Char(string="Navegador (user agent)", readonly=True)
    channel = fields.Selection(CHANNELS, string="Canal", readonly=True, index=True)
    receipt_hash = fields.Char(
        string="Hash del comprobante", readonly=True,
        help="SHA-256 de los datos de esta aceptación (usuario, empresa, documento, "
             "versión, hash del texto, fecha, IP, canal).")
    receipt_attachment_id = fields.Many2one("ir.attachment", string="Comprobante PDF",
                                            readonly=True, ondelete="set null")
    receipt_state = fields.Selection(
        [("pending", "Pendiente"), ("done", "Generado"), ("error", "Error (se reintenta)"),
         ("migrated", "Migrado (sin comprobante)")],
        string="Comprobante", readonly=True, default="pending", index=True)
    receipt_attempts = fields.Integer(string="Intentos", readonly=True)
    receipt_error = fields.Char(string="Último error", readonly=True)
    display_label = fields.Char(compute="_compute_display_label")

    RECEIPT_FIELDS = {"receipt_attachment_id", "receipt_state", "receipt_attempts",
                      "receipt_error"}

    @api.depends("document_name", "version_code", "company_name")
    def _compute_display_label(self):
        for rec in self:
            rec.display_label = "%s %s · %s" % (rec.document_name or "", rec.version_code or "",
                                                rec.company_name or "")

    # ------------------------------------------------------------------
    # Inmutabilidad
    # ------------------------------------------------------------------
    def write(self, vals):
        # Solo el propio módulo completa el comprobante (y nada más).
        if self.env.context.get("shrimp_legal_receipt") and set(vals) <= self.RECEIPT_FIELDS:
            return super().write(vals)
        raise AccessError(_("El historial de aceptaciones es inmutable: no se puede modificar."))

    def unlink(self):
        if self.env.context.get(MODULE_UNINSTALL_FLAG):
            return super().unlink()
        raise AccessError(_("El historial de aceptaciones es inmutable: no se puede borrar."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("shrimp_legal_record"):
            raise AccessError(_("Las aceptaciones solo las registra la plataforma."))
        for vals in vals_list:
            vals["receipt_hash"] = self._shrimp_receipt_hash(vals)
        return super().create(vals_list)

    @api.model
    def _shrimp_receipt_hash(self, vals):
        datos = {k: vals.get(k) for k in (
            "uuid_ref", "user_id", "user_login", "commercial_partner_id", "company_vat",
            "role", "document_id", "version_id", "version_code", "content_hash", "ip",
            "user_agent", "channel")}
        datos["accepted_at"] = str(vals.get("accepted_at") or "")
        return hashlib.sha256(json.dumps(datos, sort_keys=True, default=str).encode()).hexdigest()

    # ------------------------------------------------------------------
    # Registro (único punto de creación)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_record(self, user, versions, channel, ip=None, user_agent=None,
                       company=None, generate_receipt=True):
        """Registra que `user` aceptó `versions` (versiones PUBLICADAS).

        Los documentos de la empresa (contrato, anexos) solo los acepta el
        administrador de la cuenta: si `user` no lo es, se rechaza.
        """
        user = user.sudo()
        company = (company or user._shrimp_account_company()).sudo()
        es_admin = user._shrimp_is_account_admin()
        ahora = fields.Datetime.now()
        vals_list = []
        for v in versions.sudo():
            if v.state != "published":
                raise ValidationError(_("La versión %s ya no está vigente.") % v.name)
            if v.document_id.scope == "company" and not es_admin:
                raise ValidationError(_(
                    "Solo el administrador de la cuenta puede aceptar «%s» en nombre de la "
                    "empresa.") % v.document_id.display_label())
            vals_list.append({
                "uuid_ref": self._generate_uuid_ref(),
                "user_id": user.id, "user_login": user.login, "user_name": user.name,
                "partner_id": user.partner_id.id,
                "commercial_partner_id": company.id,
                "company_name": company.name, "company_vat": company.vat_or_id or company.vat or False,
                "is_account_admin": es_admin,
                "role": v.document_id.role or False,
                "document_id": v.document_id.id, "version_id": v.id,
                "version_code": v.name, "document_name": v.document_id.display_label(),
                "content_hash": v.content_hash,
                "accepted_at": ahora,
                "ip": (ip or "")[:64] or False,
                "user_agent": (user_agent or "")[:400] or False,
                "channel": channel,
                "receipt_state": "migrated" if channel == "migration" else "pending",
            })
        recs = self.sudo().with_context(shrimp_legal_record=True).create(vals_list)
        # Compatibilidad: la ficha (y la API) siguen leyendo estos campos.
        priv = recs.filtered(lambda a: a.document_id.doc_type == "privacy")[:1]
        if priv:
            company.with_context(shrimp_role_sync=True).write({
                "shrimp_privacy_accepted_at": priv.accepted_at,
                "shrimp_privacy_version": (priv.version_code or "")[:16],
                "shrimp_privacy_ip": priv.ip or False,
            })
        if generate_receipt and channel != "migration":
            recs._shrimp_generate_receipts()
        return recs

    # ------------------------------------------------------------------
    # Comprobante PDF
    # ------------------------------------------------------------------
    def _shrimp_generate_receipts(self):
        report = self.env.ref("shrimp_user_registry.action_report_legal_acceptance",
                              raise_if_not_found=False)
        for rec in self.sudo():
            if rec.receipt_state not in ("pending", "error"):
                continue
            try:
                with self.env.cr.savepoint():
                    if not report:
                        raise UserError("falta el informe del comprobante")
                    contenido, formato = report.sudo()._render_qweb_pdf(
                        report.report_name, res_ids=rec.ids)
                    es_pdf = formato == "pdf"
                    att = self.env["ir.attachment"].sudo().create({
                        "name": "Comprobante-aceptacion-%s.%s" % (rec.uuid_ref, "pdf" if es_pdf else "html"),
                        "raw": contenido,
                        "mimetype": "application/pdf" if es_pdf else "text/html",
                        "res_model": rec._name, "res_id": rec.id,
                    })
                    rec.with_context(shrimp_legal_receipt=True).write({
                        "receipt_attachment_id": att.id, "receipt_state": "done",
                        "receipt_attempts": rec.receipt_attempts + 1, "receipt_error": False,
                    })
            except Exception as e:  # noqa: BLE001 - la aceptación se guarda igual
                _logger.warning("Comprobante de aceptación %s: %s", rec.uuid_ref, e)
                rec.with_context(shrimp_legal_receipt=True).write({
                    "receipt_state": "error", "receipt_attempts": rec.receipt_attempts + 1,
                    "receipt_error": str(e)[:250],
                })
        return True

    @api.model
    def _cron_generate_receipts(self, limit=50):
        pendientes = self.sudo().search([
            ("receipt_state", "in", ("pending", "error")), ("receipt_attempts", "<", 6)],
            order="id", limit=limit)
        pendientes._shrimp_generate_receipts()
        return True

    @api.model
    def _shrimp_migrate_legacy_privacy(self):
        """Socios con shrimp_privacy_version (aceptación anterior a 19.0.1.6.0):
        aceptación de Privacidad y Términos de esa versión, canal migración."""
        Doc = self.env["shrimp.legal.document"].sudo()
        docs = Doc.search([("doc_type", "in", ("privacy", "terms"))])
        socios = self.env["res.partner"].sudo().with_context(active_test=False).search(
            [("shrimp_privacy_version", "!=", False)])
        creadas = 0
        for socio in socios:
            usuarios = socio.user_ids.filtered(lambda u: u.share)
            if not usuarios:
                continue
            versiones = docs.version_ids.filtered(
                lambda v: v.name == socio.shrimp_privacy_version
                and v.state in ("published", "superseded"))
            for usuario in usuarios:
                for v in versiones:
                    if self.sudo().search_count([("user_id", "=", usuario.id),
                                                 ("version_id", "=", v.id)]):
                        continue
                    self.sudo()._shrimp_record_migrated(usuario, v, socio)
                    creadas += 1
        return creadas

    @api.model
    def _shrimp_record_migrated(self, user, version, socio):
        return self.sudo().with_context(shrimp_legal_record=True).create({
            "uuid_ref": self._generate_uuid_ref(),
            "user_id": user.id, "user_login": user.login, "user_name": user.name,
            "partner_id": user.partner_id.id,
            "commercial_partner_id": socio._shrimp_role_holder().id,
            "company_name": socio.name, "company_vat": socio.vat_or_id or socio.vat or False,
            "is_account_admin": True,
            "document_id": version.document_id.id, "version_id": version.id,
            "version_code": version.name, "document_name": version.document_id.display_label(),
            "content_hash": version.content_hash,
            "accepted_at": socio.shrimp_privacy_accepted_at or fields.Datetime.now(),
            "ip": socio.shrimp_privacy_ip or False,
            "channel": "migration", "receipt_state": "migrated",
        })

    def action_retry_receipt(self):
        shrimp_require_platform_admin(self.env)
        self._shrimp_generate_receipts()
        return True

    @api.private
    def receipt_content(self):
        """(bytes, mimetype, nombre) del comprobante; lo genera si falta.

        Privado: lee en sudo; el controlador comprueba de quién es."""
        self.ensure_one()
        rec = self.sudo()
        if not rec.receipt_attachment_id and rec.receipt_state in ("pending", "error"):
            rec._shrimp_generate_receipts()
        att = rec.receipt_attachment_id
        if not att:
            return None
        return att.raw, att.mimetype, att.name

    @api.private
    def receipt_text_html(self):
        """Texto exacto aceptado (para el PDF)."""
        self.ensure_one()
        return Markup(self.sudo().version_id.content or "")


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def unlink(self):
        # El comprobante de una aceptación es parte de la prueba: no se borra.
        if not self.env.context.get(MODULE_UNINSTALL_FLAG) and any(
                a.res_model == "shrimp.legal.acceptance" for a in self.sudo()):
            raise AccessError(_("El comprobante de una aceptación legal no se puede borrar."))
        return super().unlink()
