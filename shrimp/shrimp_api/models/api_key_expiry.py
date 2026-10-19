"""Caducidad de las claves de API (D-46 de la auditoría de procesos).

* Toda clave caduca: por defecto a los 365 días (parámetro del sistema
  ``shrimp_api.key_lifetime_days``). La migración 19.0.1.10.0 dio un año
  desde la actualización a las claves que no tenían fecha.
* Aviso por correo N días antes (``shrimp_api.key_expiry_notice_days``, 7 por
  defecto) y aviso en el portal (/my y /my/api-keys).
* Una clave caducada responde 401 con ``"code": "key-expired"``
  (controllers/main.py).
* «Renovar» en el portal alarga la MISMA clave (mismo secreto) otro periodo:
  la integración no se toca. No se renueva una clave revocada ni la de una
  cuenta bloqueada.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

LIFETIME_DAYS_DEFAULT = 365
NOTICE_DAYS_DEFAULT = 7


class ShrimpApiKeyExpiry(models.Model):
    _inherit = "shrimp.api.key"

    expiry_notice_sent_at = fields.Datetime(
        string="Aviso de caducidad enviado el", readonly=True, copy=False,
        help="Se vacía al cambiar la fecha de caducidad (renovación).")
    renewed_at = fields.Datetime(string="Renovada el", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    @api.model
    def _param_int(self, key, default, minimum=1):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(key) or default)
        except (TypeError, ValueError):
            value = default
        return max(value, minimum)

    @api.model
    def _lifetime_days(self):
        return self._param_int("shrimp_api.key_lifetime_days", LIFETIME_DAYS_DEFAULT)

    @api.model
    def _notice_days(self):
        return self._param_int("shrimp_api.key_expiry_notice_days", NOTICE_DAYS_DEFAULT, minimum=0)

    @api.model
    def _default_expiry(self):
        return fields.Datetime.now() + timedelta(days=self._lifetime_days())

    # ------------------------------------------------------------------
    # Ninguna clave nueva sin caducidad
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("key_prefix") and not vals.get("expires_at"):
                vals["expires_at"] = self._default_expiry()
        return super().create(vals_list)

    def write(self, vals):
        if "expires_at" in vals and "expiry_notice_sent_at" not in vals:
            vals = dict(vals, expiry_notice_sent_at=False)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Estado de caducidad (portal)
    # ------------------------------------------------------------------
    def _expires_soon(self):
        """¿Vigente y dentro de la ventana de aviso?"""
        self.ensure_one()
        if self.status != "active" or not self.expires_at:
            return False
        return self.expires_at <= fields.Datetime.now() + timedelta(days=self._notice_days())

    def _can_renew(self):
        self.ensure_one()
        return bool(self.key_prefix) and not self.revoked_at and self.status in ("active", "expired")

    def action_renew(self):
        """Alarga la caducidad un periodo completo desde hoy (misma clave)."""
        now = fields.Datetime.now()
        for key in self.sudo():
            if not key._can_renew():
                raise ValidationError(_("Solo se renuevan claves vigentes o caducadas (no revocadas "
                                        "ni desactivadas)."))
            motivo = key._shrimp_account_block_reason(key.user_id)
            if motivo:
                raise ValidationError(motivo)
            key.write({"expires_at": now + timedelta(days=key._lifetime_days()), "renewed_at": now})
            _logger.info("API key %s renovada hasta %s", key.display_key, key.expires_at)
        return True

    # ------------------------------------------------------------------
    # Aviso previo (cron diario)
    # ------------------------------------------------------------------
    @api.model
    def _cron_expiry_notice(self):
        now = fields.Datetime.now()
        limit = now + timedelta(days=self._notice_days())
        keys = self.sudo().search([
            ("active", "=", True), ("revoked_at", "=", False), ("key_prefix", "!=", False),
            ("expires_at", ">", now), ("expires_at", "<=", limit),
            ("expiry_notice_sent_at", "=", False),
        ])
        template = self.env.ref("shrimp_api.mail_template_api_key_expiring", raise_if_not_found=False)
        for key in keys:
            email = key.user_id.partner_id.email
            if template and email:
                try:
                    with self.env.cr.savepoint():
                        template.sudo().send_mail(key.id, force_send=False,
                                                  email_values={"email_to": email})
                except Exception:  # noqa: BLE001 - un aviso nunca para el cron
                    _logger.warning("No se pudo avisar de la caducidad de %s", key.display_key,
                                    exc_info=True)
            # Se marca aunque no haya correo: el aviso del portal sigue saliendo
            # mientras esté en la ventana.
            key.write({"expiry_notice_sent_at": now})
        return len(keys)

    # ------------------------------------------------------------------
    # Avisos del portal (/my y /my/api-keys)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_portal_api_notices(self, user):
        """Lista de avisos {level, text, url} sobre las claves del usuario y las
        suscripciones de webhooks de su socio."""
        user = user.sudo()
        if not user.share or user._is_public():
            return []
        notices = []
        keys = self.sudo().with_context(active_test=False).search(
            [("user_id", "=", user.id), ("key_prefix", "!=", False), ("revoked_at", "=", False)])
        soon = keys.filtered(lambda k: k._expires_soon())
        expired = keys.filtered(lambda k: k.active and k.status == "expired")
        if soon:
            notices.append({"level": "warning", "url": "/my/api-keys", "text": _(
                "%(n)s clave(s) de API caducan en los próximos %(d)s días (%(names)s). "
                "Renuévalas para que tu integración siga funcionando.") % {
                "n": len(soon), "d": self._notice_days(), "names": ", ".join(soon.mapped("name"))}})
        if expired:
            notices.append({"level": "danger", "url": "/my/api-keys", "text": _(
                "%(n)s clave(s) de API caducaron (%(names)s): la integración recibe 401. "
                "Puedes renovarlas.") % {"n": len(expired), "names": ", ".join(expired.mapped("name"))}})
        partner = user.shrimp_actor_partner_id or user.partner_id
        subs = self.env["shrimp.webhook.subscription"].sudo().with_context(active_test=False).search(
            [("partner_id", "=", partner.id), ("active", "=", False), ("auto_disabled_at", "!=", False)])
        for sub in subs:
            notices.append({"level": "danger", "url": "/my/webhooks", "text": _(
                "El webhook «%(name)s» se desactivó solo por fallos seguidos. Corrige tu servidor y "
                "reactívalo: se reenviarán los eventos no entregados de los últimos %(d)s días.") % {
                "name": sub.name, "d": sub._resend_days()}})
        return notices
