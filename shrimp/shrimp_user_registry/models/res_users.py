# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# Permisos que se pueden exigir a un usuario de una cuenta de empresa
# (shrimp.account.member): los de perfil, el de cuenta y «admin».
SHRIMP_PERMISSION_LABELS = {
    "view": "Ver", "buy": "Comprar", "sell": "Vender", "approve": "Aprobar",
    "billing": "Facturación", "manage_account": "Gestionar cuenta", "admin": "Administrador",
}


def _role_selection(self):
    return list(self.env["res.partner"]._fields["shrimp_user_type"].selection)


class ResUsers(models.Model):
    _inherit = "res.users"

    # Perfil activo POR USUARIO (CR-12, decisión D-12): cada miembro de la
    # cuenta elige con qué perfil opera sin cambiárselo a los demás. Se guarda
    # aquí (persiste entre sesiones y dispositivos); vacío = el perfil por
    # defecto de la empresa (res.partner.shrimp_user_type). Lo que vale de
    # verdad es shrimp_active_role: el elegido si sigue siendo válido (perfil
    # aprobado de la cuenta y permitido a este usuario) o el de la empresa.
    shrimp_active_profile = fields.Selection(
        selection=_role_selection, string="Perfil activo elegido", copy=False,
        help="Perfil con el que este usuario eligió operar («Actuar como»). Vacío: el "
             "perfil por defecto de su empresa.")
    shrimp_active_role = fields.Selection(
        selection=_role_selection,
        string="Perfil activo", compute="_compute_shrimp_active_role",
        inverse="_inverse_shrimp_active_role")

    # Cuenta de empresa (administrador / subusuarios): ver shrimp_account_member.py
    shrimp_member_ids = fields.One2many(
        "shrimp.account.member", "user_id", string="Cuenta de empresa")
    # Socio en cuyo nombre actúa el usuario. Lo usan las reglas de acceso
    # (user.shrimp_actor_partner_id) en lugar de user.partner_id: para el
    # dueño de la cuenta y para los técnicos de campo es su propio contacto
    # (como siempre); para un subusuario, la empresa.
    shrimp_actor_partner_id = fields.Many2one(
        "res.partner", string="Actúa en nombre de", compute="_compute_shrimp_actor_partner")

    @api.depends("shrimp_active_profile", "partner_id.shrimp_user_type",
                 "partner_id.parent_id.shrimp_user_type")
    def _compute_shrimp_active_role(self):
        for user in self:
            user.shrimp_active_role = user.sudo()._shrimp_current_role() or False

    def _inverse_shrimp_active_role(self):
        for user in self:
            if user.shrimp_active_role:
                actor = user.sudo()._shrimp_actor_partner()
                actor.with_user(user).sudo()._shrimp_set_active_role(user.shrimp_active_role)

    # ------------------------------------------------------------------
    # Perfil activo por usuario
    # ------------------------------------------------------------------
    def _shrimp_manages_profile_of(self, holder):
        """True si el perfil activo de `holder` (la empresa) para ESTE usuario
        se guarda en el usuario: es miembro de la cuenta (admin, subusuario o
        técnico) o su dueño en una cuenta sin miembros."""
        self.ensure_one()
        if not holder or self._is_public() or not self.share:
            return False
        return self.sudo()._shrimp_account_company() == holder

    def _shrimp_roles_for_me(self, holder):
        """Perfiles de `holder` con los que ESTE usuario puede actuar: los
        aprobados de la cuenta y, si es subusuario, solo los que le dio el
        administrador."""
        self.ensure_one()
        roles = holder._shrimp_roles()
        miembro = self.sudo()._shrimp_subuser_member()
        if miembro and miembro.company_id == holder:
            permitidos = set(miembro._allowed_roles())
            roles = [r for r in roles if r in permitidos]
        return roles

    def _shrimp_profile_for(self, holder):
        """Perfil con el que este usuario actúa en `holder`, o None si el
        perfil activo de `holder` no depende de este usuario.

        El elegido (shrimp_active_profile) si sigue valiendo; si no, el de la
        empresa si se lo permiten; si tampoco, el primero que tenga."""
        self.ensure_one()
        if not self._shrimp_manages_profile_of(holder):
            return None
        user = self.sudo()
        por_defecto = holder.shrimp_user_type
        elegido = user.shrimp_active_profile
        sub = user._shrimp_subuser_member()
        if not elegido and not sub:
            return por_defecto or None
        mios = user._shrimp_roles_for_me(holder)
        if elegido and elegido in mios:
            return elegido
        if por_defecto and (por_defecto in mios or not sub):
            return por_defecto
        return mios[0] if mios else (por_defecto or None)

    def _shrimp_current_role(self):
        """Perfil con el que opera este usuario ahora (o False)."""
        self.ensure_one()
        user = self.sudo()
        if user._is_public():
            return False
        holder = user._shrimp_account_company()
        if not holder:
            return False
        return user._shrimp_profile_for(holder) or holder.shrimp_user_type or False

    # ------------------------------------------------------------------
    # Permisos del usuario (CR-12): «ver» es solo lectura
    # ------------------------------------------------------------------
    def _shrimp_restricted_member(self):
        """El miembro NO administrador de una cuenta (sus permisos limitan lo
        que puede hacer) o vacío: administradores, dueños de cuentas sin
        miembros, usuarios internos y el superusuario no tienen límite."""
        self.ensure_one()
        if self._is_public() or not self.share or self._is_superuser():
            return self.env["shrimp.account.member"]
        return self.sudo()._shrimp_subuser_member()

    def _shrimp_has_perm(self, perm, role=None, scope="active"):
        """True si el usuario puede hacer `perm`.

        perm: view / buy / sell / approve / billing (de perfil),
        manage_account (de cuenta), admin (solo el administrador) o none;
        una tupla = cualquiera de ellos.
        role: el perfil con el que se actúa (por defecto, el ACTIVO del
        usuario). scope="any": en alguno de sus perfiles (acciones sobre un
        registro que trae su propio perfil, que el modelo vuelve a comprobar).
        """
        self.ensure_one()
        if isinstance(perm, (list, tuple, set, frozenset)):
            return any(self._shrimp_has_perm(p, role=role, scope=scope) for p in perm)
        if self._is_public():
            return False
        miembro = self._shrimp_restricted_member()
        if not miembro or not perm or perm == "none":
            return True
        if perm == "admin":
            return False
        if perm == "manage_account":
            return miembro._allows(None, "manage_account")
        if role:
            return miembro._allows(role, perm)
        if scope == "any":
            return any(miembro._allows(r, perm) for r in miembro._allowed_roles())
        return miembro._allows(self._shrimp_current_role(), perm)

    def _shrimp_require_perm(self, perm, role=None, scope="active"):
        """AccessError si el usuario no tiene `perm` (ver _shrimp_has_perm)."""
        self.ensure_one()
        if self._shrimp_has_perm(perm, role=role, scope=scope):
            return True
        raise AccessError(self._shrimp_perm_message(perm, role))

    @api.model
    def _shrimp_perm_message(self, perm, role=None):
        perms = perm if isinstance(perm, (list, tuple, set, frozenset)) else [perm]
        if "admin" in perms:
            return _("Esta acción la hace el administrador de la cuenta.")
        nombres = " / ".join("«%s»" % SHRIMP_PERMISSION_LABELS.get(p, p) for p in perms)
        if role:
            return _("Tu usuario no tiene el permiso %(p)s en el perfil «%(r)s». Pídeselo al "
                     "administrador de tu cuenta.") % {
                         "p": nombres, "r": self.env["res.partner"]._shrimp_type_label(role)}
        return _("Tu usuario no tiene el permiso %s. Pídeselo al administrador de tu "
                 "cuenta.") % nombres

    def _shrimp_denied_route_patterns(self):
        """Expresiones regulares de las rutas que este usuario NO puede usar
        (para que la página desactive esos formularios y enlaces). Vacío si
        no tiene límites."""
        self.ensure_one()
        if not self._shrimp_restricted_member():
            return {"post": [], "get": []}
        from .shrimp_permission_matrix import (
            SHRIMP_GET_PERMISSIONS, SHRIMP_ROUTE_PERMISSIONS, shrimp_route_regex)
        res = {"post": [], "get": []}
        for clave, tabla in (("post", SHRIMP_ROUTE_PERMISSIONS), ("get", SHRIMP_GET_PERMISSIONS)):
            for ruta, (perm, scope) in tabla.items():
                if not self._shrimp_has_perm(perm, scope=scope):
                    res[clave].append(shrimp_route_regex(ruta))
        return res

    def _compute_shrimp_actor_partner(self):
        for user in self:
            user.shrimp_actor_partner_id = user.sudo()._shrimp_actor_partner()

    # ------------------------------------------------------------------
    # Cuenta de empresa
    # ------------------------------------------------------------------
    def _shrimp_member(self):
        """Miembro activo de una cuenta de empresa (o vacío)."""
        self.ensure_one()
        return self.sudo().shrimp_member_ids.filtered("active")[:1]

    def _shrimp_account_company(self):
        """La empresa (socio con perfiles) de este usuario."""
        self.ensure_one()
        miembro = self._shrimp_member()
        if miembro:
            return miembro.company_id
        partner = self.sudo().partner_id
        return partner._shrimp_role_holder() if partner else partner

    def _shrimp_is_account_admin(self):
        """True si administra su cuenta: miembro administrador o, sin cuenta
        de miembros (usuarios anteriores), el dueño del socio con perfiles."""
        self.ensure_one()
        miembro = self._shrimp_member()
        if miembro:
            return miembro.is_admin
        partner = self.sudo().partner_id
        return bool(partner) and partner._shrimp_role_holder() == partner

    def _shrimp_subuser_member(self):
        """Miembro NO administrador (con permisos limitados) o vacío."""
        self.ensure_one()
        miembro = self._shrimp_member()
        return miembro if miembro and not miembro.is_admin else miembro.browse()

    def _shrimp_actor_partner(self):
        self.ensure_one()
        partner = self.sudo().partner_id
        miembro = self._shrimp_member()
        if not miembro or partner == miembro.company_id:
            return partner
        # Técnico de campo: actúa con su contacto (se le asignan a él).
        if "shrimp_is_field_tech" in partner._fields and partner.shrimp_is_field_tech:
            return partner
        return miembro.company_id
