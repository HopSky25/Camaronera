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
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not self.env.context.get("shrimp_role_sync"):
            vals = dict(vals, decision_date=fields.Datetime.now(), decided_by_id=self.env.uid)
        res = super().write(vals)
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

    def action_reject(self):
        self.write({"state": "rejected"})
        for rec in self.sudo():
            rec.partner_id.message_post(body=_("Perfil «%(r)s» rechazado por %(u)s.") % {
                "r": rec.partner_id._shrimp_type_label(rec.role), "u": self.env.user.name})
        return True

    def action_reset(self):
        self.write({"state": "pending"})
        return True
