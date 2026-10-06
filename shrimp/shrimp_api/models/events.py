"""Dónde nacen los eventos de webhook.

Cada override compara el estado ANTES y DESPUÉS del write y solo emite si de
verdad cambió: un "guardar" sin cambios no puede convertirse en un aviso a
todos los integradores. La emisión solo encola (ver webhook.py); el envío lo
hace el cron.
"""

from odoo import api, models

VERDICT_STATES = ("approved", "approved_obs", "rejected")


def _emit(env, event_type, records):
    if records:
        env["shrimp.webhook.delivery"].sudo()._emit(event_type, records)


def _changed(records, before, field, to=None):
    out = records.browse()
    for rec in records:
        old = before.get(rec.id)
        new = rec[field]
        if old != new and (to is None or new in to):
            out |= rec
    return out


def _snapshot(records, field):
    return {rec.id: rec[field] for rec in records}


class ShrimpTransaction(models.Model):
    _inherit = "shrimp.transaction"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        _emit(self.env, "transaction.state_changed", records)
        return records

    def write(self, vals):
        if "state" not in vals:
            return super().write(vals)
        before = _snapshot(self, "state")
        res = super().write(vals)
        _emit(self.env, "transaction.state_changed", _changed(self, before, "state"))
        return res


class ShrimpCheckRequest(models.Model):
    _inherit = "shrimp.check.request"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        _emit(self.env, "check_request.created", records)
        return records


class ShrimpVerification(models.Model):
    _inherit = "shrimp.verification"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        _emit(self.env, "verification.assigned", records)
        return records

    def write(self, vals):
        watch = {"state", "technician_partner_id"} & set(vals)
        if not watch:
            return super().write(vals)
        before_state = _snapshot(self, "state")
        before_tech = {r.id: r.technician_partner_id.id for r in self}
        res = super().write(vals)
        if "technician_partner_id" in vals:
            moved = self.filtered(
                lambda r: r.technician_partner_id and before_tech.get(r.id) != r.technician_partner_id.id)
            _emit(self.env, "verification.assigned", moved)
        if "state" in vals:
            _emit(self.env, "verification.verdict_issued",
                  _changed(self, before_state, "state", VERDICT_STATES))
            _emit(self.env, "verification.declared_submitted",
                  _changed(self, before_state, "state", ("declared",)))
        return res


class ShrimpVerificationAcceptance(models.Model):
    _inherit = "shrimp.verification.acceptance"

    def write(self, vals):
        # Deshacer restaura la postura anterior (que puede ser «aceptada»):
        # eso no es una decisión nueva, se avisa con acceptance_reverted.
        if "decision" not in vals or self.env.context.get("shrimp_signoff_undo"):
            return super().write(vals)
        before = _snapshot(self, "decision")
        res = super().write(vals)
        decided = _changed(self, before, "decision", ("accepted", "counter", "rejected"))
        _emit(self.env, "verification.acceptance_decided", decided.mapped("verification_id"))
        return res


class ShrimpDispatch(models.Model):
    _inherit = "shrimp.dispatch"

    def write(self, vals):
        if not {"eta", "actual_arrival"} & set(vals):
            return super().write(vals)
        before_eta = _snapshot(self, "eta")
        before_arr = _snapshot(self, "actual_arrival")
        res = super().write(vals)
        if "eta" in vals:
            _emit(self.env, "dispatch.eta_changed",
                  self.filtered(lambda r: r.eta and r.eta != before_eta.get(r.id)))
        if "actual_arrival" in vals:
            _emit(self.env, "dispatch.arrived",
                  self.filtered(lambda r: r.actual_arrival and not before_arr.get(r.id)))
        return res


class ShrimpPriceList(models.Model):
    _inherit = "shrimp.price.list"

    def write(self, vals):
        if "state" not in vals:
            return super().write(vals)
        before = _snapshot(self, "state")
        res = super().write(vals)
        _emit(self.env, "price_list.published", _changed(self, before, "state", ("published",)))
        return res


class ShrimpHarvestForecast(models.Model):
    _inherit = "shrimp.harvest.forecast"

    def write(self, vals):
        if "state" not in vals:
            return super().write(vals)
        before = _snapshot(self, "state")
        res = super().write(vals)
        # Solo la primera publicación: volver a "published" desde
        # "committed" (cuando se rompe un compromiso) no es una declaración nueva.
        _emit(self.env, "forecast.published", self.filtered(
            lambda r: r.state == "published" and before.get(r.id) == "draft"))
        return res


class ShrimpHarvestCommitment(models.Model):
    _inherit = "shrimp.harvest.commitment"

    _EVENT_BY_STATE = {
        "accepted": "commitment.accepted",
        "broken": "commitment.broken",
        "honored": "commitment.settled",
        "to_confirm": "harvest.confirmation_required",
    }

    def write(self, vals):
        # Volver a «pendiente de confirmar» al deshacer un rechazo no es una
        # confirmación nueva: se avisa con harvest.confirmation_reverted.
        if "state" not in vals or self.env.context.get("shrimp_signoff_undo"):
            return super().write(vals)
        before = _snapshot(self, "state")
        res = super().write(vals)
        changed = _changed(self, before, "state", tuple(self._EVENT_BY_STATE))
        for state, event in self._EVENT_BY_STATE.items():
            _emit(self.env, event, changed.filtered(lambda r, s=state: r.state == s))
        return res


class ShrimpProduct(models.Model):
    _inherit = "shrimp.product"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        _emit(self.env, "lot.published", records.filtered(lambda r: r.state == "published"))
        return records

    def write(self, vals):
        if "state" not in vals:
            return super().write(vals)
        before = _snapshot(self, "state")
        res = super().write(vals)
        _emit(self.env, "lot.published", _changed(self, before, "state", ("published",)))
        return res


class ShrimpCopackOffer(models.Model):
    _inherit = "shrimp.copack.offer"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        _emit(self.env, "copack.offer_received", records)
        return records


class ShrimpCopackOrder(models.Model):
    _inherit = "shrimp.copack.order"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        _emit(self.env, "copack.order_state_changed", records)
        return records

    def write(self, vals):
        watch = {"state", "acceptance_state"} & set(vals)
        if not watch:
            return super().write(vals)
        before_state = _snapshot(self, "state")
        before_acta = _snapshot(self, "acceptance_state")
        res = super().write(vals)
        if "state" in vals:
            _emit(self.env, "copack.order_state_changed", _changed(self, before_state, "state"))
        if "acceptance_state" in vals:
            _emit(self.env, "copack.acta_signed",
                  _changed(self, before_acta, "acceptance_state", ("closed",)))
        return res


class ShrimpExport(models.Model):
    _inherit = "shrimp.export"

    def write(self, vals):
        if "state" not in vals:
            return super().write(vals)
        before = _snapshot(self, "state")
        res = super().write(vals)
        _emit(self.env, "export.registered", _changed(self, before, "state", ("registered",)))
        return res


class ShrimpSignoffEvent(models.Model):
    """«Deshacer mi decisión» en cualquiera de las firmas de dos partes."""
    _inherit = "shrimp.signoff.event"

    _REVERT_EVENTS = {
        "shrimp.verification": "verification.acceptance_reverted",
        "shrimp.copack.order": "copack.signature_reverted",
        "shrimp.harvest.commitment": "harvest.confirmation_reverted",
    }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records.filtered(lambda r: r.kind == "undo"):
            code = self._REVERT_EVENTS.get(rec.parent_model)
            if code:
                _emit(self.env, code, self.env[rec.parent_model].browse(rec.parent_id).exists())
        return records
