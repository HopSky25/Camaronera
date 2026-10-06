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
            yield self.env.user.partner_id.shrimp_user_type or None
        except Exception:  # noqa: BLE001 - sin usuario/partner legible
            yield None
