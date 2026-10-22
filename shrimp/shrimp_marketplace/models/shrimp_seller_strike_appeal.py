"""«Solicitar anulación» de un strike (decisión del usuario, 2026-10-15).

Una cancelación del vendedor por fuerza mayor (mortalidad, problema
sanitario…) SÍ suma strike, pero el vendedor puede pedir que se anule con una
justificación y, si quiere, una evidencia (foto o PDF). La plataforma recibe
el aviso (mensaje y actividad para los administradores de CamaronMarket) y
decide en el back-office:

  pending ──administrador «Aprobar»──▶ approved (strike anulado con el flujo
                                       de siempre: shrimp.seller.strike.action_annul)
          ──administrador «Rechazar» (motivo)──▶ rejected (el strike sigue)

Vale para cualquier strike de una venta (no solo los de cancelación). Una
solicitud abierta por strike; si se rechaza, puede volver a pedirla con
nueva evidencia.
"""
import base64
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import sql

from odoo.addons.shrimp_user_registry.authz import shrimp_is_trusted

_logger = logging.getLogger(__name__)

EVIDENCE_MIMETYPES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
_APPEAL_TABLE = "shrimp_seller_strike_appeal"


def shrimp_appeal_ready(env):
    reg = env.registry
    if getattr(reg, "_shrimp_appeal_ready", False):
        return True
    listo = sql.table_exists(env.cr, _APPEAL_TABLE)
    if listo:
        reg._shrimp_appeal_ready = True
    return listo


class ShrimpSellerStrikeAppeal(models.Model):
    _name = "shrimp.seller.strike.appeal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Solicitud de anulación de un strike"
    _order = "requested_at desc, id desc"

    name = fields.Char(string="Referencia", compute="_compute_name")
    strike_id = fields.Many2one("shrimp.seller.strike", string="Strike", required=True,
                                ondelete="cascade", index=True)
    partner_id = fields.Many2one(related="strike_id.partner_id", store=True, string="Socio")
    transaction_id = fields.Many2one(related="strike_id.transaction_id", store=True,
                                     string="Compra")
    strike_reason = fields.Selection(related="strike_id.reason", string="Causa del strike")
    reason = fields.Text(string="Justificación del vendedor", required=True)
    evidence_id = fields.Many2one("ir.attachment", string="Evidencia", ondelete="set null")
    state = fields.Selection([("pending", "Pendiente"), ("approved", "Aprobada (strike anulado)"),
                              ("rejected", "Rechazada")], string="Estado", default="pending",
                             required=True, index=True, tracking=True)
    requested_by_id = fields.Many2one("res.users", string="Pedida por", readonly=True)
    requested_at = fields.Datetime(string="Pedida el", readonly=True, default=fields.Datetime.now)
    decided_by_id = fields.Many2one("res.users", string="Decidió", readonly=True)
    decided_at = fields.Datetime(string="Decidida el", readonly=True)
    decision_note = fields.Text(string="Respuesta de la plataforma",
                                help="Obligatoria para rechazar; se envía al vendedor.")

    @api.depends("strike_id.name")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Anulación pedida · %s") % (rec.strike_id.name or "")

    def _shrimp_check_admin(self):
        self.env["shrimp.seller.strike"]._shrimp_check_admin()

    def action_approve(self):
        self._shrimp_check_admin()
        for rec in self.filtered(lambda a: a.state == "pending"):
            nota = (rec.decision_note or "").strip()
            justif = _("Solicitud del vendedor aprobada: %s") % rec.reason
            if nota:
                justif += " — " + nota
            rec.strike_id.action_annul(justif)
            rec.sudo().write({"state": "approved", "decided_by_id": self.env.user.id,
                              "decided_at": fields.Datetime.now()})
            rec._shrimp_close_activities()
        return True

    def action_reject(self):
        self._shrimp_check_admin()
        for rec in self.filtered(lambda a: a.state == "pending"):
            nota = (rec.decision_note or "").strip()
            if not nota:
                raise UserError(_("Escribe la respuesta para el vendedor antes de rechazar."))
            rec.sudo().write({"state": "rejected", "decided_by_id": self.env.user.id,
                              "decided_at": fields.Datetime.now()})
            rec.strike_id._shrimp_post(
                _("Solicitud de anulación rechazada (%s)") % (rec.transaction_id.name or ""), [
                    _("La plataforma revisó tu solicitud de anular el strike de la venta %(r)s y la "
                      "rechazó: %(n)s") % {"r": rec.transaction_id.name or "—", "n": nota},
                    _("El strike sigue vigente hasta el %s.")
                    % rec.transaction_id.shrimp_fmt_dt(rec.strike_id.expires_at, with_time=False)])
            rec._shrimp_close_activities()
        return True

    def _shrimp_close_activities(self):
        try:
            self.sudo().activity_unlink(["mail.mail_activity_data_todo"])
        except Exception:  # noqa: BLE001
            _logger.debug("Sin actividades que cerrar en %s", self.ids)


class ShrimpSellerStrikeAppealMixin(models.Model):
    _inherit = "shrimp.seller.strike"

    appeal_ids = fields.One2many("shrimp.seller.strike.appeal", "strike_id",
                                 string="Solicitudes de anulación")

    def shrimp_appeal_pending(self):
        self.ensure_one()
        if not shrimp_appeal_ready(self.env):
            return self.env["shrimp.seller.strike.appeal"]
        return self.sudo().appeal_ids.filtered(lambda a: a.state == "pending")[:1]

    def shrimp_appeal_last(self):
        self.ensure_one()
        if not shrimp_appeal_ready(self.env):
            return self.env["shrimp.seller.strike.appeal"]
        return self.sudo().appeal_ids.sorted("id")[-1:]

    def shrimp_can_appeal(self):
        self.ensure_one()
        return bool(shrimp_appeal_ready(self.env) and not self.annulled and self.is_active
                    and self.transaction_id and not self.shrimp_appeal_pending())

    def action_request_annulment(self, reason, evidence=None, actor=None):
        """El vendedor pide anular el strike (justificación y evidencia
        opcional: {"content": bytes, "mimetype": str, "name": str})."""
        self.ensure_one()
        if not shrimp_is_trusted(self.env):
            # Por RPC el `actor` no se respeta: debajo se trabaja en sudo y
            # la comprobación de la parte lo tomaría por bueno.
            actor = None
        strike = self.sudo()
        tx = strike.transaction_id
        if not tx:
            raise ValidationError(_("Este strike no está ligado a una venta."))
        tx._shrimp_check_party("seller", actor)
        if not strike.shrimp_can_appeal():
            raise ValidationError(_("Este strike ya está anulado, vencido o tiene una solicitud "
                                    "en revisión."))
        texto = (reason or "").strip()
        if not texto:
            raise ValidationError(_("Explica por qué el strike debería anularse."))
        Appeal = self.env["shrimp.seller.strike.appeal"].sudo()
        vals = {"strike_id": strike.id, "reason": texto, "requested_by_id": self.env.user.id,
                "requested_at": fields.Datetime.now()}
        appeal = Appeal.create(vals)
        if evidence and evidence.get("content"):
            if evidence.get("mimetype") not in EVIDENCE_MIMETYPES:
                raise ValidationError(_("La evidencia tiene que ser una imagen (JPG, PNG, WEBP) o "
                                        "un PDF."))
            adjunto = self.env["ir.attachment"].sudo().create({
                "name": evidence.get("name") or "evidencia-%s" % (tx.name or ""),
                "datas": base64.b64encode(evidence["content"]), "mimetype": evidence["mimetype"],
                "res_model": "shrimp.seller.strike.appeal", "res_id": appeal.id})
            appeal.write({"evidence_id": adjunto.id})
        strike._shrimp_post(_("Solicitud de anulación recibida (%s)") % (tx.name or ""), [
            _("Recibimos tu solicitud de anular el strike de la venta %s. La plataforma la "
              "revisará y te avisará.") % (tx.name or "")])
        appeal._shrimp_notify_admins()
        return appeal


class ShrimpSellerStrikeAppealNotify(models.Model):
    _inherit = "shrimp.seller.strike.appeal"

    def _shrimp_notify_admins(self):
        self.ensure_one()
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False)
        admins = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal()) if grupo else []
        texto = _("El vendedor %(v)s pide anular el strike «%(s)s». Justificación: %(j)s") % {
            "v": self.partner_id.name, "s": self.strike_id.name, "j": self.reason}
        if not admins:
            return
        try:
            self.sudo().message_post(
                body=Markup("<p>%s</p>") % texto, subject=_("Solicitud de anulación de strike"),
                partner_ids=admins.mapped("partner_id").ids, message_type="comment",
                subtype_xmlid="mail.mt_comment")
            for admin in admins[:5]:
                self.sudo().activity_schedule(
                    "mail.mail_activity_data_todo", user_id=admin.id,
                    summary=_("Revisar solicitud de anulación de strike"), note=texto)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo avisar la solicitud %s", self.id, exc_info=True)
