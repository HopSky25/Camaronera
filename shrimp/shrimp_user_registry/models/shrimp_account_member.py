# -*- coding: utf-8 -*-
"""Cuenta de empresa con administrador y subusuarios.

Quien crea la cuenta es el ADMINISTRADOR de la empresa (el socio comercial
con perfiles). Desde el portal (/my/team) invita subusuarios y decide, por
cada perfil de la empresa, qué puede hacer cada uno:

    ver       entrar con ese perfil (aparece en su selector «Perfil»)
    comprar   comprar / comprometer cosecha / pedir empaque
    vender    publicar productos, listas de precios, declarar cosecha,
              ofrecer empaque, registrar salidas, piscinas
    aprobar   emitir veredictos de verificación
    facturación  ver cobros y facturas de la plataforma

Además, un permiso de CUENTA (no de perfil):

    gestionar cuenta  editar la ficha de la empresa (Mi cuenta), sus
                      certificados (agregar / renovar / borrar), las claves
                      API, los webhooks y los técnicos de la verificadora
                      (hueco CR-12, decisión D-12)

«Ver» es solo lectura en todas partes: cada acción de escritura del portal y
de la API exige su permiso (tabla central en shrimp_permission_matrix.py,
comprobada en ir.http y en la API). El administrador lo tiene todo; lo que es
SOLO suyo (equipo, agregar perfiles, aceptar el contrato y los anexos de la
empresa) no se puede delegar.

El perfil activo es POR USUARIO (res.users.shrimp_active_profile): cada
miembro elige el suyo sin cambiárselo a los demás.

Los subusuarios actúan EN NOMBRE de la empresa (current_partner() devuelve la
empresa) y lo que pueden hacer es la intersección de lo que puede la empresa
con su perfil activo (matriz de capacidades) y los permisos que les dio el
administrador (res.partner._shrimp_can). No agregan perfiles ni aceptan el
contrato o los anexos de perfil: eso es del administrador. Cada uno acepta,
eso sí, la política de privacidad, los términos y la política de cookies.

Los técnicos de campo de una verificadora son subusuarios con el permiso
«aprobar» del perfil Verificador; siguen actuando con su propio contacto
(las verificaciones se les asignan a ellos).
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

PERMISSIONS = [
    ("view", "Ver"),
    ("buy", "Comprar"),
    ("sell", "Vender"),
    ("approve", "Aprobar"),
    ("billing", "Facturación"),
]
# Permiso de cuenta (no depende del perfil).
ACCOUNT_PERMISSIONS = [
    ("manage_account", "Gestionar cuenta"),
]


def _role_selection(self):
    return list(self.env["res.partner"]._fields["shrimp_user_type"].selection)


class ShrimpAccountMember(models.Model):
    _name = "shrimp.account.member"
    _inherit = ["shrimp.uuid.mixin"]
    _description = "Usuario de una cuenta de empresa"
    _order = "company_id, is_admin desc, id"
    _rec_name = "user_id"

    company_id = fields.Many2one(
        "res.partner", string="Empresa", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Usuario", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one(related="user_id.partner_id", string="Contacto")
    login = fields.Char(related="user_id.login", string="Correo / usuario")
    is_admin = fields.Boolean(string="Administrador", index=True)
    active = fields.Boolean(default=True)
    invited_by_id = fields.Many2one("res.users", string="Invitado por", readonly=True)
    invite_date = fields.Datetime(string="Invitado el", readonly=True)
    last_login = fields.Datetime(related="user_id.login_date", string="Último acceso")
    state = fields.Selection(
        [("invited", "Invitación pendiente"), ("active", "Activo"), ("inactive", "Desactivado")],
        string="Estado", compute="_compute_state")
    permission_ids = fields.One2many(
        "shrimp.account.member.permission", "member_id", string="Permisos por perfil")
    can_manage_account = fields.Boolean(
        string="Gestionar cuenta",
        help="Editar la ficha de la empresa, sus certificados, las claves API y los "
             "webhooks. El administrador siempre lo tiene.")

    _user_uniq = models.Constraint("unique(user_id)", "Ese usuario ya pertenece a una cuenta.")

    def _compute_state(self):
        for rec in self:
            if not rec.active or not rec.user_id.with_context(active_test=False).active:
                rec.state = "inactive"
            elif not rec.user_id.sudo().login_date and rec.invite_date:
                rec.state = "invited"
            else:
                rec.state = "active"

    @api.constrains("company_id", "is_admin", "active")
    def _check_one_admin(self):
        for company in self.mapped("company_id"):
            admins = self.sudo().search([("company_id", "=", company.id), ("is_admin", "=", True)])
            if len(admins) > 1:
                raise ValidationError(_("Una cuenta tiene un solo administrador."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        self.env.registry.clear_cache()  # reglas de acceso cacheadas por usuario
        return recs

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    # ------------------------------------------------------------------
    # Permisos
    # ------------------------------------------------------------------
    def _allowed_roles(self):
        """Perfiles de la empresa con los que este usuario puede actuar."""
        self.ensure_one()
        if self.is_admin:
            return self.company_id._shrimp_roles()
        mios = set(self.permission_ids.mapped("role"))
        return [r for r in self.company_id._shrimp_roles() if r in mios]

    def _allows(self, role, permission):
        self.ensure_one()
        if self.is_admin:
            return True
        if permission == "manage_account":
            return bool(self.can_manage_account)
        if not role:
            return False
        linea = self.permission_ids.filtered(lambda p: p.role == role)[:1]
        if not linea:
            return False
        if permission in (None, "view"):
            return True
        return bool(linea["can_%s" % permission])

    def _set_permissions(self, perms, manage_account=None):
        """perms: {rol: set(permisos)}. 'view' es implícito si hay alguno.
        `manage_account` (True/False) fija el permiso de cuenta; None no lo toca."""
        self.ensure_one()
        if manage_account is not None and bool(manage_account) != self.can_manage_account:
            self.sudo().write({"can_manage_account": bool(manage_account)})
        Perm = self.env["shrimp.account.member.permission"].sudo()
        roles_empresa = set(self.company_id._shrimp_roles(include_pending=True))
        self.permission_ids.sudo().unlink()
        for role, permisos in perms.items():
            if role not in roles_empresa or not permisos:
                continue
            Perm.create({
                "member_id": self.id, "role": role,
                "can_buy": "buy" in permisos, "can_sell": "sell" in permisos,
                "can_approve": "approve" in permisos, "can_billing": "billing" in permisos,
            })
        self.env.registry.clear_cache()

    def _shrimp_permissions_summary(self):
        """Texto corto de los permisos (constancia en el chatter)."""
        self.ensure_one()
        etiquetas = dict(PERMISSIONS)
        Partner = self.env["res.partner"]
        partes = []
        for role, permisos in sorted(self._permissions_dict().items()):
            partes.append("%s: %s" % (Partner._shrimp_type_label(role), ", ".join(
                etiquetas[p] for p, _l in PERMISSIONS if p in permisos)))
        if self.can_manage_account:
            partes.append(_("gestionar cuenta"))
        return "; ".join(partes) or _("solo acceso, sin perfiles")

    def _permissions_dict(self):
        self.ensure_one()
        res = {}
        for p in self.permission_ids:
            res[p.role] = {"view"} | {k for k in ("buy", "sell", "approve", "billing")
                                      if p["can_%s" % k]}
        return res

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_set_active(self, activo):
        for rec in self.sudo():
            if rec.is_admin and not activo:
                raise UserError(_("No se puede desactivar al administrador: transfiere antes "
                                  "la administración."))
            usuario = rec.user_id.with_context(active_test=False)
            # Sin active_test=False: Odoo no deja archivar un contacto con un
            # usuario activo y, con ese contexto, contaría también al archivado.
            partner = usuario.partner_id.with_context(active_test=True)
            if activo:
                if partner != rec.company_id:
                    partner.active = True
                usuario.active = True
            else:
                usuario.active = False
                if partner != rec.company_id:
                    partner.active = False
            rec.active = activo
        return True

    def _shrimp_full_permissions(self):
        """Todo lo delegable: los cinco permisos en cada perfil de la empresa
        (lo que tenía el administrador, menos lo que es solo suyo)."""
        self.ensure_one()
        return {r: {"view", "buy", "sell", "approve", "billing"}
                for r in self.company_id._shrimp_roles(include_pending=True)}

    def action_make_admin(self, previous_permissions=None, previous_manage_account=None):
        """Transfiere la administración de la cuenta a este usuario.

        El administrador anterior queda como subusuario con los permisos que
        elija quien transfiere (`previous_permissions` = {rol: {permisos}},
        `previous_manage_account`); por defecto (None), los que tenía menos
        lo que es solo del administrador: todos los permisos de perfil y
        «gestionar cuenta» (decisión D-37)."""
        self.ensure_one()
        rec = self.sudo()
        if not rec.active:
            raise UserError(_("Solo un usuario activo puede ser administrador."))
        actual = self.sudo().search([("company_id", "=", rec.company_id.id),
                                     ("is_admin", "=", True), ("id", "!=", rec.id)])
        if actual:
            actual.write({"is_admin": False})
            for m in actual:
                perms = m._shrimp_full_permissions() if previous_permissions is None \
                    else previous_permissions
                manage = True if previous_manage_account is None else bool(previous_manage_account)
                m._set_permissions(perms, manage_account=manage)
                m.company_id.message_post(body=_(
                    "%(u)s deja de administrar la cuenta y queda como usuario con: %(p)s.") % {
                        "u": m.user_id.name, "p": m._shrimp_permissions_summary()})
        rec.write({"is_admin": True, "can_manage_account": False})
        rec.permission_ids.unlink()
        rec.company_id.message_post(body=_("Administración de la cuenta transferida a %s.")
                                    % rec.user_id.name)
        return True

    # ------------------------------------------------------------------
    # Altas
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_ensure(self, user, company, is_admin=False, permissions=None):
        """Miembro de `user` en `company` (lo crea si no existe)."""
        user = user.sudo()
        miembro = self.sudo().with_context(active_test=False).search(
            [("user_id", "=", user.id)], limit=1)
        if miembro:
            return miembro
        if is_admin and self.sudo().search_count([("company_id", "=", company.id),
                                                   ("is_admin", "=", True)]):
            is_admin = False
        miembro = self.sudo().create({"company_id": company.id, "user_id": user.id,
                                      "is_admin": is_admin})
        if permissions and not is_admin:
            miembro._set_permissions(permissions)
        return miembro


    @api.model
    def _shrimp_migrate_existing_users(self):
        """Usuarios portal anteriores al sistema de subusuarios (migración)."""
        Users = self.env["res.users"].sudo().with_context(active_test=True)
        portal = self.env.ref("base.group_portal")
        campo = "group_ids" if "group_ids" in Users._fields else "groups_id"
        usuarios = Users.search([(campo, "in", portal.ids), ("shrimp_member_ids", "=", False)])
        creados = 0
        for u in usuarios:
            p = u.partner_id
            if p.shrimp_user_type:
                self._shrimp_ensure(u, p, is_admin=True)
                creados += 1
            elif p.parent_id and "shrimp_is_field_tech" in p._fields and p.shrimp_is_field_tech:
                empresa = p.parent_id._shrimp_role_holder()
                if empresa.shrimp_user_type:
                    self._shrimp_ensure(u, empresa, is_admin=False,
                                        permissions={"verificador": {"view", "approve"}})
                    creados += 1
        return creados


class ShrimpAccountMemberPermission(models.Model):
    _name = "shrimp.account.member.permission"
    _description = "Permisos de un subusuario en un perfil"
    _order = "member_id, id"

    member_id = fields.Many2one("shrimp.account.member", required=True, ondelete="cascade",
                                index=True)
    role = fields.Selection(selection=_role_selection, string="Perfil", required=True)
    can_buy = fields.Boolean(string="Comprar")
    can_sell = fields.Boolean(string="Vender")
    can_approve = fields.Boolean(string="Aprobar")
    can_billing = fields.Boolean(string="Facturación")

    _member_role_uniq = models.Constraint("unique(member_id, role)",
                                          "Ese perfil ya tiene permisos para el usuario.")
