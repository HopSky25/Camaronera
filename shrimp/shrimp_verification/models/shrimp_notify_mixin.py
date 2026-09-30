"""Los tres ayudantes de correo que ya usaba la verificación.

Estaban escritos dentro de ``shrimp.verification`` y no tienen nada que ver con
verificar: son «¿hay SMTP?», «manda esta plantilla a esta dirección y dime si
salió de verdad» y «deja constancia en el chatter de a quién se avisó». El
seguimiento del despacho necesita exactamente lo mismo —y necesita que se
comporte IGUAL, porque el valor de esos avisos está en que el fallo silencioso
de SMTP quede escrito en algún sitio—, así que se sacan a un mixin en vez de
copiarlos. Copiarlos habría dejado dos versiones que se desincronizan: la
siguiente corrección del envío se aplicaría a una sola.

Quien lo herede tiene que heredar también ``mail.thread``: ``_log_notificacion``
escribe en el chatter.
"""

from odoo import api, models, _


class ShrimpNotifyMixin(models.AbstractModel):
    _name = "shrimp.notify.mixin"
    _description = "Envío de avisos por correo con constancia en el chatter"

    @api.model
    def _hay_servidor_de_correo(self):
        """¿Hay a dónde entregar el correo?

        Si no hay ningún servidor SMTP configurado, intentar el envío no falla
        rápido: Odoo abre un socket y espera el timeout por cada destinatario,
        y eso deja colgada la página del comprador varios minutos. Mejor no
        intentarlo y decirlo claro en el chatter.
        """
        return bool(self.env["ir.mail_server"].sudo().search_count([]))

    def _send_template(self, xmlid, email_to, ctx=None):
        """Envía una plantilla a una dirección. Devuelve True solo si el correo
        salió de verdad: si no hay servidor SMTP configurado Odoo no lanza
        excepción, deja el mail en estado ``exception``, y eso hay que poder
        distinguirlo de un envío exitoso para reportarlo en el chatter."""
        if not email_to or not self._hay_servidor_de_correo():
            return False
        try:
            template = self.env.ref(xmlid, raise_if_not_found=False)
            if not template:
                return False
            if ctx:
                template = template.with_context(**ctx)
            mail_id = template.sudo().send_mail(
                self.id, force_send=True, email_values={"email_to": email_to})
        except Exception:
            # El correo nunca debe romper el flujo de compra ni el de verificación.
            return False
        # Con force_send y auto_delete, el registro desaparece al enviarse bien.
        mail = self.env["mail.mail"].sudo().browse(mail_id).exists()
        return (not mail) or mail.state == "sent"

    def _log_notificacion(self, entregados):
        """Deja constancia en el chatter de a quién se le avisó y a quién no.

        Sin esto, un SMTP mal configurado se traga los correos en silencio y
        nadie se entera hasta que un comprador reclama que nunca supo nada.
        """
        self.ensure_one()
        if not entregados:
            return
        ok = [p.name for p, enviado in entregados if enviado]
        fallo = [p.name for p, enviado in entregados if not enviado]
        partes = []
        if ok:
            partes.append(_("Notificados por correo: %s.") % ", ".join(ok))
        if fallo and not self._hay_servidor_de_correo():
            partes.append(_(
                "No se envió correo a %s: no hay ningún servidor de correo "
                "saliente configurado en Ajustes > Técnico > Servidores de "
                "correo saliente."
            ) % ", ".join(fallo))
        elif fallo:
            partes.append(_(
                "No se pudo enviar el correo a: %s. Revisa el servidor de "
                "correo saliente en Ajustes."
            ) % ", ".join(fallo))
        # _message_log y no message_post: esto es una anotación de auditoría,
        # no un mensaje que deba volver a notificar a los seguidores (eso
        # dispararía una segunda tanda de correos por cada aviso enviado).
        self.sudo()._message_log(body=" ".join(partes))
