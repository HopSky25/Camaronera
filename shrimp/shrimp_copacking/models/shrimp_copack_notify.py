"""Avisos del servicio de empaque (auditoría de procesos: CR-14, AL-24, ME-23).

Antes ningún paso de la maquila avisaba a nadie: ni la oferta recibida, ni la
adjudicación, ni el acta a la firma, ni la disputa. Con plazos que vencen
solos (acta: el silencio del cliente vale conformidad; solicitudes y ofertas
que caducan) el aviso deja de ser cortesía: es lo que hace justo el plazo.

Un aviso es un mensaje en el chatter del documento con las personas de la
cuenta como destinatarias (el correo sale por la cola de Odoo, nunca en
línea) y, cuando hay algo que decidir para la plataforma, una actividad para
sus administradores. Un aviso nunca tumba la operación que lo dispara.
"""
import logging

from markupsafe import Markup

from odoo import _, api, models

_logger = logging.getLogger(__name__)


class ShrimpCopackNotifyMixin(models.AbstractModel):
    _name = "shrimp.copack.notify.mixin"
    _description = "Avisos del servicio de empaque"

    @api.model
    def _cpk_users_of(self, partner):
        """Usuarios de la cuenta de `partner`: dueño y administradores (y los
        miembros que la cuenta haya habilitado para el perfil)."""
        Users = self.env["res.users"].sudo()
        if not partner:
            return Users
        Tx = self.env["shrimp.transaction"]
        if hasattr(Tx, "_shrimp_party_users"):
            try:
                return Tx._shrimp_party_users(partner, None, None)
            except Exception:  # noqa: BLE001 — un aviso nunca tumba nada
                _logger.debug("Sin usuarios de cuenta para %s", partner.id, exc_info=True)
        holder = partner.sudo()
        if hasattr(holder, "_shrimp_role_holder"):
            holder = holder._shrimp_role_holder()
        return Users.search([("partner_id", "=", holder.id), ("active", "=", True)])

    def _cpk_portal_url(self, partner=None):
        """Ruta del portal del documento (cada modelo la define)."""
        return ""

    def _cpk_notify(self, partners, subject, text, url=None):
        """Aviso a una o varias cuentas (res.partner) con enlace al portal."""
        self.ensure_one()
        destinos = self.env["res.partner"]
        for socio in partners:
            if not socio:
                continue
            usuarios = self._cpk_users_of(socio)
            destinos |= usuarios.mapped("partner_id").filtered("email") or socio.filtered("email")
        enlace = url if url is not None else self._cpk_portal_url(partners[:1] if partners else None)
        cuerpo = Markup("<p>%s</p>") % text
        if enlace:
            cuerpo += Markup("<p><a href=\"%s%s\">%s</a></p>") % (
                self.get_base_url(), enlace, _("Abrir en la plataforma"))
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=cuerpo, subject=subject, partner_ids=destinos.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo avisar (%s) en %s", subject, self.display_name, exc_info=True)

    @api.model
    def _cpk_platform_admins(self):
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False) \
            or self.env.ref("shrimp_marketplace.group_shrimp_manager", raise_if_not_found=False)
        if not grupo:
            return self.env["res.users"]
        return grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal())

    def _cpk_notify_platform(self, text, subject, summary, deadline=None):
        """Mediación: aviso y actividad para los administradores de la plataforma."""
        self.ensure_one()
        admins = self._cpk_platform_admins()
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("<p>%s</p>") % text, subject=subject,
                partner_ids=admins.mapped("partner_id").ids,
                message_type="comment", subtype_xmlid="mail.mt_comment")
            if "activity_ids" in self._fields:
                for admin in admins[:5]:
                    self.sudo().activity_schedule(
                        "mail.mail_activity_data_todo", user_id=admin.id,
                        date_deadline=deadline, summary=summary, note=text)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo avisar a la plataforma de %s", self.display_name, exc_info=True)

    def _cpk_close_platform_activities(self, feedback=None):
        """Cierra las actividades de mediación pendientes del documento."""
        if "activity_ids" not in self._fields:
            return
        for rec in self.sudo():
            pendientes = rec.activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref("mail.mail_activity_data_todo"))
            if pendientes:
                try:
                    pendientes.action_feedback(feedback=feedback or _("Resuelto."))
                except Exception:  # noqa: BLE001
                    pendientes.unlink()
