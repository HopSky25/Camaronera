"""Correos en cola cuando la acción viene de la API.

Varios flujos de negocio mandan el correo con force_send=True: el portal
espera a que el SMTP conteste. En una petición de API eso es inaceptable (un
SMTP lento convierte un POST de 50 ms en uno de 30 s y dispara reintentos del
cliente). Con el contexto ``shrimp_api_queue_mail`` el correo se encola y lo
manda el cron de correo de Odoo.
"""

from odoo import api, models


class MailTemplate(models.Model):
    _inherit = "mail.template"

    def send_mail(self, res_id, force_send=False, raise_exception=False, email_values=None,
                  email_layout_xmlid=False):
        if self.env.context.get("shrimp_api_queue_mail"):
            force_send = False
        return super().send_mail(res_id, force_send=force_send, raise_exception=raise_exception,
                                 email_values=email_values, email_layout_xmlid=email_layout_xmlid)

    def send_mail_batch(self, res_ids, force_send=False, raise_exception=False, email_values=None,
                        email_layout_xmlid=False):
        if self.env.context.get("shrimp_api_queue_mail"):
            force_send = False
        return super().send_mail_batch(res_ids, force_send=force_send,
                                       raise_exception=raise_exception,
                                       email_values=email_values,
                                       email_layout_xmlid=email_layout_xmlid)


class ShrimpNotifyMixin(models.AbstractModel):
    _inherit = "shrimp.notify.mixin"

    def _send_template(self, xmlid, email_to, ctx=None):
        if not self.env.context.get("shrimp_api_queue_mail"):
            return super()._send_template(xmlid, email_to, ctx=ctx)
        # Encolado: se da por avisado (el cron de correo lo entrega). El
        # mixin original devolvía False para todo lo que no salió en el acto,
        # y en la API eso dejaría en el chatter un "no se pudo avisar" falso.
        if not email_to or not self._hay_servidor_de_correo():
            return False
        try:
            template = self.env.ref(xmlid, raise_if_not_found=False)
            if not template:
                return False
            if ctx:
                template = template.with_context(**ctx)
            template.sudo().send_mail(self.id, force_send=False,
                                      email_values={"email_to": email_to})
        except Exception:  # noqa: BLE001
            return False
        return True
