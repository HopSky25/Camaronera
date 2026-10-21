"""Conciliación de la nota de crédito de un cobro con su factura.

``_try_credit`` (shrimp_charge.py) emite la nota de crédito con
``_reverse_moves(cancel=False)``: hace falta para que el puente SRI ponga el
documento 04, el motivo y el punto de emisión antes de contabilizarla, pero
deja la factura Y la nota abiertas en cuentas por cobrar (el cliente aparece
debiendo y con saldo a favor a la vez). Aquí se concilian después de
contabilizar la nota, por el mismo camino con y sin SRI.

Va en archivo aparte para no tocar más de una línea de shrimp_charge.py.
"""
import logging

from odoo import _, models

_logger = logging.getLogger(__name__)


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    @staticmethod
    def _shrimp_open_receivables(move):
        return move.line_ids.filtered(
            lambda l: l.account_id.account_type == "asset_receivable" and not l.reconciled
            and l.parent_state == "posted")

    def _shrimp_reconcile_refund(self, refund):
        """Concilia la cuenta por cobrar de la nota de crédito con la de la factura.

        - Factura sin pagar: se concilian y las dos quedan cerradas.
        - Pagada en parte: se concilia lo pendiente; el resto de la nota queda
          abierto (saldo a favor del cliente) y se agenda «Devolver al cliente».
        - Pagada del todo (o en proceso de pago): la nota queda abierta entera
          y la misma actividad.

        Nunca lanza: un fallo se registra en el cobro y la nota sigue
        contabilizada (se concilia a mano)."""
        self.ensure_one()
        invoice = self.invoice_id
        try:
            with self.env.cr.savepoint():
                credit = self._shrimp_open_receivables(refund)
                debit = self._shrimp_open_receivables(invoice).filtered(
                    lambda l: l.account_id in credit.account_id)
                if credit and debit:
                    (credit | debit).reconcile()
                refund.invalidate_recordset(["amount_residual", "payment_state"])
                saldo = refund.amount_residual
                if refund.currency_id.is_zero(saldo):
                    return True
                responsable = self._shrimp_billing_responsible()
                tipo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
                nota = _(
                    "La factura %(f)s ya estaba pagada (en todo o en parte): la nota de crédito "
                    "%(n)s deja a %(p)s un saldo a favor de %(s)s %(m)s. Devuélvaselo (pago de "
                    "la nota de crédito) o aplíquelo a otra factura suya.") % {
                        "f": invoice.name, "n": refund.name,
                        "p": refund.partner_id.display_name,
                        "s": refund.currency_id.symbol or refund.currency_id.name,
                        "m": "%.2f" % saldo}
                if tipo and responsable:
                    refund.activity_schedule(
                        activity_type_id=tipo.id, user_id=responsable.id,
                        summary=_("Devolver al cliente"), note=nota)
                self.message_post(body=nota)
        except Exception as e:  # noqa: BLE001 - la nota ya está contabilizada
            _logger.warning("Cobro %s: no se pudo conciliar la nota de crédito %s: %s",
                            self.name, refund.name, e)
            self.message_post(body=_(
                "La nota de crédito %(n)s quedó contabilizada pero no se pudo conciliar con "
                "la factura %(f)s: %(e)s. Concílielas a mano.") % {
                    "n": refund.name, "f": invoice.name, "e": e})
        return True
