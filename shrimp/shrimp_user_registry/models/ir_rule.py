# -*- coding: utf-8 -*-
from odoo import models


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _compute_domain_context_values(self):
        """Claves de la caché de dominios de ir.rule (se cachea por usuario):
        - el socio por el que actúa el usuario (subusuarios: las reglas usan
          user.shrimp_actor_partner_id);
        - su perfil ACTIVO, que es por usuario (CR-12);
        - los perfiles con los que el socio PUEDE actuar (activo o aprobado:
          _shrimp_has_role).
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
