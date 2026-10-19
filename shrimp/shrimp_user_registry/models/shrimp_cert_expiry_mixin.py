"""Vigencia de los certificados (decisión D-13).

Lo comparten los certificados del socio (shrimp.user.certificate.line) y los
del producto (shrimp.product.certificate.line, en shrimp_marketplace):

* ``validity_state`` (almacenado, con filtro): Vigente · Por vencer (dentro
  de los días de aviso de Ajustes, 30 por defecto) · Vencido · Sin
  vencimiento. Depende de la fecha de expiración y de «hoy», así que el cron
  diario de shrimp_marketplace lo refresca (``_shrimp_refresh_validity``).
* ``expiry_notice_stage``: qué aviso se mandó ya (30 días antes / al vencer),
  para no repetirlo; vuelve a «sin aviso» si cambia la fecha de expiración.

Vencer NO bloquea nada (ni el perfil ni el producto): solo se marca y se
muestra «Vencido» en lugar de «Vigente». La única excepción es la
acreditación del verificador, que ya bloquea en shrimp_verification.
"""
from datetime import timedelta

from odoo import api, fields, models

VALIDITY_STATES = [
    ("valid", "Vigente"),
    ("expiring", "Por vencer"),
    ("expired", "Vencido"),
    ("no_expiry", "Sin vencimiento"),
]
NOTICE_STAGES = [
    ("none", "Sin aviso"),
    ("warned", "Aviso previo enviado"),
    ("expired", "Aviso de vencimiento enviado"),
]
# Mismo parámetro que el aviso de «Mi panel» (Ajustes › CamaronMarket).
ALERT_DAYS_PARAM = "shrimp_marketplace.cert_alert_days"
ALERT_DAYS_DEFAULT = 30


class ShrimpCertExpiryMixin(models.AbstractModel):
    _name = "shrimp.cert.expiry.mixin"
    _description = "Vigencia de un certificado (vencido / por vencer)"

    validity_state = fields.Selection(
        VALIDITY_STATES, string="Vigencia", compute="_compute_validity_state",
        store=True, index=True,
        help="Se recalcula cada día. Un certificado vencido se sigue mostrando, marcado como "
             "«Vencido»; no bloquea el perfil ni el producto.")
    expiry_notice_stage = fields.Selection(
        NOTICE_STAGES, string="Aviso de vencimiento", default="none", copy=False,
        readonly=True)

    @api.model
    def _shrimp_cert_alert_days(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(ALERT_DAYS_PARAM)
        try:
            dias = int(float(raw)) if raw not in (None, False, "") else ALERT_DAYS_DEFAULT
        except (TypeError, ValueError):
            dias = ALERT_DAYS_DEFAULT
        return dias if 1 <= dias <= 3650 else ALERT_DAYS_DEFAULT

    @api.model
    def _shrimp_cert_today(self):
        return fields.Date.context_today(self)

    @api.depends("expiry_date")
    def _compute_validity_state(self):
        hoy = self._shrimp_cert_today()
        tope = hoy + timedelta(days=self._shrimp_cert_alert_days())
        for rec in self:
            fecha = rec.expiry_date
            if not fecha:
                rec.validity_state = "no_expiry"
            elif fecha < hoy:
                rec.validity_state = "expired"
            elif fecha <= tope:
                rec.validity_state = "expiring"
            else:
                rec.validity_state = "valid"

    def shrimp_is_expired(self):
        """Vencido HOY (cálculo en vivo, para las plantillas)."""
        self.ensure_one()
        return bool(self.expiry_date and self.expiry_date < self._shrimp_cert_today())

    def write(self, vals):
        if "expiry_date" in vals and "expiry_notice_stage" not in vals:
            vals = dict(vals, expiry_notice_stage="none")
        return super().write(vals)

    @api.model
    def _shrimp_refresh_validity(self):
        """Recalcula la vigencia que cambió solo por el paso del tiempo (o
        por un cambio en los días de aviso). Devuelve los registros tocados."""
        hoy = self._shrimp_cert_today()
        tope = hoy + timedelta(days=self._shrimp_cert_alert_days())
        Model = self.sudo().with_context(active_test=False)
        viejos = Model.search([
            ("expiry_date", "!=", False),
            "|", "|", "|",
            "&", ("validity_state", "!=", "expired"), ("expiry_date", "<", hoy),
            "&", ("validity_state", "=", "valid"), ("expiry_date", "<=", tope),
            "&", ("validity_state", "=", "expiring"), ("expiry_date", ">", tope),
            ("validity_state", "in", (False, "no_expiry")),
        ])
        if viejos:
            self.env.add_to_compute(Model._fields["validity_state"], viejos)
            viejos.flush_recordset(["validity_state"])
        return viejos
