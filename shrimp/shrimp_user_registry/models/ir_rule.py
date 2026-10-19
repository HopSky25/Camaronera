# -*- coding: utf-8 -*-
from odoo import models


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _compute_domain_context_values(self):
        """Hay reglas que dependen del perfil ACTIVO del usuario
        (user.partner_id.shrimp_user_type). ir.rule cachea el dominio por
        usuario: sin esta clave, al cambiar de perfil seguiría aplicando el
        dominio del perfil anterior hasta que se vaciara la caché."""
        yield from super()._compute_domain_context_values()
        try:
            user = self.env.user
            actor = user.sudo()._shrimp_actor_partner()
            # Subusuarios: las reglas usan user.shrimp_actor_partner_id.
            yield actor.id or None
            # Perfil activo POR USUARIO (CR-12).
            yield user.sudo()._shrimp_current_role() or actor.shrimp_user_type or None
        except Exception:  # noqa: BLE001 - sin usuario/partner legible
            yield None
