# -*- coding: utf-8 -*-
from odoo import models


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _compute_domain_context_values(self):
        """Hay reglas que dependen del perfil ACTIVO del usuario
        (user.partner_id.shrimp_user_type) y otras de los perfiles con los que
        PUEDE actuar (activo o aprobado: _shrimp_has_role, p. ej. la bandeja
        del maquilador). ir.rule cachea el dominio por usuario: sin estas
        claves, al cambiar de perfil o al aprobarle uno nuevo seguiría
        aplicando el dominio anterior hasta que se vaciara la caché."""
        yield from super()._compute_domain_context_values()
        try:
            socio = self.env.user.partner_id
            activo = socio.shrimp_user_type or None
            perfiles = tuple(socio._shrimp_roles()) if socio else ()
        except Exception:  # noqa: BLE001 - sin usuario/partner legible
            activo, perfiles = None, ()
        yield activo
        yield perfiles
