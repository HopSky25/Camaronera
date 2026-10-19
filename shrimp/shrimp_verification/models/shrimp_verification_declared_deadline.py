"""D-41: plazo de la verificación declarada «en preparación» (auditoría, ME-12).

ANTES: una declaración en preparación no tenía plazo; el vendedor no tenía
salida y el stock quedaba reservado hasta que el comprador cancelara.

AHORA (decisión del usuario, definitiva):

* El informe declarado debe PRESENTARSE dentro de N días (5, Ajustes ›
  Verificación) desde la fecha de entrega de la compra (o desde la compra, si
  no tiene fecha o es posterior). Hasta las 23:59 del último día.
* Recordatorio a las dos partes el día anterior al vencimiento.
* Vencido sin presentar: la compra se CANCELA sin costo (servicio único de
  cancelación, causa «Verificación declarada no presentada a tiempo»), sin
  strike: el informe lo podía presentar cualquiera de las dos partes.
* El vendedor puede DESISTIR antes de presentarlo: es la cancelación del
  vendedor de siempre (shrimp.transaction.shrimp_cancel("seller")), con
  strike («Compra cancelada por el vendedor»).

Las declaraciones que ya existían al instalar esto no vencen de golpe: el
plazo cuenta como mínimo desde la fecha del -u (DECISIONS_SINCE_PARAM, lo fija
la migración 19.0.1.24.0).
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

DECLARED_DAYS_PARAM = "shrimp_verification.declared_days"
DECLARED_DAYS_DEFAULT = 5
DECISIONS_SINCE_PARAM = "shrimp_verification.decisions_rules_since"


class ShrimpVerificationDeclaredDeadline(models.Model):
    _inherit = "shrimp.verification"

    declared_deadline = fields.Datetime(string="Presentar el informe declarado hasta",
                                        compute="_compute_declared_deadline")

    @api.model
    def _shrimp_declared_days(self):
        return self.env["shrimp.settings"].get_int(
            DECLARED_DAYS_PARAM, DECLARED_DAYS_DEFAULT, minimum=1, maximum=365)

    @api.model
    def _shrimp_decisions_since(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(DECISIONS_SINCE_PARAM)
        try:
            return fields.Date.to_date(raw) if raw else False
        except (TypeError, ValueError):
            return False

    def _shrimp_declared_limit(self, days=None):
        self.ensure_one()
        if self.verification_mode != "declared":
            return False
        tx = self.transaction_id
        inicio = self.create_date or fields.Datetime.now()
        ref = tx.with_context(shrimp_public_tz=True).shrimp_local_date(inicio)
        if tx.delivery_date and tx.delivery_date > ref:
            ref = tx.delivery_date
        desde = self._shrimp_decisions_since()
        if desde and desde > ref:
            ref = desde
        return self._shrimp_end_of_day_utc(ref + timedelta(days=days or self._shrimp_declared_days()))

    @api.depends("verification_mode", "state", "transaction_id.delivery_date")
    def _compute_declared_deadline(self):
        dias = self._shrimp_declared_days()
        for rec in self:
            rec.declared_deadline = rec._shrimp_declared_limit(dias) \
                if rec.id and rec.verification_mode == "declared" \
                and rec.state == "declared_draft" else False

    @api.model
    def _shrimp_cron_declared(self, limit=500):
        dias = self._shrimp_declared_days()
        ahora = fields.Datetime.now()
        borradores = self.sudo().search([
            ("verification_mode", "=", "declared"), ("state", "=", "declared_draft"),
            ("transaction_id.state", "=", "pending_verification")], limit=limit, order="id asc")
        for ver in borradores:
            try:
                with self.env.cr.savepoint():
                    ver._shrimp_declared_step(dias, ahora)
            except Exception:  # noqa: BLE001
                _logger.exception("Plazo de la verificación declarada %s", ver.name)
        return True

    def _shrimp_declared_step(self, dias, ahora):
        self.ensure_one()
        tx = self.transaction_id.sudo()
        limite = self._shrimp_declared_limit(dias)
        if not limite:
            return
        if ahora > limite:
            tx._shrimp_cancel_apply(
                "system", "declared_overdue",
                _("El informe de la verificación declarada no se presentó dentro de %(d)s días "
                  "desde la fecha de entrega (vencía el %(f)s): la compra se cancela sin costo.")
                % {"d": dias, "f": tx.shrimp_fmt_dt(limite)})
            return
        clave = "ddcl_rem:%s" % limite.date().isoformat()
        if ahora >= limite - timedelta(hours=24) and clave not in tx._shrimp_marks():
            texto = _("El informe de la verificación declarada de la compra %(r)s tiene que "
                      "presentarse hasta el %(f)s. Si no se presenta, la compra se cancela sin "
                      "costo.") % {"r": tx.name, "f": tx.shrimp_fmt_dt(limite)}
            for lado in ("buyer", "seller"):
                tx._shrimp_notify(lado, _("Presenta el informe declarado de %s") % tx.name, texto)
            tx._shrimp_add_marks([clave])

