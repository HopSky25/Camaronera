"""Claves de API y estado de la cuenta (hueco #6 de la auditoría de procesos).

* No se emiten claves a una cuenta cuyo perfil activo está pendiente o
  rechazado (portal y cualquier otra vía, porque se comprueba en
  ``api_create_key``).
* Al RECHAZAR una cuenta (o su perfil activo) se DESACTIVAN las claves de
  todos sus usuarios: quedan «inactivas» (no revocadas) para que el
  administrador pueda reactivarlas si la cuenta se aprueba después.
* La puerta de cada petición (pendiente = solo lectura, rechazada = 403,
  documentos MAYORES sin aceptar = 403) vive en controllers/framework.py.

Decisión del usuario: la suspensión de publicación por strikes
(shrimp.seller.suspension) NO desactiva las claves: solo impide crear y publicar
productos por la API, para que el vendedor pueda seguir despachando lo que tiene en curso.
"""

import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ShrimpApiKeyAccountGate(models.Model):
    _inherit = "shrimp.api.key"

    @api.model
    def _shrimp_account_block_reason(self, user):
        """Texto del motivo por el que `user` no puede tener claves nuevas, o False."""
        user = user.sudo()
        partner = user.shrimp_actor_partner_id or user.partner_id
        if not partner:
            return False
        holder = partner._shrimp_role_holder()
        if "shrimp_suspended" in holder._fields and holder.shrimp_suspended:
            # D-20: una cuenta suspendida no crea claves nuevas.
            return _("Tu cuenta está suspendida: no puede crear claves de API mientras dure "
                     "la suspensión.")
        state = holder.shrimp_account_state
        if state == "pending":
            return _("Tu cuenta está pendiente de aprobación: podrás crear claves de API cuando "
                     "la plataforma la apruebe.")
        if state == "rejected":
            return _("Tu cuenta fue rechazada por la plataforma: no puede tener claves de API.")
        return False

    @api.model
    def api_create_key(self, user, name, scope_codes, expires_at=None,
                       ip_allowlist=False, allowed_origins=False):
        motivo = self._shrimp_account_block_reason(user)
        if motivo:
            raise ValidationError(motivo)
        return super().api_create_key(user, name, scope_codes, expires_at=expires_at,
                                      ip_allowlist=ip_allowlist, allowed_origins=allowed_origins)

    @api.model
    def _shrimp_keys_of_accounts(self, holders):
        """Claves activas (no revocadas) de los usuarios de las cuentas `holders`."""
        if not holders:
            return self.browse()
        keys = self.sudo().search([("active", "=", True), ("revoked_at", "=", False),
                                   ("key_prefix", "!=", False)])
        ids = set(holders.ids)

        def _de(key):
            u = key.user_id.sudo()
            p = u.shrimp_actor_partner_id or u.partner_id
            return bool(p) and p._shrimp_role_holder().id in ids
        return keys.filtered(_de)

    @api.model
    def _shrimp_disable_keys_of_rejected(self, holders=None):
        """Desactiva las claves de las cuentas rechazadas (todas, o `holders`).
        Devuelve las claves desactivadas."""
        Partner = self.env["res.partner"].sudo()
        if holders is None:
            holders = Partner.search([("shrimp_account_state", "=", "rejected")])
        else:
            holders = holders.sudo().filtered(lambda p: p.shrimp_account_state == "rejected")
        keys = self._shrimp_keys_of_accounts(holders)
        if keys:
            keys.write({"active": False})
            for holder in holders:
                mias = keys.filtered(lambda k, h=holder: (
                    (k.user_id.shrimp_actor_partner_id or k.user_id.partner_id)
                    ._shrimp_role_holder() == h))
                if mias:
                    holder.message_post(body=_(
                        "Cuenta rechazada: se desactivaron %(n)s clave(s) de la API externa "
                        "(%(k)s). Si la cuenta se aprueba, el administrador puede reactivarlas.") % {
                        "n": len(mias), "k": ", ".join(mias.mapped("display_key"))})
            _logger.info("shrimp_api: %s claves desactivadas por cuenta rechazada", len(keys))
        return keys


class ResPartnerApiGate(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        res = super().write(vals)
        if vals.get("shrimp_account_state") == "rejected":
            self.env["shrimp.api.key"].sudo()._shrimp_disable_keys_of_rejected(self)
        return res
