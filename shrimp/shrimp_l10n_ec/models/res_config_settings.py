from markupsafe import Markup, escape

from odoo import _, api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_service_journal_id = fields.Many2one(
        related="company_id.shrimp_service_journal_id", readonly=False)
    shrimp_service_sri_point_id = fields.Many2one(
        related="company_id.shrimp_service_sri_point_id", readonly=False)
    shrimp_service_payment_id = fields.Many2one(
        related="company_id.shrimp_service_payment_id", readonly=False)
    shrimp_sri_auto_emit = fields.Boolean(
        related="company_id.shrimp_sri_auto_emit", readonly=False)

    # Quién corrige la facturación (cobros agotados, rechazos del SRI). Vacío:
    # el primer administrador de CamaronMarket.
    shrimp_billing_responsible_user_id = fields.Many2one(
        "res.users", string="Responsable de facturación SRI",
        config_parameter="shrimp_marketplace.billing_responsible_user_id",
        domain="[('share', '=', False)]",
        help="Recibe una actividad «Por hacer» en cada cobro que agota sus intentos o que "
             "el SRI rechaza, para corregirlo y reenviarlo. Si se deja vacío, el primer "
             "administrador de CamaronMarket.")
    # Guardados con _shrimp_param_specs (ver shrimp_marketplace).
    shrimp_sri_credit_auto_emit = fields.Boolean(
        string="Enviar las notas de crédito al SRI automáticamente", default=True,
        help="Al anular un cobro ya facturado y AUTORIZADO, la nota de crédito (04) se "
             "firma y se envía al SRI como la factura. Desactivado: queda contabilizada y "
             "el responsable de facturación recibe una actividad para revisarla y emitirla.")
    shrimp_sri_final_consumer = fields.Boolean(
        string="Facturar a consumidor final si el pagador no tiene RUC/cédula",
        default=True,
        help="Solo si el total de la factura no pasa de USD 50 (límite del SRI). Por encima, "
             "el cobro espera y se le piden los datos fiscales al pagador. Ojo: una factura a "
             "consumidor final no admite nota de crédito ni anulación.")
    shrimp_sri_daily_summary = fields.Boolean(
        string="Resumen diario de la cola SRI", default=True,
        help="Correo diario a los administradores de CamaronMarket con los cobros pendientes, "
             "en error o rechazados por el SRI (solo si hay alguno, o si falta configuración).")

    shrimp_sri_config_status = fields.Html(
        string="Estado de la configuración SRI", compute="_compute_shrimp_sri_status",
        sanitize=False)
    shrimp_sri_config_ok = fields.Boolean(compute="_compute_shrimp_sri_status")
    shrimp_sri_backlog_count = fields.Integer(
        string="Cobros por reencolar", compute="_compute_shrimp_sri_status")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_sri_credit_auto_emit": ("shrimp_l10n_ec.credit_auto_emit", True, None, None),
            "shrimp_sri_final_consumer": ("shrimp_l10n_ec.final_consumer_fallback", True, None, None),
            "shrimp_sri_daily_summary": ("shrimp_l10n_ec.daily_summary", True, None, None),
        })
        return specs

    @api.depends("company_id", "shrimp_service_journal_id", "shrimp_service_sri_point_id",
                 "shrimp_service_payment_id", "shrimp_sri_auto_emit")
    def _compute_shrimp_sri_status(self):
        Charge = self.env["shrimp.charge"].sudo()
        for rec in self:
            company = rec.company_id
            problemas = company._shrimp_sri_config_problems() if company else []
            rec.shrimp_sri_config_ok = bool(company.ec_sri_enabled) and not problemas
            rec.shrimp_sri_backlog_count = Charge.search_count(
                Charge._shrimp_sri_backlog_domain(company)) if company else 0
            if not company.ec_sri_enabled:
                rec.shrimp_sri_config_status = Markup(
                    "<div class='alert alert-warning mb-0'>%s</div>") % _(
                    "El SRI no está habilitado en esta compañía: las facturas de servicio se "
                    "contabilizan sin comprobante electrónico.")
            elif problemas:
                rec.shrimp_sri_config_status = Markup(
                    "<div class='alert alert-danger mb-0'><b>%s</b><ul class='mb-0'>%s</ul></div>"
                ) % (_("Facturación electrónica INCOMPLETA: ningún cobro se factura hasta "
                       "corregir esto."),
                     Markup("").join(Markup("<li>%s</li>") % escape(p) for p in problemas))
            else:
                rec.shrimp_sri_config_status = Markup(
                    "<div class='alert alert-success mb-0'>%s</div>") % _(
                    "Configuración completa: las facturas y notas de crédito de servicio se "
                    "emiten al SRI (ambiente: %s).") % dict(
                        company._fields["ec_sri_environment"].selection).get(
                        company.ec_sri_environment)

    def action_shrimp_sri_requeue_backlog(self):
        """Botón: reencolar el backlog (pendientes/errores con intentos
        agotados). Solo con la configuración completa; el cron los factura de
        200 en 200 por hora."""
        self.ensure_one()
        self.env["shrimp.charge"].check_access("write")
        n = self.env["shrimp.charge"]._shrimp_sri_requeue_backlog(self.company_id)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": _("Cola SRI"), "type": "success", "sticky": False,
                       "message": _("%s cobros vuelven a la cola. El proceso automático los "
                                    "factura de 200 en 200 cada hora.") % n,
                       "next": {"type": "ir.actions.act_window_close"}},
        }
