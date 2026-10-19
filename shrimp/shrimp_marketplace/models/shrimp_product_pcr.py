"""PCR positivo bloquea el lote (decisión D-30).

REGLA
  Un lote está BLOQUEADO cuando su ficha de calidad MÁS RECIENTE (fecha de
  muestreo, luego la última registrada) tiene algún PCR «positivo»
  (WSSV, IHHNV, AHPND o EHP). Mientras lo esté:

  * se despublica solo (pasa a borrador) y no se puede volver a publicar:
    ni desde el portal, ni por la API, ni desde el back-office;
  * no se puede comprar por ningún camino (motivo_no_comprable, que usan la
    compra directa, la verificada, la reserva de cosecha y la API);
  * se avisa al vendedor y a los compradores con compras abiertas (no
    recibidas) de ese lote, que pueden cancelarlas SIN COSTO y SIN strike al
    vendedor (causa «PCR positivo»: es un evento sanitario, no un
    incumplimiento).

  Se desbloquea registrando una ficha NUEVA (posterior) sin ningún PCR
  positivo. La ficha positiva no se corrige ni se borra desde el portal: el
  resultado queda en el historial del lote (solo la plataforma puede
  corregir un error de carga).
"""
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .shrimp_product_quality import PCR_FIELDS

_logger = logging.getLogger(__name__)

# Compras de un lote que todavía no se recibieron (el comprador puede
# cancelarlas sin costo si el lote resulta PCR positivo).
PCR_OPEN_TX_STATES = ("confirmed", "pending_verification", "pending_acceptance")
PCR_RECEIVED = ("received", "auto", "verified")


class ShrimpProductQualityPcr(models.Model):
    _inherit = "shrimp.product.quality"

    def _shrimp_pcr_locked_for_user(self):
        """La ficha positiva solo la corrige la plataforma (interno gestor)."""
        user = self.env.user
        return not (user._is_superuser() or (
            not user.share and user.has_group("shrimp_marketplace.group_shrimp_manager")))

    @api.model_create_multi
    def create(self, vals_list):
        productos = self.env["shrimp.product"].browse(
            [v.get("product_id") for v in vals_list if v.get("product_id")])
        antes = productos._shrimp_pcr_blocked_ids()
        records = super().create(vals_list)
        records.mapped("product_id")._shrimp_pcr_sync(antes)
        return records

    def write(self, vals):
        criticos = {f for f, _l in PCR_FIELDS} | {"sample_date", "product_id"}
        if criticos & set(vals) and self._shrimp_pcr_locked_for_user():
            for rec in self.filtered(lambda r: r.pcr_status == "positivo"):
                cambia = any(rec[f] != vals[f] for f, _l in PCR_FIELDS if f in vals)
                if "sample_date" in vals and fields.Date.to_date(vals["sample_date"]) != rec.sample_date:
                    cambia = True
                if "product_id" in vals and vals["product_id"] != rec.product_id.id:
                    cambia = True
                if cambia:
                    raise ValidationError(_(
                        "La ficha del %s tiene un PCR positivo y no se puede corregir: el resultado "
                        "queda en el historial del lote. Para desbloquearlo registra una ficha NUEVA "
                        "con un muestreo posterior y sin PCR positivos.")
                        % fields.Date.to_string(rec.sample_date))
        productos = self.mapped("product_id")
        antes = productos._shrimp_pcr_blocked_ids()
        res = super().write(vals)
        (productos | self.mapped("product_id"))._shrimp_pcr_sync(antes)
        return res

    def unlink(self):
        if self._shrimp_pcr_locked_for_user() and self.filtered(lambda r: r.pcr_status == "positivo"):
            raise ValidationError(_(
                "Una ficha con PCR positivo no se puede borrar: el resultado queda en el historial "
                "del lote. Para desbloquearlo registra una ficha nueva sin PCR positivos."))
        productos = self.mapped("product_id")
        antes = productos._shrimp_pcr_blocked_ids()
        res = super().unlink()
        productos.exists()._shrimp_pcr_sync(antes)
        return res


class ShrimpProductPcr(models.Model):
    _inherit = "shrimp.product"

    pcr_blocked = fields.Boolean(
        string="Bloqueado por PCR positivo", compute="_compute_pcr_blocked",
        search="_search_pcr_blocked",
        help="La ficha de calidad más reciente tiene algún PCR positivo: el lote no se publica "
             "ni se vende hasta registrar una ficha nueva sin positivos.")

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def _shrimp_pcr_blocked_ids(self):
        """ids (de self, o de todos si self está vacío y se pide con
        all_products) cuya ficha más reciente es PCR positivo."""
        ids = [i for i in self.ids if isinstance(i, int)]
        if not ids:
            return set()
        self.env["shrimp.product.quality"].flush_model(
            ["product_id", "sample_date", "pcr_status"])
        self.env.cr.execute("""
            SELECT product_id FROM (
                SELECT DISTINCT ON (product_id) product_id, pcr_status
                  FROM shrimp_product_quality
                 WHERE product_id = ANY(%s)
              ORDER BY product_id, sample_date DESC, id DESC) ultima
             WHERE pcr_status = 'positivo'
        """, [ids])
        return {r[0] for r in self.env.cr.fetchall()}

    @api.depends("quality_ids.pcr_status", "quality_ids.sample_date")
    def _compute_pcr_blocked(self):
        bloqueados = self._shrimp_pcr_blocked_ids()
        for rec in self:
            rec.pcr_blocked = rec.id in bloqueados

    def _search_pcr_blocked(self, operator, value):
        if operator in ("in", "not in"):
            valores = {bool(v) for v in value}
            quiere = True in valores
            if operator == "not in":
                quiere = not quiere
        elif operator in ("=", "!="):
            quiere = bool(value) if operator == "=" else not bool(value)
        else:
            raise NotImplementedError(operator)
        self.env["shrimp.product.quality"].flush_model(
            ["product_id", "sample_date", "pcr_status"])
        self.env.cr.execute("""
            SELECT product_id FROM (
                SELECT DISTINCT ON (product_id) product_id, pcr_status
                  FROM shrimp_product_quality
              ORDER BY product_id, sample_date DESC, id DESC) ultima
             WHERE pcr_status = 'positivo'
        """)
        ids = [r[0] for r in self.env.cr.fetchall()]
        return [("id", "in" if quiere else "not in", ids)]

    def _shrimp_pcr_block_text(self):
        self.ensure_one()
        ficha = self._shrimp_latest_quality()
        positivos = [label for f, label in PCR_FIELDS if ficha and ficha[f] == "positivo"]
        return _("PCR positivo (%(p)s) en el muestreo del %(f)s") % {
            "p": ", ".join(positivos) or "?",
            "f": fields.Date.to_string(ficha.sample_date) if ficha else "?"}

    # ------------------------------------------------------------------
    # Publicar / comprar
    # ------------------------------------------------------------------
    def _shrimp_publish_problems(self, check_stock=True):
        problemas = super()._shrimp_publish_problems(check_stock=check_stock)
        if self.id and self.pcr_blocked:
            problemas.append(_(
                "el lote está bloqueado por %s; registra una ficha de calidad nueva sin PCR "
                "positivos para volver a publicarlo") % self._shrimp_pcr_block_text())
        return problemas

    def motivo_no_comprable(self, partner, role=None):
        motivo = super().motivo_no_comprable(partner, role=role)
        if motivo:
            return motivo
        if self.id and self.sudo().pcr_blocked:
            return _("Lote bloqueado: %s.") % self.sudo()._shrimp_pcr_block_text()
        return motivo

    def _update_state_from_stock(self):
        bloqueados = self.filtered(lambda r: r.id and r.sudo().pcr_blocked)
        res = super(ShrimpProductPcr, self - bloqueados)._update_state_from_stock()
        for rec in bloqueados:
            # Nunca vuelve a «publicado» solo; con stock queda en borrador.
            if float(rec.available_qty or 0.0) > 0:
                if rec.state in ("published", "sold"):
                    rec.sudo().with_context(shrimp_system_flow=True).write({"state": "draft"})
            elif rec.state == "published":
                rec.sudo().with_context(shrimp_system_flow=True).write({"state": "sold"})
        return res

    # ------------------------------------------------------------------
    # Transiciones: bloquear / desbloquear
    # ------------------------------------------------------------------
    def _shrimp_pcr_sync(self, antes):
        """Aplica el cambio de estado PCR (antes = ids bloqueados antes)."""
        productos = self.exists()
        if not productos:
            return
        productos.invalidate_recordset(["pcr_blocked"])
        ahora = productos._shrimp_pcr_blocked_ids()
        for prod in productos:
            if prod.id in ahora and prod.id not in antes:
                prod._shrimp_pcr_block()
            elif prod.id in antes and prod.id not in ahora:
                prod._shrimp_pcr_unblock()

    def _shrimp_pcr_block(self):
        self.ensure_one()
        prod = self.sudo()
        motivo = prod._shrimp_pcr_block_text()
        estaba_publicado = prod.state == "published"
        if prod.state in ("published", "sold"):
            prod.with_context(shrimp_system_flow=True).write({"state": "draft"})
        abiertas = prod._shrimp_pcr_open_purchases()
        texto = _(
            "El lote «%(n)s» quedó BLOQUEADO por %(m)s.%(d)s No se puede publicar ni vender hasta "
            "que registres una ficha de calidad NUEVA, con un muestreo posterior, sin ningún PCR "
            "positivo.") % {
                "n": prod.name, "m": motivo,
                "d": (" " + _("Se despublicó automáticamente.")) if estaba_publicado else ""}
        if abiertas:
            texto += " " + _(
                "Los compradores de %s compra(s) abierta(s) de este lote fueron avisados y pueden "
                "cancelarlas sin costo (sin strike para ti).") % len(abiertas)
        prod._shrimp_notify_seller(_("Lote %s bloqueado por PCR positivo") % prod.name, texto)
        for tx in abiertas:
            tx._shrimp_notify("buyer", _("PCR positivo en el lote de tu compra %s") % tx.name, _(
                "El lote «%(n)s» de tu compra %(r)s tiene %(m)s. La plataforma lo bloqueó: ya no se "
                "vende. Puedes cancelar la compra sin costo desde la página de la compra; si "
                "prefieres seguir con ella, no tienes que hacer nada.") % {
                    "n": prod.name, "r": tx.name, "m": motivo})
        _logger.info("Lote %s bloqueado por PCR positivo (%s compras abiertas)", prod.id, len(abiertas))
        return abiertas

    def _shrimp_pcr_unblock(self):
        self.ensure_one()
        prod = self.sudo()
        prod._shrimp_notify_seller(_("Lote %s desbloqueado") % prod.name, _(
            "La ficha de calidad más reciente del lote «%s» ya no tiene PCR positivos: el lote "
            "quedó desbloqueado y puedes volver a publicarlo.") % prod.name)

    def _shrimp_pcr_open_purchases(self):
        self.ensure_one()
        return self.env["shrimp.transaction"].sudo().search([
            ("product_id", "=", self.id), ("state", "in", PCR_OPEN_TX_STATES),
        ]).filtered(lambda t: t.reception_status not in PCR_RECEIVED)

    def _shrimp_notify_seller(self, subject, text):
        """Aviso (correo y chatter) a los usuarios de la cuenta vendedora con
        permiso de vender en el perfil del lote."""
        self.ensure_one()
        seller = self.seller_partner_id
        users = self.env["shrimp.transaction"]._shrimp_party_users(
            seller, self.seller_role, "sell")
        destinatarios = users.mapped("partner_id").filtered("email") or seller.filtered("email")
        url = "%s/marketplace/products/%s/quality" % (self.get_base_url(), self.uuid_ref)
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("<p>%s</p><p><a href=\"%s\">%s</a></p>") % (text, url, _("Ver el lote")),
                subject=subject, partner_ids=destinatarios.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 — un aviso nunca tumba el proceso
            _logger.warning("No se pudo avisar al vendedor del lote %s", self.id, exc_info=True)


class ShrimpTransactionPcr(models.Model):
    _inherit = "shrimp.transaction"

    def shrimp_pcr_cancel_available(self):
        """True si el comprador puede cancelar sin costo porque el lote de la
        compra quedó bloqueado por PCR positivo y todavía no la recibió."""
        self.ensure_one()
        tx = self.sudo()
        return bool(tx.state in PCR_OPEN_TX_STATES
                    and tx.reception_status not in PCR_RECEIVED
                    and tx.product_id and tx.product_id.pcr_blocked)

    def shrimp_buyer_cancel_cause(self):
        causa = super().shrimp_buyer_cancel_cause()
        if not causa and self.shrimp_pcr_cancel_available():
            return "pcr_positive"
        return causa

    def _shrimp_buyer_cancel(self, causa, nota):
        if causa == "pcr_positive":
            return self._shrimp_cancel_apply(
                "buyer", "pcr_positive",
                nota or _("El lote resultó PCR positivo: %s.")
                % self.sudo().product_id._shrimp_pcr_block_text(),
                seller_fault=False)
        return super()._shrimp_buyer_cancel(causa, nota)
