"""LOPDP (hueco #7): claves de API y webhooks en la copia de los datos y en
la anonimización; registros técnicos de la API en la purga de retención."""
from odoo import _, api, models


class ResPartnerLopdpApi(models.Model):
    _inherit = "res.partner"

    def _shrimp_lopdp_export_extra(self, datos):
        datos = super()._shrimp_lopdp_export_extra(datos)
        users = self._shrimp_lopdp_users()
        Key = self.env["shrimp.api.key"].sudo().with_context(active_test=False)
        datos["api_keys"] = [{
            "name": k.name, "key": k.display_key, "scopes": sorted(k.scope_ids.mapped("code")),
            "status": k.status, "expires_at": k.expires_at and k.expires_at.isoformat(),
            "last_used": k.last_used and k.last_used.isoformat(), "last_ip": k.last_ip,
            "owner": k.user_id.login}
            for k in Key.search([("user_id", "in", users.ids), ("key_prefix", "!=", False)])]
        Sub = self.env["shrimp.webhook.subscription"].sudo().with_context(active_test=False)
        datos["webhooks"] = [{"name": s.name, "url": s.url, "events": s.event_types,
                              "active": s.active}
                             for s in Sub.search([("partner_id", "in", self._shrimp_lopdp_partner_ids())])]
        return datos

    def _shrimp_lopdp_anonymize_extra(self, dry_run=False):
        informe = super()._shrimp_lopdp_anonymize_extra(dry_run=dry_run)
        Sub = self.env["shrimp.webhook.subscription"].sudo().with_context(active_test=False)
        subs = Sub.search([("partner_id", "in", self._shrimp_lopdp_partner_ids())])
        informe.append(_("Suscripciones a webhooks borradas: %s (las claves de API de sus usuarios "
                         "se revocan)") % len(subs))
        if subs and not dry_run:
            subs.unlink()
        return informe


class ShrimpDataRetentionRunApi(models.Model):
    _inherit = "shrimp.data.retention.run"

    @api.model
    def _shrimp_log_specs(self):
        return super()._shrimp_log_specs() + [
            ("shrimp.api.idempotency", "create_date", _("respuestas guardadas de la API (idempotencia)")),
            ("shrimp.webhook.delivery", "create_date", _("entregas de webhooks")),
        ]
