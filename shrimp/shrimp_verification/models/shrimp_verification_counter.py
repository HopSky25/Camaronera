"""D-38 y D-22: la contraoferta del comprador en la ronda de aceptación
(auditoría de procesos, ME-08 y AL-07).

D-38 (definitiva): una sola contraoferta por compra (aunque se deshaga) y
hacerla NO reinicia el plazo de 48 h de la ronda (antes lo reiniciaba, y con
«deshacer» el comprador podía retener el stock indefinidamente). Solo se
garantiza el margen mínimo de «deshacer» para que el vendedor pueda leerla
(shrimp.verification.acceptance.action_counter).

D-22 (definitiva, revisada por el usuario): la contraoferta ocurre entre
empacadora y camaronera con el producto YA en la planta del comprador (no se
puede devolver). Por eso hay solo dos salidas:

  contraoferta ─┬─ el vendedor la ACEPTA ──────────────▶ compra cerrada al precio
                │                                         de la contraoferta;
                └─ el vendedor la RECHAZA o CALLA hasta ─▶ compra cerrada al precio
                   el vencimiento del plazo                ORIGINAL (sin paso extra
                                                           del comprador, sin
                                                           cancelación, sin strike).

Antes, el silencio del vendedor aplicaba el precio rebajado (el cron daba por
aceptada su postura) y su rechazo tumbaba el trato.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class ShrimpVerificationCounter(models.Model):
    _inherit = "shrimp.verification"

    counter_outcome = fields.Selection(
        [("rejected", "El vendedor rechazó la contraoferta"),
         ("expired", "El vendedor no respondió la contraoferta")],
        string="Contraoferta", readonly=True, copy=False,
        help="La contraoferta del comprador no se aceptó: la compra se cerró al precio original.")

    def shrimp_counter_allowed(self):
        """¿El comprador todavía puede contraofertar? (D-38: una sola vez)."""
        self.ensure_one()
        comprador = self.acceptance_ids.filtered(lambda a: a.role == "buyer")[:1]
        return bool(comprador and not self.counter_outcome
                    and not comprador._shrimp_counter_used())

    def _shrimp_counter_rejected(self, vendedor, auto=False, reason=None, actor=None):
        """El vendedor no aceptó la contraoferta (en forma expresa o callando):
        la compra se cierra al precio ORIGINAL."""
        self.ensure_one()
        comprador = self.acceptance_ids.filtered(lambda a: a.role == "buyer")[:1]
        if not comprador or comprador.decision != "counter":
            raise UserError(_("No hay una contraoferta pendiente."))
        if auto:
            if not self.env.su:
                raise AccessError(_("Solo el sistema registra decisiones automáticas."))
        else:
            vendedor._check_actor(actor=actor)
        precio = comprador.counter_price
        motivo_v = (reason or "").strip() or (
            _("No respondió la contraoferta en el plazo: se aplica el precio original.") if auto
            else _("Rechazó la contraoferta: se aplica el precio original."))
        antes = vendedor._signoff_snapshot()
        vendedor.with_context(_shrimp_firma_ok=True).write({
            "decision": "accepted", "decided_at": fields.Datetime.now(), "auto": bool(auto),
            "reason": motivo_v})
        vendedor._signoff_log("decision", antes, reason=motivo_v, auto=auto)
        antes_c = comprador._signoff_snapshot()
        motivo_c = _("Su contraoferta (%s por unidad) no fue aceptada: la compra se cierra al "
                     "precio original.") % precio
        comprador.with_context(_shrimp_firma_ok=True).write({
            "decision": "accepted", "decided_at": fields.Datetime.now(), "auto": True,
            "counter_price": 0.0, "reason": motivo_c})
        comprador._signoff_log("decision", antes_c, auto=True, reason=motivo_c)
        self.sudo().write({"counter_outcome": "expired" if auto else "rejected"})
        self.sudo()._message_log(body=_(
            "Contraoferta de %(p)s no aceptada por el vendedor (%(como)s): la compra se cierra al "
            "precio original de %(o)s.") % {
            "p": precio, "como": _("no respondió") if auto else _("la rechazó"),
            "o": self.transaction_id.price_unit})
        self._resolver_aceptacion()
        return True

    def _shrimp_acceptance_expired(self):
        self.ensure_one()
        comprador = self.acceptance_ids.filtered(lambda a: a.role == "buyer")[:1]
        vendedor = self.acceptance_ids.filtered(lambda a: a.role == "seller")[:1]
        if comprador.decision == "counter" and vendedor.decision == "pending":
            # D-22: callar ante la contraoferta es rechazarla → precio original.
            return self._shrimp_counter_rejected(vendedor, auto=True)
        return super()._shrimp_acceptance_expired()


class ShrimpVerificationAcceptanceCounter(models.Model):
    _inherit = "shrimp.verification.acceptance"

    def _shrimp_counter_used(self):
        """¿Esta postura ya hizo una contraoferta (aunque la haya deshecho)?"""
        self.ensure_one()
        if not self.id:
            return False
        return bool(self.env["shrimp.signoff.event"].sudo().search_count([
            ("res_model", "=", self._name), ("res_id", "=", self.id),
            ("kind", "=", "decision"), ("decision", "=", "counter")], limit=1))

    def action_reject(self, reason=None, actor=None):
        """El vendedor que «rechaza» con una contraoferta abierta rechaza la
        CONTRAOFERTA (no el informe): la compra se cierra al precio original
        (D-22)."""
        contra = self.filtered(lambda a: a.role == "seller" and a.decision == "pending"
                               and a.verification_id.acceptance_state == "waiting"
                               and any(o.role == "buyer" and o.decision == "counter"
                                       for o in a.verification_id.acceptance_ids))
        for rec in contra:
            rec.verification_id._shrimp_counter_rejected(rec, reason=reason, actor=actor)
        resto = self - contra
        if resto:
            return super(ShrimpVerificationAcceptanceCounter, resto).action_reject(
                reason=reason, actor=actor)
        return True
