"""Caducidad de las publicaciones (decisión D-31).

REGLA
  Una publicación vence en:
    1. «Disponible hasta» (available_to), si el vendedor la puso;
    2. si no, N días después de la «Fecha tentativa de entrega»
       (Ajustes › CamaronMarket › Marketplace, 15 por defecto);
    3. si tampoco hay fecha de entrega, N días después de la fecha de
       publicación.
  Un cron (cada hora) avisa al vendedor X días antes (3 por defecto) y, al
  vencer, pasa la publicación a borrador y le avisa. Para volver a
  publicarla tiene que poner una fecha vigente: no se publica un lote cuya
  fecha «Disponible hasta» ya pasó, ni uno cuya entrega tentativa quedó
  atrás hace más de N días.

  La migración 19.0.1.38.0 no vence en masa las publicaciones viejas: a las
  que ya habían pasado su fecha (o la tenían a menos del aviso) les da
  «Disponible hasta» = hoy + 30 días, así el vendedor recibe el aviso antes.
"""
import logging
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

EXPIRY_DAYS_PARAM = "shrimp_marketplace.publication_expiry_days"
EXPIRY_DAYS_DEFAULT = 15
EXPIRY_NOTICE_PARAM = "shrimp_marketplace.publication_expiry_notice_days"
EXPIRY_NOTICE_DEFAULT = 3
# Gracia de la migración para las publicaciones sin caducidad útil.
EXPIRY_MIGRATION_DAYS = 30


class ShrimpProductExpiry(models.Model):
    _inherit = "shrimp.product"

    publication_expires_at = fields.Datetime(
        string="La publicación vence", compute="_compute_publication_expires_at",
        help="«Disponible hasta»; si no hay, N días después de la fecha tentativa de entrega "
             "(o de la publicación). Al vencer, la publicación pasa a borrador.")
    # Columna nueva: el vencimiento para el que ya se mandó el aviso previo
    # (si el vendedor mueve la fecha, se vuelve a avisar para la nueva).
    expiry_notice_for = fields.Datetime(
        string="Aviso de vencimiento enviado para", copy=False, readonly=True)

    @api.model
    def _shrimp_expiry_days(self):
        return self.env["shrimp.settings"].get_int(
            EXPIRY_DAYS_PARAM, EXPIRY_DAYS_DEFAULT, minimum=0, maximum=3650)

    @api.model
    def _shrimp_expiry_notice_days(self):
        return self.env["shrimp.settings"].get_int(
            EXPIRY_NOTICE_PARAM, EXPIRY_NOTICE_DEFAULT, minimum=0, maximum=365)

    def _shrimp_expiry_at(self, publishing=False):
        """Cuándo vence la publicación (Datetime UTC ingenuo) o None.
        `publishing`: se está publicando ahora (la fecha de publicación de
        referencia es hoy, no la de una publicación anterior)."""
        self.ensure_one()
        if self.available_to:
            return self.available_to
        dias = self._shrimp_expiry_days()
        Tx = self.env["shrimp.transaction"]
        if self.expected_delivery_date:
            return Tx.shrimp_noon_utc(
                fields.Date.to_date(self.expected_delivery_date) + timedelta(days=dias),
                self.seller_partner_id)
        base = fields.Datetime.now() if publishing else (self.published_date or self.create_date)
        return (base + timedelta(days=dias)) if base else None

    @api.depends("available_to", "expected_delivery_date", "published_date")
    def _compute_publication_expires_at(self):
        for rec in self:
            rec.publication_expires_at = rec._shrimp_expiry_at() if rec.id else False

    def _shrimp_publish_problems(self, check_stock=True):
        problemas = super()._shrimp_publish_problems(check_stock=check_stock)
        # Al cargar datos (demo, XML) no se juzga la fecha: la demo trae lotes
        # históricos y demo_99 les da una caducidad desde hoy.
        # Solo al PUBLICAR (borrador → publicado). Un lote que nace publicado
        # (cosecha, datos) o vuelve de «vendido» al reponer stock no se frena
        # aquí: si ya pasó su fecha, el cron lo vence en la próxima pasada.
        if not self.id or not check_stock or self.env.context.get("install_mode"):
            return problemas
        vence = self._shrimp_expiry_at(publishing=True)
        if vence and vence <= fields.Datetime.now():
            if self.available_to:
                problemas.append(_("la fecha «Disponible hasta» ya pasó; pon una fecha futura"))
            else:
                problemas.append(_(
                    "la fecha tentativa de entrega quedó atrás hace más de %s días; actualízala "
                    "o pon «Disponible hasta»") % self._shrimp_expiry_days())
        return problemas

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_expiry_candidates_domain(self, tope):
        """Publicadas que podrían vencer antes de `tope` (filtro grueso; el
        cálculo fino lo hace _shrimp_expiry_at)."""
        dias = self._shrimp_expiry_days()
        tope_fecha = fields.Date.to_date(tope) - timedelta(days=dias) + timedelta(days=1)
        return [
            ("active", "=", True), ("state", "=", "published"),
            "|", "|",
            ("available_to", "<=", tope),
            "&", ("available_to", "=", False), ("expected_delivery_date", "<=", tope_fecha),
            "&", "&", ("available_to", "=", False), ("expected_delivery_date", "=", False),
            ("published_date", "<=", tope - timedelta(days=dias)),
        ]

    @api.model
    def _cron_publication_expiry(self, limit=2000):
        """Avisa X días antes y vence las publicaciones cumplidas."""
        ahora = fields.Datetime.now()
        aviso = self._shrimp_expiry_notice_days()
        tope = ahora + timedelta(days=aviso)
        candidatos = self.sudo().search(
            self._shrimp_expiry_candidates_domain(tope), limit=limit, order="id")
        vencidos = avisados = 0
        for prod in candidatos:
            vence = prod._shrimp_expiry_at()
            if not vence or vence > tope:
                continue
            try:
                with self.env.cr.savepoint():
                    if vence <= ahora:
                        prod._shrimp_expire_publication(vence)
                        vencidos += 1
                    elif aviso and prod.expiry_notice_for != vence:
                        prod._shrimp_expiry_warn(vence)
                        avisados += 1
            except Exception:  # noqa: BLE001 — un lote no frena a los demás
                _logger.exception("Caducidad de la publicación %s", prod.id)
        if vencidos or avisados:
            _logger.info("Publicaciones: %s vencidas, %s avisos previos", vencidos, avisados)
        return {"expired": vencidos, "warned": avisados}

    def _shrimp_fmt_expiry(self, vence):
        return self.env["shrimp.transaction"].shrimp_fmt_dt(vence, self.seller_partner_id)

    def _shrimp_expiry_warn(self, vence):
        self.ensure_one()
        self.with_context(shrimp_system_flow=True).write({"expiry_notice_for": vence})
        self._shrimp_notify_seller(_("Tu publicación «%s» vence pronto") % self.name, _(
            "La publicación del lote «%(n)s» vence el %(f)s. Al vencer pasará a borrador y dejará "
            "de verse en el marketplace. Si el lote sigue disponible, actualiza «Disponible "
            "hasta» (o la fecha tentativa de entrega) antes de esa fecha.") % {
                "n": self.name, "f": self._shrimp_fmt_expiry(vence)})

    def _shrimp_expire_publication(self, vence):
        self.ensure_one()
        self.with_context(shrimp_system_flow=True).write(
            {"state": "draft", "expiry_notice_for": vence})
        self._shrimp_notify_seller(_("Tu publicación «%s» venció") % self.name, _(
            "La publicación del lote «%(n)s» venció el %(f)s y pasó a borrador: ya no se ve en el "
            "marketplace. Si el lote sigue disponible, pon una fecha «Disponible hasta» vigente y "
            "vuelve a publicarlo.") % {"n": self.name, "f": self._shrimp_fmt_expiry(vence)})

    # ------------------------------------------------------------------
    # Migración
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_expiry_migrate(self):
        """Da «Disponible hasta» = hoy + 30 días a las publicaciones que ya
        vencieron (o vencen antes del aviso) para no despublicarlas en masa.
        Devuelve cuántas tocó."""
        ahora = fields.Datetime.now()
        tope = ahora + timedelta(days=self._shrimp_expiry_notice_days() + 1)
        nueva = ahora + timedelta(days=EXPIRY_MIGRATION_DAYS)
        publicadas = self.sudo().search([("active", "=", True), ("state", "=", "published")])
        tocar = publicadas.filtered(lambda p: not p._shrimp_expiry_at() or p._shrimp_expiry_at() <= tope)
        if tocar:
            tocar.with_context(shrimp_system_flow=True, skip_evolution_snapshot=True,
                               tracking_disable=True).write({"available_to": nueva})
        return len(tocar)


class ResConfigSettingsExpiry(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_publication_expiry_days = fields.Integer(
        string="Días de publicación tras la entrega", default=EXPIRY_DAYS_DEFAULT,
        help="Una publicación sin «Disponible hasta» vence estos días después de su fecha "
             "tentativa de entrega (o de su publicación, si no tiene fecha de entrega).")
    shrimp_publication_expiry_notice_days = fields.Integer(
        string="Aviso antes del vencimiento (días)", default=EXPIRY_NOTICE_DEFAULT,
        help="Días antes del vencimiento en que se avisa al vendedor (0 = sin aviso previo).")

    @api.model
    def _shrimp_param_specs(self):
        specs = super()._shrimp_param_specs()
        specs.update({
            "shrimp_publication_expiry_days": (EXPIRY_DAYS_PARAM, EXPIRY_DAYS_DEFAULT, 0, 3650),
            "shrimp_publication_expiry_notice_days": (
                EXPIRY_NOTICE_PARAM, EXPIRY_NOTICE_DEFAULT, 0, 365),
        })
        return specs
