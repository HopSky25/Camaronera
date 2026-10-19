# -*- coding: utf-8 -*-
from odoo import models


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _shrimp_request_role(self):
        """Perfil con el que actúa ESTA petición, si el código de servidor lo
        fijó en el contexto (la API, con la cabecera X-Shrimp-Role ya
        validada), o False. Se vuelve a validar aquí: el contexto puede venir
        de fuera (call_kw), así que solo vale un perfil aprobado de la cuenta
        y permitido al usuario."""
        rol = self.env.context.get("shrimp_acting_role")
        if not rol or not isinstance(rol, str):
            return False
        try:
            user = self.env.user
            actor = user.sudo()._shrimp_actor_partner()
            if not actor or not actor._shrimp_has_role(rol):
                return False
            if not actor.with_user(user).sudo()._shrimp_user_allows(None, rol):
                return False
        except Exception:  # noqa: BLE001 - sin usuario/partner legible
            return False
        return rol

    def _eval_context(self):
        """`shrimp_role` en los dominios: el perfil de la petición (o False).
        Las reglas que dependen del perfil usan
        ``(shrimp_role or user.shrimp_active_role)``: la app trabaja con un
        perfil por petición sin cambiar el perfil activo del portal."""
        ctx = super()._eval_context()
        ctx["shrimp_role"] = self._shrimp_request_role()
        return ctx

    def _compute_domain_context_values(self):
        """Claves de la caché de dominios de ir.rule (se cachea por usuario):
        - el socio por el que actúa el usuario (subusuarios: las reglas usan
          user.shrimp_actor_partner_id);
        - su perfil ACTIVO, que es por usuario (CR-12);
        - los perfiles con los que el socio PUEDE actuar (activo o aprobado:
          _shrimp_has_role);
        - el perfil de la petición (shrimp_role), si lo hay.
        Sin ellas, al cambiar de perfil o al aprobarle uno nuevo seguiría
        aplicando el dominio anterior hasta que se vaciara la caché."""
        yield from super()._compute_domain_context_values()
        try:
            user = self.env.user
            actor = user.sudo()._shrimp_actor_partner()
            actor_id = actor.id or None
            activo = user.sudo()._shrimp_current_role() or actor.shrimp_user_type or None
            perfiles = tuple(actor._shrimp_roles()) if actor else ()
        except Exception:  # noqa: BLE001 - sin usuario/partner legible
            actor_id, activo, perfiles = None, None, ()
        yield actor_id
        yield activo
        yield perfiles
        yield self._shrimp_request_role() or None
