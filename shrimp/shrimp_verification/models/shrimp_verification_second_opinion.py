"""D-25: segunda opinión (re-verificación) tras un rechazo (auditoría, AL-11).

ANTES: el rechazo del verificador cancelaba la compra en el acto y le cargaba
el honorario al vendedor, sin apelación posible (unique(transaction_id)
impedía otra verificación de la misma compra).

AHORA (decisión del usuario, definitiva):

  rechazo por el informe de campo ──▶ plazo de impugnación (24 h, Ajustes;
     contest_state «open»): la compra sigue RETENIDA (pendiente de
     verificación, stock reservado) y el cobro todavía no se reasigna.
     ├─ el vendedor IMPUGNA dentro del plazo eligiendo otra verificadora
     │  acreditada (nunca la misma, nunca una de las partes) ──▶ se crea la
     │  SEGUNDA verificación de la misma compra (second_opinion_of_id); la
     │  primera queda «superada» (superseded) y la compra pasa a ver solo la
     │  segunda. Su honorario (tarifa de la segunda verificadora, D-40) lo
     │  paga SIEMPRE quien la pidió (el vendedor), pase lo que pase.
     │    ├─ la segunda APRUEBA ─▶ la compra sigue normal (ronda de aceptación
     │    │   de la segunda). El honorario de la PRIMERA sigue las reglas de
     │    │   siempre: compra cerrada ─▶ comprador; trato caído ─▶ quien se
     │    │   retiró sin motivo medible (o el vendedor, si el producto no
     │    │   cumplió).
     │    └─ la segunda RECHAZA ─▶ el rechazo se mantiene (no hay tercera
     │        opinión): compra cancelada, los dos honorarios al vendedor.
     └─ nadie impugna ─▶ el rechazo queda FIRME al vencer el plazo (cron):
        compra cancelada, honorario al vendedor, liquidación a la verificadora.

  Sin impugnación posible: el rechazo por «Producto no presentado» (D-16; no
  hay producto que volver a verificar) y el rechazo de una segunda opinión.
  A cada verificadora se le liquida cuando su veredicto queda resuelto (D-24).
  Regla de pago (23-oct-2026): la segunda verificadora cobra solo si emite su
  veredicto; si la compra se cancela, se reasigna o vence antes, no se le
  liquida nada y su honorario (a cargo del vendedor) se anula. La primera ya
  dictaminó: su rechazo se mantiene y se le liquida.
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.shrimp_user_registry.authz import shrimp_actor

_logger = logging.getLogger(__name__)

CONTEST_HOURS_PARAM = "shrimp_verification.contest_hours"
CONTEST_HOURS_DEFAULT = 24


class ShrimpVerificationSecondOpinion(models.Model):
    _inherit = "shrimp.verification"

    superseded = fields.Boolean(
        string="Superada por una segunda opinión", readonly=True, copy=False, index=True,
        help="El vendedor impugnó este rechazo: la compra sigue con la segunda verificación.")
    second_opinion_of_id = fields.Many2one(
        "shrimp.verification", string="Segunda opinión de", readonly=True, copy=False,
        index=True, ondelete="restrict",
        help="Verificación cuyo rechazo se impugnó. Esta segunda opinión la paga quien la pidió.")
    second_opinion_id = fields.Many2one(
        "shrimp.verification", string="Segunda opinión", compute="_compute_second_opinion_id")
    contest_state = fields.Selection(
        [("open", "Plazo para impugnar"), ("contested", "Impugnado: segunda opinión en curso"),
         ("firm", "Rechazo firme"), ("upheld", "Confirmado por la segunda opinión"),
         ("overturned", "Revertido por la segunda opinión")],
        string="Impugnación", readonly=True, copy=False, index=True, tracking=True)
    contest_deadline = fields.Datetime(string="Impugnar hasta", readonly=True, copy=False)

    def _compute_second_opinion_id(self):
        Ver = self.env["shrimp.verification"].sudo()
        for rec in self:
            rec.second_opinion_id = Ver.search(
                [("second_opinion_of_id", "=", rec.id)], limit=1) if rec.id else Ver

    @api.model
    def _shrimp_contest_hours(self):
        # 0 = sin segunda opinión (el rechazo queda firme en el acto).
        return self.env["shrimp.settings"].get_int(
            CONTEST_HOURS_PARAM, CONTEST_HOURS_DEFAULT, minimum=0, maximum=24 * 30)

    # ------------------------------------------------------------------
    # Rechazo: firme o con plazo de impugnación
    # ------------------------------------------------------------------
    def _shrimp_after_reject(self):
        self.ensure_one()
        if self.rejection_kind == "not_presented":
            self._shrimp_not_presented_effects()
            return self._shrimp_finalize_rejection()
        horas = self._shrimp_contest_hours()
        if self.verification_mode != "platform" or self.second_opinion_of_id or not horas:
            return self._shrimp_finalize_rejection()
        limite = fields.Datetime.now() + timedelta(hours=horas)
        self.sudo().write({"contest_state": "open", "contest_deadline": limite})
        tx = self.transaction_id.sudo()
        plazo = tx.shrimp_fmt_dt(limite)
        tx._shrimp_notify("seller", _("Venta %s: la verificación fue rechazada") % tx.name, _(
            "La verificadora %(v)s rechazó el producto de la venta %(r)s. Si no estás de acuerdo, "
            "puedes pedir una SEGUNDA OPINIÓN hasta el %(p)s desde la venta: otra verificadora "
            "acreditada vuelve a verificar y su honorario lo pagas tú (tarifa de la verificadora "
            "que elijas). Si la segunda opinión aprueba, la compra sigue; si rechaza, el rechazo "
            "se mantiene. Si no impugnas, la compra se cancela y el honorario de la verificación "
            "lo pagas tú.") % {"v": self.verifier_partner_id.name, "r": tx.name, "p": plazo})
        tx._shrimp_notify("buyer", _("Compra %s: verificación rechazada") % tx.name, _(
            "La verificadora %(v)s rechazó el producto. El vendedor puede pedir una segunda opinión "
            "hasta el %(p)s; mientras tanto la compra queda en espera. Si no la pide, la compra se "
            "cancela sin costo para ti.") % {"v": self.verifier_partner_id.name, "p": plazo})
        return True

    def _shrimp_finalize_rejection(self, cancel_tx=True):
        """El rechazo queda firme: honorario al vendedor, liquidación a la
        verificadora y (si corresponde) compra cancelada."""
        for rec in self.sudo():
            if rec.contest_state == "open":
                rec.write({"contest_state": "firm"})
            rec.fee_payer_partner_id = rec.seller_partner_id.id
            rec._create_payer_invoice()
            rec._shrimp_payout_request()
            primera = rec.second_opinion_of_id
            if primera:
                primera._shrimp_second_opinion_resolved("upheld", rec.seller_partner_id)
            if cancel_tx:
                rec.transaction_id.action_cancel_for_verification()
        return True

    def _shrimp_second_opinion_resolved(self, outcome, payer):
        """La primera verificación (impugnada) se resuelve con la segunda."""
        for rec in self.sudo():
            if rec.contest_state != "contested":
                continue
            rec.write({"contest_state": outcome, "fee_payer_partner_id": payer.id})
            rec._create_payer_invoice()
            rec._shrimp_payout_request()
            rec._message_log(body=_(
                "Segunda opinión resuelta (%(o)s): el honorario de esta verificación lo paga "
                "%(p)s.") % {"o": dict(rec._fields["contest_state"].selection).get(outcome),
                             "p": payer.name})

    def _cerrar_trato_aceptado(self):
        res = super()._cerrar_trato_aceptado()
        if self.second_opinion_of_id:
            self.second_opinion_of_id._shrimp_second_opinion_resolved(
                "overturned", self.buyer_partner_id)
        return res

    def _cerrar_trato_caido(self, rechazo):
        culpable = False
        if self.second_opinion_of_id:
            culpable, _txt = self._shrimp_broken_culprit(rechazo)
        res = super()._cerrar_trato_caido(rechazo)
        if self.second_opinion_of_id:
            self.second_opinion_of_id._shrimp_second_opinion_resolved("overturned", culpable)
        return res

    # ------------------------------------------------------------------
    # Impugnar
    # ------------------------------------------------------------------
    def shrimp_contest_problem(self):
        """'' si el vendedor puede pedir la segunda opinión ahora; si no, por qué."""
        self.ensure_one()
        rec = self.sudo()
        if rec.verification_mode != "platform" or rec.state != "rejected":
            return _("Solo se impugna el rechazo de una verificadora de la plataforma.")
        if rec.rejection_kind == "not_presented":
            return _("Un rechazo por «Producto no presentado» no admite segunda opinión.")
        if rec.second_opinion_of_id:
            return _("Esta ya es una segunda opinión: su resultado no se impugna.")
        if rec.contest_state != "open":
            return _("Este rechazo ya no se puede impugnar.")
        if rec.contest_deadline and fields.Datetime.now() > rec.contest_deadline:
            return _("Venció el plazo para impugnar el rechazo.")
        if rec.transaction_id.state != "pending_verification":
            return _("La compra ya no está en verificación.")
        return ""

    def shrimp_second_opinion_options(self):
        """Verificadoras que el vendedor puede elegir (acreditadas, ni la
        primera ni una de las partes)."""
        self.ensure_one()
        tx = self.transaction_id.sudo()
        return self.env["res.partner"].sudo().with_context(shrimp_exclude_partner_ids=[
            tx.seller_partner_id.commercial_partner_id.id,
            tx.buyer_partner_id.commercial_partner_id.id,
        ]).available_verifiers().filtered(lambda v: v != self.verifier_partner_id)

    def action_request_second_opinion(self, verifier, actor=None):
        """El vendedor impugna el rechazo: otra verificadora vuelve a verificar."""
        self.ensure_one()
        # El `actor` del argumento solo vale si quien llama es de confianza
        # (después, en sudo, _shrimp_check_party ya lo daría por bueno).
        actor = shrimp_actor(self.env, actor)
        primera = self.sudo()
        tx = primera.transaction_id
        tx._shrimp_check_party("seller", actor)
        problema = primera.shrimp_contest_problem()
        if problema:
            raise ValidationError(problema)
        verifier = verifier.sudo() if verifier else verifier
        if not verifier:
            raise ValidationError(_("Elige la verificadora de la segunda opinión."))
        if verifier == primera.verifier_partner_id \
                or verifier.shrimp_same_entity(primera.verifier_partner_id):
            raise ValidationError(_("La segunda opinión la da otra verificadora."))
        if not verifier._shrimp_has_role("verificador") or not verifier.verifier_is_accredited:
            raise ValidationError(_("La verificadora «%s» no tiene una acreditación aprobada y "
                                    "vigente.") % verifier.name)
        if verifier in (tx.buyer_partner_id, tx.seller_partner_id) \
                or verifier.shrimp_same_entity(tx.buyer_partner_id, tx.seller_partner_id):
            raise ValidationError(_("La verificadora no puede ser el comprador ni el vendedor."))
        fee = primera.shrimp_fee_for(verifier)
        primera.write({"superseded": True, "contest_state": "contested"})
        # El índice único parcial se comprueba en el INSERT: la primera tiene
        # que estar «superada» en la base antes de crear la segunda.
        primera.flush_recordset(["superseded"])
        segunda = self.env["shrimp.verification"].sudo().create({
            "transaction_id": tx.id, "verification_mode": "platform",
            "verifier_partner_id": verifier.id, "fee": fee,
            "second_opinion_of_id": primera.id,
            "fee_payer_partner_id": tx.seller_partner_id.id,
            "batch_code": primera.batch_code or False, "pond_id": primera.pond_id.id or False,
            "pond_label": primera.pond_label or False,
            "facility_id": primera.facility_id.id or False,
            "presentation": tx.product_id.presentation or False,
        })
        segunda._register_fee_charge()
        texto = _("El vendedor pidió una segunda opinión sobre el rechazo de %(v1)s: la orden "
                  "%(o)s la hace %(v2)s (honorario %(f).2f, a cargo del vendedor).") % {
            "v1": primera.verifier_partner_id.name, "o": segunda.name, "v2": verifier.name,
            "f": fee}
        primera._message_log(body=texto)
        segunda._message_log(body=texto)
        tx.message_post(body=Markup("<p>%s</p>") % texto)
        tx._shrimp_notify("buyer", _("Compra %s: segunda opinión en curso") % tx.name, texto + " " + _(
            "Si la segunda verificación aprueba, la compra sigue y vuelves a aceptar el informe; si "
            "rechaza, la compra se cancela sin costo para ti."))
        tx._shrimp_notify("seller", _("Venta %s: segunda opinión pedida") % tx.name, texto)
        primera._shrimp_ver_notify(primera._shrimp_verifier_contacts(),
                                   _("Segunda opinión sobre %s") % primera.name, texto)
        return segunda

    # ------------------------------------------------------------------
    # Cron: el plazo de impugnación vence
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_cron_contest(self, limit=500):
        vencidas = self.sudo().search([
            ("contest_state", "=", "open"), ("contest_deadline", "<=", fields.Datetime.now())],
            limit=limit, order="contest_deadline asc, id asc")
        for rec in vencidas:
            try:
                with self.env.cr.savepoint():
                    rec._shrimp_finalize_rejection()
                    tx = rec.transaction_id.sudo()
                    for lado in ("buyer", "seller"):
                        tx._shrimp_notify(lado, _("Compra %s cancelada: rechazo firme") % tx.name, _(
                            "Venció el plazo para pedir una segunda opinión sobre el rechazo de la "
                            "verificadora %(v)s: el rechazo queda firme, la compra se cancela y el "
                            "honorario de la verificación lo paga el vendedor.") % {
                            "v": rec.verifier_partner_id.name})
            except Exception:  # noqa: BLE001
                _logger.exception("Plazo de impugnación de %s", rec.name)
        return True


class ShrimpTransactionSecondOpinion(models.Model):
    _inherit = "shrimp.transaction"

    def shrimp_verification_contest(self):
        """La verificación con el rechazo impugnable (o en segunda opinión)."""
        self.ensure_one()
        ver = self.sudo().verification_ids[:1]
        return ver if ver and ver.contest_state == "open" else self.env["shrimp.verification"]

    def action_request_second_opinion(self, verifier, actor=None):
        self.ensure_one()
        # Antes de pasar a la verificación (que viaja en sudo).
        actor = shrimp_actor(self.env, actor)
        ver = self.shrimp_verification_contest()
        if not ver:
            raise ValidationError(_("Esta compra no tiene un rechazo que se pueda impugnar."))
        return ver.action_request_second_opinion(verifier, actor=actor)

    def action_switch_verifier(self, verifier, actor=None):
        """En una segunda opinión, cambia de verificadora quien la pidió (y paga)."""
        self.ensure_one()
        ver = self.sudo().verification_ids[:1]
        if ver and ver.second_opinion_of_id:
            self._shrimp_check_party("seller", actor)
            if not self.shrimp_verification_overdue():
                raise ValidationError(_("Solo puedes cambiar de verificadora si la actual no "
                                        "cumplió su plazo o declinó la orden."))
            motivo = _("la verificadora de la segunda opinión no cumplió (%s)") % dict(
                ver._fields["deadline_overdue"].selection).get(ver.deadline_overdue, "")
            return ver._shrimp_reassign_verifier(verifier, motivo, _("lo pidió el vendedor"))
        return super().action_switch_verifier(verifier, actor=actor)

    def _shrimp_cancel_release_hold(self, motivo, kind):
        ver = self.sudo().verification_ids[:1]
        if ver and ver.contest_state == "open":
            ver._shrimp_finalize_rejection(cancel_tx=False)
        elif ver and ver.second_opinion_of_id.contest_state == "contested":
            # La compra se cancela con la segunda opinión sin terminar: el
            # rechazo de la primera se mantiene.
            ver.second_opinion_of_id._shrimp_second_opinion_resolved(
                "upheld", self.seller_partner_id)
        return super()._shrimp_cancel_release_hold(motivo, kind)
