"""Vencimiento de certificados: cron diario y avisos (decisión D-13).

Certificados del socio (shrimp.user.certificate.line) y del producto
(shrimp.product.certificate.line) APROBADOS:

* 30 días antes (Ajustes › CamaronMarket › «Aviso de certificados por
  vencer»): aviso a los administradores de la cuenta y a la plataforma;
* al vencer: aviso igual, y el certificado pasa a «Vencido» (filtro en el
  back-office; la ficha pública del producto y el perfil del vendedor lo
  muestran «Vencido» en lugar de vigente).

Un aviso por cuenta y por corrida (agrupado), y cada certificado se avisa una
sola vez por etapa (expiry_notice_stage, que vuelve a cero si se cambia la
fecha). No se bloquea nada: la acreditación del verificador ya se controla en
shrimp_verification.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import api, models, _

_logger = logging.getLogger(__name__)


class ShrimpProductCertificateExpiry(models.Model):
    _inherit = "shrimp.product.certificate.line"

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_certificate_expiry(self):
        UserLine = self.env["shrimp.user.certificate.line"].sudo()
        ProdLine = self.sudo().with_context(active_test=False)
        UserLine._shrimp_refresh_validity()
        ProdLine._shrimp_refresh_validity()
        hoy = ProdLine._shrimp_cert_today()
        tope = hoy + timedelta(days=ProdLine._shrimp_cert_alert_days())

        def pendientes(Model, extra):
            avisar = Model.search(extra + [
                ("status", "=", "approved"), ("expiry_date", "!=", False),
                "|",
                "&", ("expiry_date", "<", hoy), ("expiry_notice_stage", "!=", "expired"),
                "&", "&", ("expiry_date", ">=", hoy), ("expiry_date", "<=", tope),
                ("expiry_notice_stage", "=", "none"),
            ])
            vencidos = avisar.filtered(lambda l: l.expiry_date < hoy)
            return vencidos, avisar - vencidos

        # Certificados del socio.
        u_venc, u_prox = pendientes(UserLine, [])
        por_cuenta = defaultdict(lambda: {"user": [], "prod": []})
        for line in u_venc | u_prox:
            cuenta = line.partner_id._shrimp_role_holder()
            por_cuenta[cuenta]["user"].append(line)

        # Certificados de producto: solo los de lotes vivos (borrador o
        # publicados) se avisan; los de lotes cerrados se marcan en silencio.
        p_venc, p_prox = pendientes(ProdLine, [])
        vivos = (p_venc | p_prox).filtered(
            lambda l: l.active and l.product_id.active and l.product_id.state in ("draft", "published"))
        callados = (p_venc | p_prox) - vivos
        for line in vivos:
            cuenta = line.product_id.seller_partner_id._shrimp_role_holder()
            por_cuenta[cuenta]["prod"].append(line)

        avisos = 0
        for cuenta, grupos in por_cuenta.items():
            try:
                with self.env.cr.savepoint():
                    self._shrimp_cert_notify_account(cuenta, grupos["user"], grupos["prod"], hoy)
                    avisos += 1
            except Exception:  # noqa: BLE001 — una cuenta no frena a las demás
                _logger.exception("Aviso de certificados vencidos a %s", cuenta.id)
        for line in callados:
            line.expiry_notice_stage = "expired" if line.expiry_date < hoy else "warned"
        _logger.info("Certificados: %s socio y %s producto avisados (%s cuentas)",
                     len(u_venc | u_prox), len(vivos), avisos)
        return {"accounts": avisos, "user_lines": len(u_venc | u_prox), "product_lines": len(vivos)}

    @api.model
    def _shrimp_platform_admin_partners(self):
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False)
        if not grupo:
            return self.env["res.partner"]
        admins = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal())
        return admins.mapped("partner_id")

    @api.model
    def _shrimp_cert_notify_account(self, cuenta, user_lines, prod_lines, hoy):
        def fila(nombre, fecha, donde=""):
            estado = _("VENCIÓ el %s") % fecha.strftime("%d/%m/%Y") if fecha < hoy else \
                _("vence el %s") % fecha.strftime("%d/%m/%Y")
            return Markup("<li>%s%s: %s</li>") % (nombre, donde, estado)

        filas = []
        for line in sorted(user_lines, key=lambda l: l.expiry_date):
            filas.append(fila(line.certificate_id.name or _("Certificado"), line.expiry_date))
        for line in sorted(prod_lines, key=lambda l: l.expiry_date):
            filas.append(fila(line.certificate_id.name or _("Certificado"), line.expiry_date,
                              " " + _("(lote «%s»)") % (line.product_id.name or "")))
        hay_vencidos = any(l.expiry_date < hoy for l in list(user_lines) + list(prod_lines))
        asunto = _("Certificados vencidos o por vencer de %s") % (cuenta.name or "") if hay_vencidos \
            else _("Certificados por vencer de %s") % (cuenta.name or "")
        cuerpo = Markup("<p>%s</p><ul>%s</ul><p>%s</p>") % (
            _("Estos certificados de %s están vencidos o por vencer:") % (cuenta.name or ""),
            Markup("").join(filas),
            _("Un certificado vencido se sigue mostrando, marcado como «Vencido». Sube la versión "
              "renovada desde Mi cuenta › Certificados (o desde la ficha del lote) para que vuelva "
              "a figurar como vigente."))
        admins = self.env["shrimp.transaction"]._shrimp_party_users(cuenta, None, None)
        destinatarios = admins.mapped("partner_id").filtered("email") or cuenta.filtered("email")
        destinatarios |= self._shrimp_platform_admin_partners()
        cuenta.sudo().with_context(mail_notify_force_send=False).message_post(
            body=cuerpo, subject=asunto, partner_ids=destinatarios.ids,
            message_type="comment", subtype_xmlid="mail.mt_comment",
            email_layout_xmlid="mail.mail_notification_light")
        for line in list(user_lines) + list(prod_lines):
            line.expiry_notice_stage = "expired" if line.expiry_date < hoy else "warned"
