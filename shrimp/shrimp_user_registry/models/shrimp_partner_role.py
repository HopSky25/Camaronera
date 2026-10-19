# -*- coding: utf-8 -*-
"""Perfiles de una cuenta (multi-rol).

Una misma empresa puede ser a la vez laboratorio y camaronera, o camaronera y
empacadora. Cada perfil es una línea de este modelo, con su propia
aprobación: la empacadora y el maquilador nacen pendientes y los aprueba la
administración rol por rol, sin tocar los demás perfiles de la cuenta.

res.partner.shrimp_user_type se conserva como el perfil ACTIVO ("Actuar
como"): media plataforma (plantillas, reglas, API) lo lee, y el sentido de
esas lecturas —"¿con qué rol está operando ahora?"— no cambia. Ver
res_partner.py (_shrimp_roles, _shrimp_has_role, _shrimp_can,
_shrimp_set_active_role, _shrimp_request_role).
"""
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ShrimpPartnerRole(models.Model):
    _name = "shrimp.partner.role"
    _description = "Perfil (rol) de una cuenta"
    _order = "partner_id, sequence, id"
    _rec_name = "role"

    partner_id = fields.Many2one(
        "res.partner", string="Cuenta", required=True, ondelete="cascade", index=True)
    role = fields.Selection(
        selection="_selection_role", string="Perfil", required=True, index=True)
    state = fields.Selection(
        [("pending", "Pendiente de aprobación"),
         ("approved", "Aprobado"),
         ("rejected", "Rechazado")],
        string="Estado", required=True, default="approved", index=True)
    sequence = fields.Integer(default=10)
    request_date = fields.Datetime(
        string="Solicitado el", default=fields.Datetime.now, readonly=True)
    decision_date = fields.Datetime(string="Decidido el", readonly=True, copy=False)
    decided_by_id = fields.Many2one(
        "res.users", string="Decidido por", readonly=True, copy=False)
    notes = fields.Text(string="Notas", groups="base.group_user")
    # D-18: rechazo con motivo obligatorio (se envía por correo al socio) y
    # un número limitado de nuevas solicitudes tras un rechazo.
    reject_reason = fields.Text(
        string="Motivo del rechazo", readonly=True, copy=False, groups="base.group_user",
        help="Se envía al socio por correo y se le muestra en «Mi cuenta».")
    rejection_count = fields.Integer(string="Rechazos", readonly=True, copy=False)
    # D-19: plazo de aprobación (48 horas hábiles por defecto). Aviso al
    # equipo que aprueba al llegar la solicitud; recordatorio y escalamiento
    # al vencer.
    sla_notified_at = fields.Datetime(string="Aviso al equipo", readonly=True, copy=False)
    sla_escalated_at = fields.Datetime(string="Escalado el", readonly=True, copy=False)
    rerequest_count = fields.Integer(
        string="Nuevas solicitudes", readonly=True, copy=False,
        help="Veces que la cuenta volvió a pedir el perfil después de un rechazo "
             "(máximo: parámetro shrimp_user_registry.role_max_rerequests, 3 por defecto).")

    is_active_profile = fields.Boolean(
        string="Perfil activo", compute="_compute_is_active_profile")
    requires_approval = fields.Boolean(
        string="Requiere aprobación", compute="_compute_requires_approval")
    partner_email = fields.Char(related="partner_id.email", string="Correo")
    partner_vat = fields.Char(related="partner_id.vat_or_id", string="RUC / Cédula")

    _shrimp_partner_role_uniq = models.Constraint(
        "unique(partner_id, role)",
        "La cuenta ya tiene ese perfil.",
    )

    @api.model
    def _selection_role(self):
        # Los mismos valores que shrimp_user_type, con lo que cada módulo le
        # agrega (empacadora, maquilador, verificador).
        return list(self.env["res.partner"]._fields["shrimp_user_type"].selection)

    @api.depends("role", "partner_id.shrimp_user_type")
    def _compute_is_active_profile(self):
        for rec in self:
            rec.is_active_profile = bool(rec.role) and rec.partner_id.shrimp_user_type == rec.role

    @api.depends("role")
    def _compute_requires_approval(self):
        Partner = self.env["res.partner"]
        for rec in self:
            rec.requires_approval = Partner._shrimp_type_can(rec.role, "requires_approval")

    @api.depends("role", "partner_id")
    def _compute_display_name(self):
        Partner = self.env["res.partner"]
        for rec in self:
            rec.display_name = "%s · %s" % (rec.partner_id.name or "",
                                            Partner._shrimp_type_label(rec.role))

    # ------------------------------------------------------------------
    # Reglas
    # ------------------------------------------------------------------
    @api.constrains("role", "partner_id", "state")
    def _check_exclusive(self):
        """Un perfil exclusivo (verificador) no convive con ningún otro.

        Quien certifica la calidad de un lote no puede ser parte del negocio
        que verifica: un verificador que además compra o vende tendría un
        conflicto de interés en cada inspección.
        """
        Partner = self.env["res.partner"]
        exclusivos = set(Partner._shrimp_types_with("exclusive_profile"))
        if not exclusivos:
            return
        for partner in self.mapped("partner_id"):
            roles = set(partner.sudo().shrimp_role_ids.filtered(
                lambda r: r.state != "rejected").mapped("role"))
            if len(roles) > 1 and roles & exclusivos:
                raise ValidationError(_(
                    "El perfil «%s» es exclusivo: una cuenta de verificación no "
                    "puede tener otros perfiles (conflicto de interés).") % ", ".join(
                    Partner._shrimp_type_label(r) for r in sorted(roles & exclusivos)))

    @api.model_create_multi
    def create(self, vals_list):
        Partner = self.env["res.partner"]
        for vals in vals_list:
            if not vals.get("state") and vals.get("role"):
                vals["state"] = ("pending" if Partner._shrimp_type_can(
                    vals["role"], "requires_approval") else "approved")
            if vals.get("state") in ("approved", "rejected") and not vals.get("decision_date"):
                vals.setdefault("decision_date", fields.Datetime.now())
        recs = super().create(vals_list)
        recs.filtered(lambda r: r.state == "pending")._shrimp_notify_approvers()
        return recs

    def write(self, vals):
        if "state" in vals and not self.env.context.get("shrimp_role_sync"):
            vals = dict(vals, decision_date=fields.Datetime.now(), decided_by_id=self.env.uid)
        if vals.get("state") == "pending":
            # Nueva solicitud (re-pedido tras un rechazo, vuelta a pendiente):
            # el plazo vuelve a empezar.
            vals = dict(vals, sla_notified_at=False, sla_escalated_at=False)
        antes = self.filtered(lambda r: r.state != "pending") if vals.get("state") == "pending" \
            else self.browse()
        res = super().write(vals)
        if antes:
            antes._shrimp_notify_approvers()
        if "state" in vals and not self.env.context.get("shrimp_role_sync"):
            # El estado del perfil ACTIVO se refleja en shrimp_account_state,
            # que es lo que consulta shrimp_is_operational() en todo el portal.
            for rec in self.sudo():
                partner = rec.partner_id
                if partner.shrimp_user_type == rec.role \
                        and partner.shrimp_account_state != rec.state:
                    partner.with_context(shrimp_role_sync=True).write(
                        {"shrimp_account_state": rec.state})
        return res

    def unlink(self):
        if not self.env.context.get("shrimp_role_sync"):
            for rec in self.sudo():
                partner = rec.partner_id
                if partner.shrimp_user_type != rec.role:
                    continue
                otros = partner.shrimp_role_ids.filtered(
                    lambda r: r.id not in self.ids and r.state == "approved")
                if not otros:
                    raise UserError(_(
                        "No se puede quitar el perfil activo «%s»: es el único "
                        "perfil aprobado de la cuenta.") % partner._shrimp_type_label(rec.role))
                partner.with_context(shrimp_role_sync=True).write({
                    "shrimp_user_type": otros[0].role,
                    "shrimp_account_state": otros[0].state,
                })
        return super().unlink()

    # ------------------------------------------------------------------
    # Aprobación por perfil (back-office)
    # ------------------------------------------------------------------
    def action_approve(self):
        self.write({"state": "approved"})
        for rec in self.sudo():
            rec.partner_id.message_post(
                body=_("Tu perfil «%s» fue aprobado. Ya puedes elegirlo en «Perfil» "
                       "y operar con él.") % rec.partner_id._shrimp_type_label(rec.role),
                partner_ids=rec.partner_id.ids, subtype_xmlid="mail.mt_comment")
        return True

    @api.model
    def _shrimp_max_rerequests(self):
        """D-18: cuántas veces se puede volver a pedir un perfil rechazado."""
        try:
            return max(0, int(self.env["ir.config_parameter"].sudo().get_param(
                "shrimp_user_registry.role_max_rerequests", "3")))
        except (TypeError, ValueError):
            return 3

    def _shrimp_rerequests_left(self):
        self.ensure_one()
        return max(0, self._shrimp_max_rerequests() - (self.rerequest_count or 0))

    def action_open_reject_wizard(self):
        """Botón «Rechazar» del back-office: pide el motivo (D-18)."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Rechazar perfil"),
            "res_model": "shrimp.partner.role.reject",
            "view_mode": "form",
            "target": "new",
            "context": {"default_role_ids": [(6, 0, self.ids)]},
        }

    def action_reject(self, reason=None):
        """Rechaza el perfil. El motivo es obligatorio (D-18): queda en la
        línea, en el chatter de la cuenta y se envía por correo al socio."""
        reason = (reason or self.env.context.get("shrimp_reject_reason") or "").strip()
        if not reason:
            raise UserError(_("Indica el motivo del rechazo: se le envía al socio."))
        for rec in self.sudo():
            rec.write({"state": "rejected", "reject_reason": reason,
                       "rejection_count": (rec.rejection_count or 0) + 1})
            # D-20: lo publicado con un perfil rechazado se retira.
            rec.partner_id._shrimp_withdraw_publications(roles=[rec.role], reason=reason)
            rec._shrimp_notify_rejection()
        return True

    def _shrimp_notify_rejection(self):
        """Correo al socio (y a los administradores de su cuenta) con el
        motivo y las solicitudes que le quedan."""
        self.ensure_one()
        socio = self.partner_id.sudo()
        etiqueta = socio._shrimp_type_label(self.role)
        quedan = self._shrimp_rerequests_left()
        if quedan:
            siguiente = _("Puedes corregirlo y volver a pedirlo desde «Mi cuenta › Perfiles» "
                          "(te quedan %s solicitudes).") % quedan
        else:
            siguiente = _("Ya no puedes volver a pedirlo desde el portal: escribe a la "
                          "administración si crees que es un error.")
        destinatarios = socio
        if "shrimp_member_ids" in socio._fields:
            destinatarios |= socio.shrimp_member_ids.filtered(
                lambda m: m.is_admin and m.active).mapped("user_id.partner_id")
        socio.message_post(
            body=Markup("<p>%s</p><p><b>%s</b> %s</p><p>%s</p>") % (
                _("Tu perfil «%s» fue rechazado.") % etiqueta, _("Motivo:"),
                self.reject_reason, siguiente),
            subject=_("Perfil «%s» rechazado") % etiqueta,
            partner_ids=destinatarios.filtered("email").ids,
            subtype_xmlid="mail.mt_comment")

    # ------------------------------------------------------------------
    # D-19: plazo de aprobación
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_sla_param(self, key, default):
        return self.env["ir.config_parameter"].sudo().get_param(
            "shrimp_user_registry.%s" % key, default)

    @api.model
    def _shrimp_sla_hours(self):
        try:
            return max(1, int(self._shrimp_sla_param("approval_sla_hours", "48")))
        except (TypeError, ValueError):
            return 48

    @api.model
    def _shrimp_business_hours_between(self, start, end):
        """Horas hábiles (lunes a viernes, día completo, hora de Ecuador)
        entre `start` y `end` (UTC naive)."""
        import pytz
        from datetime import timedelta
        if not start or not end or end <= start:
            return 0.0
        tz = pytz.timezone(self._shrimp_sla_param("approval_sla_tz", "America/Guayaquil"))
        a = pytz.utc.localize(start).astimezone(tz).replace(tzinfo=None)
        b = pytz.utc.localize(end).astimezone(tz).replace(tzinfo=None)
        total = 0.0
        cursor = a
        while cursor < b:
            fin_dia = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                          microsecond=0)
            tramo_fin = min(fin_dia, b)
            if cursor.weekday() < 5:
                total += (tramo_fin - cursor).total_seconds() / 3600.0
            cursor = tramo_fin
        return total

    @api.model
    def _shrimp_approver_partners(self):
        """Equipo que aprueba: los usuarios internos del grupo configurado
        (parámetro shrimp_user_registry.approver_group, por defecto el de
        administración) y los correos extra de approver_emails."""
        xmlid = self._shrimp_sla_param("approver_group", "base.group_system")
        grupo = self.env.ref(xmlid, raise_if_not_found=False) or self.env.ref("base.group_system")
        usuarios = self.env["res.users"].sudo().search(
            [("group_ids", "in", grupo.ids), ("share", "=", False), ("active", "=", True)]) \
            if "group_ids" in self.env["res.users"]._fields else grupo.sudo().users
        return usuarios.filtered(lambda u: u.email and not u._is_superuser()).mapped("partner_id")

    @api.model
    def _shrimp_extra_emails(self, key):
        texto = self._shrimp_sla_param(key, "") or ""
        return [c.strip() for c in texto.replace(";", ",").split(",") if "@" in c]

    def _shrimp_mail(self, subject, body, partners, emails=()):
        if not partners and not emails:
            return
        self.env["mail.mail"].sudo().create({
            "subject": subject,
            "body_html": body,
            "email_from": self.env.company.email_formatted or self.env.user.email_formatted,
            "recipient_ids": [(6, 0, partners.ids)],
            "email_to": ",".join(emails) or False,
            "auto_delete": True,
        })

    def _shrimp_notify_approvers(self):
        """Aviso al equipo que aprueba: hay una solicitud nueva (D-19)."""
        pendientes = self.sudo().filtered(lambda r: r.state == "pending" and not r.sla_notified_at)
        if not pendientes:
            return
        equipo = self._shrimp_approver_partners()
        extra = self._shrimp_extra_emails("approver_emails")
        horas = self._shrimp_sla_hours()
        for rec in pendientes:
            etiqueta = rec.partner_id._shrimp_type_label(rec.role)
            rec._shrimp_mail(
                _("Perfil por aprobar: %(c)s (%(r)s)") % {"c": rec.partner_id.name, "r": etiqueta},
                Markup("<p>%s</p>") % (_(
                    "La cuenta «%(c)s» pidió el perfil «%(r)s». Plazo de respuesta: %(h)s horas "
                    "hábiles (Aprobaciones › Perfiles por aprobar).") % {
                        "c": rec.partner_id.name, "r": etiqueta, "h": horas}),
                equipo, extra)
        pendientes.with_context(shrimp_role_sync=True).write(
            {"sla_notified_at": fields.Datetime.now()})

    @api.model
    def _cron_approval_sla(self):
        """Solicitudes pendientes que pasaron el plazo (horas hábiles):
        recordatorio al equipo y escalamiento (D-19). Una vez por solicitud."""
        ahora = fields.Datetime.now()
        horas = self._shrimp_sla_hours()
        vencidas = self.sudo().search([("state", "=", "pending"), ("sla_escalated_at", "=", False)])
        vencidas = vencidas.filtered(lambda r: self._shrimp_business_hours_between(
            r.request_date or r.create_date, ahora) >= horas)
        if not vencidas:
            return 0
        equipo = self._shrimp_approver_partners()
        extra = self._shrimp_extra_emails("approver_emails")
        escalar = self._shrimp_extra_emails("approval_escalation_emails")
        filas = Markup("").join(
            Markup("<li>%s · %s · %s</li>") % (
                r.partner_id.name, r.partner_id._shrimp_type_label(r.role),
                fields.Datetime.to_string(r.request_date)) for r in vencidas)
        cuerpo = Markup("<p>%s</p><ul>%s</ul>") % (_(
            "Estas solicitudes de perfil superaron el plazo de %s horas hábiles sin respuesta:")
            % horas, filas)
        vencidas._shrimp_mail(_("Recordatorio: %s perfiles por aprobar fuera de plazo") % len(vencidas),
                              cuerpo, equipo, extra)
        if escalar:
            vencidas._shrimp_mail(_("Escalamiento: %s perfiles por aprobar fuera de plazo")
                                  % len(vencidas), cuerpo, self.env["res.partner"], escalar)
        for rec in vencidas:
            rec.partner_id.message_post(body=_(
                "Solicitud del perfil «%s» fuera de plazo: recordatorio y escalamiento enviados.")
                % rec.partner_id._shrimp_type_label(rec.role))
        vencidas.with_context(shrimp_role_sync=True).write({"sla_escalated_at": ahora})
        return len(vencidas)

    def action_reset(self):
        self.write({"state": "pending"})
        return True
