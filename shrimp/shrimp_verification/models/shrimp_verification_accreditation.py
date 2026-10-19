"""D-23: acreditación de la verificadora que vence (auditoría, AL-09).

Decisión del usuario (definitiva):

* Una verificadora cuya acreditación vence DURANTE una orden PUEDE terminar
  sus órdenes abiertas (asignar, ir a campo, dictaminar), pero NO recibe
  órdenes nuevas: el comprador no la ve en el selector
  (res.partner.available_verifiers), start_verified_purchase la rechaza, y
  tampoco se le puede reasignar una orden ni pedirle una segunda opinión
  (todas exigen verifier_is_accredited).
* Cron diario: avisa a la verificadora N días antes del vencimiento (30,
  Ajustes › Verificación) y el día en que vence; los dos avisos llegan
  también a la plataforma (mensaje + actividad a los administradores).

Las marcas de aviso enviado viven en la línea del certificado
(shrimp_expiry_notice / shrimp_expiry_notice_for): si la verificadora
renueva (cambia la fecha), los avisos vuelven a empezar.
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

WARN_DAYS_PARAM = "shrimp_verification.accreditation_warn_days"
WARN_DAYS_DEFAULT = 30


class ShrimpUserCertificateLineExpiry(models.Model):
    _inherit = "shrimp.user.certificate.line"

    shrimp_expiry_notice = fields.Selection(
        [("soon", "Avisado: vence pronto"), ("expired", "Avisado: vencida")],
        string="Aviso de vencimiento enviado", readonly=True, copy=False)
    shrimp_expiry_notice_for = fields.Date(
        string="Aviso enviado para la fecha", readonly=True, copy=False,
        help="Fecha de expiración a la que corresponde el último aviso: si la acreditación se "
             "renueva (cambia la fecha), los avisos vuelven a empezar.")

    @api.model
    def _shrimp_warn_days(self):
        return self.env["shrimp.settings"].get_int(WARN_DAYS_PARAM, WARN_DAYS_DEFAULT,
                                                   minimum=1, maximum=365)

    @api.model
    def _cron_verifier_accreditation_expiry(self, limit=500):
        """Avisos de vencimiento de las acreditaciones de verificadora."""
        hoy = fields.Date.context_today(self)
        dias = self._shrimp_warn_days()
        lineas = self.sudo().search([
            ("certificate_id.role", "=", "verificador"), ("status", "=", "approved"),
            ("expiry_date", "!=", False), ("expiry_date", "<=", hoy + timedelta(days=dias)),
        ], limit=limit, order="expiry_date asc, id asc")
        for linea in lineas:
            try:
                with self.env.cr.savepoint():
                    linea._shrimp_expiry_step(hoy)
            except Exception:  # noqa: BLE001 — un aviso nunca tumba el cron
                _logger.exception("Aviso de vencimiento de la acreditación %s", linea.id)
        return True

    def _shrimp_expiry_step(self, hoy):
        self.ensure_one()
        aviso = self.shrimp_expiry_notice if self.shrimp_expiry_notice_for == self.expiry_date \
            else False
        vencida = self.expiry_date < hoy
        if vencida and aviso != "expired":
            self._shrimp_expiry_notify("expired")
        elif not vencida and not aviso:
            self._shrimp_expiry_notify("soon")

    def _shrimp_expiry_notify(self, cual):
        self.ensure_one()
        socio = self.partner_id.sudo()
        # ¿Le queda otra acreditación vigente? Entonces no se queda sin
        # poder recibir órdenes: el aviso es informativo.
        otra = socio.certificate_line_ids.filtered(
            lambda c: c != self and c.certificate_id.role == "verificador"
            and c.status == "approved" and (not c.expiry_date or c.expiry_date > self.expiry_date))
        abiertas = self.env["shrimp.verification"].sudo().search_count([
            ("verifier_partner_id", "=", socio.id), ("verification_mode", "=", "platform"),
            ("state", "in", ("received", "assigned", "in_field", "done"))])
        fecha = self.expiry_date.strftime("%d/%m/%Y")
        if cual == "soon":
            asunto = _("Tu acreditación de verificadora vence el %s") % fecha
            texto = _(
                "La acreditación «%(c)s» de %(v)s vence el %(f)s. Renuévala y súbela en tu perfil "
                "antes de esa fecha: con la acreditación vencida puedes terminar las órdenes que ya "
                "tienes, pero no recibirás órdenes nuevas.") % {
                "c": self.certificate_id.name, "v": socio.name, "f": fecha}
        else:
            asunto = _("Tu acreditación de verificadora venció")
            texto = _(
                "La acreditación «%(c)s» de %(v)s venció el %(f)s. Desde hoy no recibes órdenes "
                "nuevas. Puedes terminar tus %(n)s orden(es) abiertas. Sube la acreditación "
                "renovada en tu perfil para volver a recibir órdenes.") % {
                "c": self.certificate_id.name, "v": socio.name, "f": fecha, "n": abiertas}
        if otra:
            texto += " " + _("(Tienes otra acreditación vigente registrada.)")
        destinatarios = socio.filtered(lambda p: p.email)
        try:
            socio.with_context(mail_notify_force_send=False).message_post(
                body=Markup("<p>%s</p>") % texto, subject=asunto,
                partner_ids=destinatarios.ids, message_type="comment",
                subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo avisar a la verificadora %s", socio.name, exc_info=True)
        self._shrimp_expiry_escalate(socio, asunto, texto, abiertas)
        self.sudo().write({"shrimp_expiry_notice": cual,
                           "shrimp_expiry_notice_for": self.expiry_date})

    def _shrimp_expiry_escalate(self, socio, asunto, texto, abiertas):
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager",
                             raise_if_not_found=False)
        admins = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal()) if grupo else []
        if not admins:
            return
        nota = _("%(t)s Órdenes abiertas de la verificadora: %(n)s.") % {"t": texto, "n": abiertas}
        try:
            socio.message_post(
                body=Markup("<p>%s</p>") % nota, subject=asunto,
                partner_ids=admins.mapped("partner_id").ids, message_type="comment",
                subtype_xmlid="mail.mt_comment")
            for admin in admins[:5]:
                socio.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=admin.id,
                    summary=_("Acreditación de verificadora: %s") % socio.name, note=nota)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo escalar el vencimiento de %s", socio.name, exc_info=True)
