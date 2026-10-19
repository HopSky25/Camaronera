"""Cierre de la compra verificada (hueco #1 de la auditoría de procesos).

ANTES: al aceptarse el informe la compra pasaba a «Confirmada», pero una
compra verificada no lleva el paso «Confirmar recepción» (el portal no lo
muestra y el cron de recepción no la mira): quedaba confirmada para siempre y
la empacadora nunca recibía su lote (no podía empacar ni exportar).

AHORA (decisión D-01, recomendación de la auditoría): el informe aceptado por
las dos partes ACREDITA la recepción —la verificación se hace con el producto
en planta (peso, basura, tallas, sabor) o con las larvas contadas—, así que al
cerrarse la ronda de aceptación la compra pasa directamente a «Completada»:

  veredicto favorable ─▶ ronda de aceptación ─▶ closed (ambas aceptan o
  vence el plazo) ─▶ action_confirm (consume lotes, comisión) ─▶
  action_receive (lote del comprador + ajuste al peso de planta) ─▶ DONE
  (reception_status = «verified»).

Si el cierre fallara (p. ej. un error al crear el lote del comprador), la
compra queda «Confirmada» y el cron horario de entregas lo reintenta
(``_shrimp_cron_close_verified``). La migración 19.0.1.21.0 cierra las
compras verificadas que ya estaban atascadas.
"""
import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)


class ShrimpVerificationClosePurchase(models.Model):
    _inherit = "shrimp.verification"

    def _cerrar_trato_aceptado(self):
        res = super()._cerrar_trato_aceptado()
        tx = self.transaction_id
        if tx and tx.state == "confirmed":
            tx._shrimp_close_verified_purchase()
        return res


class ShrimpTransactionVerifiedClose(models.Model):
    _inherit = "shrimp.transaction"

    def _shrimp_verified_closable(self):
        """Compra verificada, confirmada y con el informe aceptado por ambas."""
        self.ensure_one()
        ver = self.sudo().verification_ids[:1]
        return bool(self.state == "confirmed" and ver and ver.acceptance_state == "closed"
                    and ver.state in ("approved", "approved_obs", "declared"))

    def _shrimp_close_verified_purchase(self, silent=False):
        """Recepción acreditada por la verificación: lote del comprador y
        compra completada. Devuelve las compras cerradas (cada una en su
        savepoint: un fallo no tumba la aceptación del informe)."""
        cerradas = self.browse()
        for rec in self:
            if not rec._shrimp_verified_closable():
                continue
            try:
                with self.env.cr.savepoint():
                    rec.sudo().with_context(
                        shrimp_reception_skip_date=True, shrimp_reception_verified=True,
                        shrimp_silent=silent,
                    ).action_receive()
                cerradas |= rec
            except Exception:  # noqa: BLE001 — el cron lo reintenta
                _logger.exception("No se pudo completar la compra verificada %s", rec.name)
        return cerradas

    def _shrimp_after_verified_reception(self):
        self.ensure_one()
        ver = self.sudo().verification_ids[:1]
        if ver:
            self.sudo()._message_log(body=_(
                "Recepción acreditada por la verificación %(v)s (informe aceptado por las dos "
                "partes): la compra queda completada y el lote pasa al inventario del comprador.")
                % {"v": ver.name})
        return super()._shrimp_after_verified_reception()

    @api.model
    def _cron_reception_and_delivery(self, limit=500):
        try:
            self._shrimp_cron_close_verified(limit)
        except Exception:  # noqa: BLE001 — no frena el resto del cron
            _logger.exception("Cierre de compras verificadas: fallo del cron")
        return super()._cron_reception_and_delivery(limit=limit)

    @api.model
    def _shrimp_cron_close_verified(self, limit=500):
        """Red de seguridad: verificadas confirmadas con el informe aceptado."""
        pendientes = self.sudo().search([
            ("state", "=", "confirmed"), ("verification_ids.acceptance_state", "=", "closed"),
        ], limit=limit, order="id asc")
        return pendientes._shrimp_close_verified_purchase()
