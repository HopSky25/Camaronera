"""D-24: liquidación a la verificadora (auditoría de procesos, AL-10).

ANTES: la liquidación (factura de proveedor por la parte del honorario que le
toca a la verificadora) se intentaba al emitir el veredicto, sin estado ni
reintento: si fallaba quedaba un mensaje en el chatter y nada más. En Pruebas
0 de 261 veredictos la tenían (los veredictos de la demo nunca pasaron por
_close, y nada la emitía después).

AHORA (decisión del usuario, definitiva): a la verificadora se le paga cuando
se CIERRA LA RONDA DE ACEPTACIÓN:

  ronda cerrada (aceptada por las dos, o vencida) ─┐
  trato caído (una parte rechazó el informe)      ─┼─▶ payout_state «Por liquidar»
  rechazo firme (sin impugnar o confirmado, D-25) ─┘      │ _create_verifier_bill
                                                          ▼ (shrimp.charge._create_vendor_bill)
                              «Liquidada» (factura de proveedor; en borrador si el diario
                              exige documentos del SRI) ──▶ «Pagada» cuando contabilidad
                              registra el pago (payment_state de la factura)
                              «Error» ──▶ el cron la reintenta (5 intentos; luego escala a
                              la plataforma)

La verificadora ve el estado en su portal: /verifier/reports, sección
«Liquidaciones». No se inventa contabilidad: el documento es el mismo que ya
emitía shrimp.charge._create_vendor_bill.

REGLA DE PAGO (decisión de la dueña, 23-oct-2026): «La verificadora se le
pagará una vez realizado su trabajo; si no, no se debe pagar ningún valor».
Trabajo hecho = veredicto firmado por esa verificadora
(shrimp.verification._shrimp_trabajo_hecho): aprobada, aprobada con
observaciones, rechazada por el informe o «Producto no presentado».

  con veredicto ─▶ se liquida en los momentos de arriba; si la compra se
                   cancela con la ronda abierta, al cancelarse (la ronda se
                   cierra ahí) y el honorario se conserva.
  sin veredicto ─▶ NO se liquida nada y el honorario cobrado al pagador se
                   anula (nota de crédito si ya estaba facturado): orden
                   declinada, plazo vencido (cambio de verificadora o
                   cancelación), compra cancelada antes del veredicto aunque
                   la visita hubiera empezado («En campo», «Informe
                   completo»), mutuo acuerdo, desistimiento del vendedor,
                   verificación declarada por las partes, y la segunda
                   opinión que no llegó a dictaminar.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

PAYOUT_MAX_ATTEMPTS = 5


class ShrimpVerificationPayout(models.Model):
    _inherit = "shrimp.verification"

    payout_state = fields.Selection(
        [("none", "Sin liquidar"), ("pending", "Por liquidar"), ("billed", "Liquidada"),
         ("error", "Error al liquidar"),
         # Veredictos anteriores a esta regla (19.0.1.24.0): decisión del
         # usuario, no se liquidan.
         ("legacy", "Histórico — no se liquida")],
        string="Liquidación a la verificadora", default="none", required=True, copy=False,
        index=True, tracking=True,
        help="Solo si la verificadora emitió su veredicto (sin veredicto no se le paga nada). "
             "Se liquida cuando se cierra la ronda de aceptación (o el rechazo queda firme). "
             "«Liquidada»: la factura de proveedor ya está registrada; el pago lo registra "
             "contabilidad.")
    payout_due_date = fields.Datetime(string="Liquidar desde", readonly=True, copy=False)
    payout_error = fields.Text(string="Último error de la liquidación", readonly=True, copy=False)
    payout_attempts = fields.Integer(string="Intentos de liquidación", readonly=True, copy=False)
    payout_last_attempt = fields.Datetime(string="Último intento de liquidación", readonly=True,
                                          copy=False)
    # Calculado, sin columna: lo que ve la verificadora.
    payout_paid = fields.Boolean(string="Pagada a la verificadora",
                                 compute="_compute_payout_paid")

    @api.depends("vendor_bill_id.payment_state", "vendor_bill_id.state")
    def _compute_payout_paid(self):
        for rec in self:
            bill = rec.sudo().vendor_bill_id
            rec.payout_paid = bool(bill and bill.state == "posted"
                                   and bill.payment_state in ("paid", "in_payment"))

    def shrimp_payout_label(self):
        """Texto del estado de la liquidación para el portal de la verificadora."""
        self.ensure_one()
        rec = self.sudo()
        if rec.payout_state == "billed":
            if rec.payout_paid:
                return _("Pagada")
            if rec.vendor_bill_id.state == "draft":
                return _("Liquidada · en revisión contable")
            return _("Liquidada · pago pendiente")
        if rec.payout_state == "legacy":
            return _("Histórico — no se liquida")
        if rec.payout_state == "pending":
            return _("Por liquidar")
        if rec.payout_state == "error":
            return _("En reintento")
        if rec.verification_mode == "platform" and rec.state in (
                "approved", "approved_obs") and rec.acceptance_state == "waiting":
            return _("Se liquida al cerrarse la ronda de aceptación")
        if rec.state == "rejected" and rec.contest_state in ("open", "contested"):
            return _("Se liquida cuando el rechazo quede firme")
        if rec.verification_mode == "platform" and rec.state == "cancelled":
            return _("Sin veredicto: no se liquida")
        return _("Sin liquidar")

    # ------------------------------------------------------------------
    # Cuándo se debe
    # ------------------------------------------------------------------
    def _shrimp_payout_request(self):
        """La liquidación ya es debida: la marca y la intenta emitir."""
        pendientes = self.browse()
        for rec in self.sudo():
            # Regla de pago (23-oct-2026): sin veredicto no se debe nada.
            if not rec._shrimp_trabajo_hecho() or (rec.fee or 0.0) <= 0 \
                    or not rec.verifier_partner_id or rec.vendor_bill_id \
                    or rec.payout_state in ("pending", "billed", "error", "legacy"):
                continue
            rec.write({"payout_state": "pending", "payout_due_date": fields.Datetime.now(),
                       "payout_attempts": 0, "payout_error": False})
            pendientes |= rec
        pendientes._create_verifier_bill()
        return True

    def _cerrar_trato_aceptado(self):
        res = super()._cerrar_trato_aceptado()
        self._shrimp_payout_request()
        return res

    def _cerrar_trato_caido(self, rechazo):
        res = super()._cerrar_trato_caido(rechazo)
        self._shrimp_payout_request()
        return res

    def _shrimp_payout_failed(self, intentos, error):
        self.ensure_one()
        if intentos != PAYOUT_MAX_ATTEMPTS:
            return
        try:
            self._shrimp_ver_escalate(_(
                "La liquidación a la verificadora %(v)s de la orden %(o)s falló %(n)s veces. "
                "Último error: %(e)s") % {"v": self.verifier_partner_id.name, "o": self.name,
                                          "n": intentos, "e": str(error)[:500]})
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo escalar la liquidación de %s", self.name, exc_info=True)

    @api.model
    def _cron_vencer_plazos(self):
        """El cron horario de la verificación suma: plazo de impugnación
        (D-25), plazo de la declarada (D-41) y reintento de liquidaciones
        (D-24). Cada parte aislada: un fallo no frena a las demás."""
        res = super()._cron_vencer_plazos()
        for metodo in ("_shrimp_cron_contest", "_shrimp_cron_declared", "_cron_verifier_payouts"):
            try:
                getattr(self, metodo)()
            except Exception:  # noqa: BLE001
                _logger.exception("Cron de verificación: fallo en %s", metodo)
        return res

    @api.model
    def _cron_verifier_payouts(self, limit=200):
        """Reintenta las liquidaciones debidas que no se pudieron emitir."""
        recs = self.sudo().search([
            ("payout_state", "in", ("pending", "error")),
            ("payout_attempts", "<", PAYOUT_MAX_ATTEMPTS),
        ], limit=limit, order="payout_due_date asc, id asc")
        for rec in recs:
            try:
                with self.env.cr.savepoint():
                    rec._create_verifier_bill()
            except Exception:  # noqa: BLE001
                _logger.exception("Liquidación de la verificación %s", rec.name)
        return True

    def action_retry_payout(self):
        """Botón del back-office: reintenta la liquidación (vuelve a contar)."""
        # El mismo grupo que el botón (group_shrimp_manager): trabaja en sudo.
        self.env["shrimp.transaction"]._shrimp_check_platform_admin()
        recs = self.sudo().filtered(lambda r: r.payout_state in ("pending", "error"))
        recs.write({"payout_attempts": 0})
        recs._create_verifier_bill()
        return True

    @api.model
    def shrimp_payouts_for(self, empresa, limit=50):
        """Liquidaciones de la empresa verificadora para su portal."""
        return self.sudo().search([
            ("verifier_partner_id", "=", empresa.id), ("verification_mode", "=", "platform"),
            "|", ("payout_state", "!=", "none"),
            "&", ("state", "in", ("approved", "approved_obs", "rejected")),
            ("verified_date", "!=", False),
        ], limit=limit, order="verified_date desc, id desc")
