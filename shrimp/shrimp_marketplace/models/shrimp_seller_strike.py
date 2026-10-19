"""Strikes del vendedor por incumplir la entrega (sin multa en dinero).

CAUSAS (las pone el código, nunca el portal). D-42: UN strike como máximo
por compra, sean cuales sean las causas (las siguientes quedan anotadas en él).
  unnotified_delay    pasó la fecha acordada + la gracia (2 días) sin
                      despacho y sin aviso de retraso (cron);
  buyer_cancel_delay  el comprador canceló la compra por el retraso;
  delay_over_cap      el atraso total superó el tope (fecha original + 15
                      días) sin despacho (cron);
  seller_cancel       el vendedor canceló la venta (antes del despacho o de
                      la visita de la verificadora);
  platform_cancel     la plataforma canceló la compra (o resolvió una
                      incidencia como «no se entregó») por culpa del vendedor.
Un retraso AVISADO y aceptado (expresa o tácitamente) NO da strike: solo baja
el indicador «Entregas a tiempo %».

VIGENCIA: 90 días (Ajustes). La plataforma puede anular un strike con una
justificación (back-office, Administrador de CamaronMarket).

UMBRALES (Ajustes; strikes ACTIVOS = no anulados y no vencidos)
  3  aviso al vendedor + baja en el orden del catálogo + fuera de «destacados»;
  5  publicación suspendida (shrimp.seller.suspension): no puede publicar
     productos nuevos y los suyos salen del catálogo y no se pueden comprar;
     las ventas en curso siguen normalmente. D-42: se levanta SOLA cuando los
     strikes activos bajan del umbral (vencen a los 90 días o se anulan), con
     aviso al vendedor y a la plataforma; la plataforma también puede
     levantarla antes («Levantar suspensión» en la ficha del socio).
Los strikes no se muestran en público: solo «Entregas a tiempo %».
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from .shrimp_transaction_delay import shrimp_delay_ready

_logger = logging.getLogger(__name__)

STRIKE_EXPIRY_PARAM = "shrimp_marketplace.strike_expiry_days"
STRIKE_EXPIRY_DEFAULT = 90
STRIKE_WARNING_PARAM = "shrimp_marketplace.strike_warning_threshold"
STRIKE_WARNING_DEFAULT = 3
STRIKE_SUSPENSION_PARAM = "shrimp_marketplace.strike_suspension_threshold"
STRIKE_SUSPENSION_DEFAULT = 5

SUSPENDED_CACHE_KEY = "shrimp_suspended_partner_ids"

STRIKE_REASONS = [
    ("unnotified_delay", "Atraso sin aviso"),
    ("buyer_cancel_delay", "Compra cancelada por el comprador por retraso"),
    ("delay_over_cap", "Atraso total mayor al tope"),
    # Cancelación general (models/shrimp_transaction_cancel.py).
    ("seller_cancel", "Compra cancelada por el vendedor"),
    ("platform_cancel", "Compra cancelada por la plataforma por incumplimiento del vendedor"),
]


def _bool_values(operator, value):
    """Valores booleanos que pide un dominio sobre un campo calculado (Odoo 19
    normaliza «= True» a «in [True]»)."""
    if operator in ("=", "=="):
        return {bool(value)}
    if operator in ("!=", "<>"):
        return {not value}
    vals = {bool(v) for v in (value if isinstance(value, (list, tuple, set)) else [value])}
    if operator == "in":
        return vals
    if operator == "not in":
        return {True, False} - vals
    return {True, False}


class ShrimpSellerStrike(models.Model):
    _name = "shrimp.seller.strike"
    _inherit = ["mail.thread"]
    _description = "Strike del vendedor (incumplimiento de entrega)"
    _order = "date desc, id desc"

    name = fields.Char(string="Referencia", compute="_compute_name")
    partner_id = fields.Many2one("res.partner", string="Socio (cuenta)", required=True,
                                 index=True, ondelete="cascade")
    transaction_id = fields.Many2one("shrimp.transaction", string="Compra", index=True,
                                     ondelete="set null")
    seller_role = fields.Char(string="Perfil del vendedor")
    reason = fields.Selection(STRIKE_REASONS, string="Causa", required=True, index=True)
    date = fields.Datetime(string="Fecha", default=fields.Datetime.now, required=True)
    expires_at = fields.Datetime(string="Vence el", required=True, index=True)
    annulled = fields.Boolean(string="Anulado", readonly=True, tracking=True, index=True)
    annulled_by_id = fields.Many2one("res.users", string="Anulado por", readonly=True)
    annulled_at = fields.Datetime(string="Anulado el", readonly=True)
    annul_justification = fields.Text(
        string="Justificación de la anulación", tracking=True,
        help="Obligatoria para anular: queda en el historial del strike.")
    is_active = fields.Boolean(string="Activo", compute="_compute_is_active",
                               search="_search_is_active")

    @api.depends("transaction_id.name", "reason")
    def _compute_name(self):
        for rec in self:
            # Etiquetas del campo (no de STRIKE_REASONS): otros módulos agregan
            # causas con selection_add (p. ej. «Producto no presentado»).
            rec.name = "%s · %s" % (dict(rec._fields["reason"].selection).get(rec.reason, ""),
                                    rec.transaction_id.name or "—")

    @api.depends("annulled", "expires_at")
    def _compute_is_active(self):
        ahora = fields.Datetime.now()
        for rec in self:
            rec.is_active = bool(not rec.annulled and rec.expires_at and rec.expires_at > ahora)

    def _search_is_active(self, operator, value):
        buscados = _bool_values(operator, value)
        ahora = fields.Datetime.now()
        if buscados == {True}:
            return [("annulled", "=", False), ("expires_at", ">", ahora)]
        if buscados == {False}:
            return ["|", ("annulled", "=", True), ("expires_at", "<=", ahora)]
        return [] if buscados else [("id", "=", 0)]

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_expiry_days(self):
        return self.env["shrimp.settings"].get_int(
            STRIKE_EXPIRY_PARAM, STRIKE_EXPIRY_DEFAULT, minimum=1, maximum=3650)

    @api.model
    def _shrimp_thresholds(self):
        S = self.env["shrimp.settings"]
        aviso = S.get_int(STRIKE_WARNING_PARAM, STRIKE_WARNING_DEFAULT, minimum=1, maximum=100)
        suspension = S.get_int(STRIKE_SUSPENSION_PARAM, STRIKE_SUSPENSION_DEFAULT, minimum=1,
                               maximum=100)
        return aviso, max(suspension, aviso)

    @api.model
    def _shrimp_account(self, partner):
        partner = partner.sudo()
        if hasattr(partner, "_shrimp_role_holder"):
            return partner._shrimp_role_holder()
        return partner.commercial_partner_id

    @api.model
    def _shrimp_active_count(self, partner):
        if not partner or not shrimp_delay_ready(self.env):
            return 0
        return self.sudo().search_count([("partner_id", "=", self._shrimp_account(partner).id),
                                         ("is_active", "=", True)])

    # ------------------------------------------------------------------
    # Alta (solo código)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_add_strike(self, tx, reason, only_new=False):
        """Strike al vendedor de `tx` por `reason`, una vez por compra y causa
        (aunque se haya anulado). Avisa al vendedor y aplica los umbrales.
        Devuelve el strike nuevo, o el que ya había (vacío con `only_new`)."""
        tx = tx.sudo()
        cuenta = self._shrimp_account(tx.seller_partner_id)
        Strike = self.sudo().with_context(mail_create_nosubscribe=True)
        # D-42: como máximo UN strike por compra, sean cuales sean las causas
        # (antes, uno por compra y causa: una misma venta podía sumar tres:
        # atraso sin aviso + sobre el tope + cancelación del comprador). La
        # causa nueva queda anotada en el strike que ya existe (anulado o no).
        existente = Strike.search([("transaction_id", "=", tx.id)], order="id asc", limit=1) \
            if tx.id else Strike.browse()
        if existente:
            if existente.reason != reason and not self.env.context.get("shrimp_strike_quiet"):
                try:
                    existente.message_post(body=_(
                        "La compra %(r)s volvió a incumplir (%(c)s), pero una compra suma como "
                        "máximo un strike: no se agrega otro.") % {
                        "r": tx.name, "c": dict(STRIKE_REASONS).get(reason, reason)})
                except Exception:  # noqa: BLE001
                    _logger.debug("Sin constancia de la causa extra del strike %s", existente.id)
            return Strike.browse() if only_new else existente
        ahora = fields.Datetime.now()
        strike = Strike.create({
            "partner_id": cuenta.id,
            "transaction_id": tx.id,
            "seller_role": tx.seller_role or False,
            "reason": reason,
            "date": ahora,
            "expires_at": ahora + timedelta(days=self._shrimp_expiry_days()),
        })
        strike._shrimp_after_strike()
        return strike

    def _shrimp_seller_users(self):
        self.ensure_one()
        Tx = self.env["shrimp.transaction"]
        users = Tx._shrimp_party_users(self.partner_id, self.seller_role, "sell")
        return users.mapped("partner_id").filtered("email") or self.partner_id.filtered("email")

    def _shrimp_how_to_avoid(self):
        self.ensure_one()
        Tx = self.env["shrimp.transaction"]
        if self.reason == "unnotified_delay":
            return _("Si no vas a llegar a la fecha acordada, usa «Notificar retraso de entrega» en la "
                     "venta antes de que pasen %s días de esa fecha, y marca el despacho en cuanto "
                     "salga.") % Tx._shrimp_delay_grace_days()
        if self.reason == "buyer_cancel_delay":
            return _("Avisa los retrasos con tiempo y propone fechas que puedas cumplir; si el "
                     "comprador propone otra fecha, acéptala cuando puedas entregar ese día.")
        if self.reason == "seller_cancel":
            return _("Publica solo el stock que puedas entregar y mantén actualizado el estado de tus "
                     "lotes; si algo cambia, avisa un retraso en vez de cancelar.")
        if self.reason == "platform_cancel":
            return _("Entrega lo vendido en la fecha acordada y marca el despacho en la plataforma; "
                     "si hay un problema, avísalo al comprador desde la compra.")
        return _("El retraso total de una entrega no puede pasar de %s días sobre la fecha original: "
                 "publica fechas de entrega que puedas cumplir.") % Tx._shrimp_delay_max_days()

    def _shrimp_after_strike(self):
        """Aviso al vendedor y umbrales (aviso y suspensión)."""
        self.ensure_one()
        aviso, suspension = self._shrimp_thresholds()
        activos = self._shrimp_active_count(self.partner_id)
        dias = self._shrimp_expiry_days()
        causa = dict(self._fields["reason"]._description_selection(self.env)).get(self.reason, "")
        partes = [
            _("Recibiste un strike por la venta %(r)s: %(c)s.") % {
                "r": self.transaction_id.name or "—", "c": causa},
            _("Strikes activos: %(n)s. Cada strike vence a los %(d)s días.") % {"n": activos, "d": dias},
            _("Cómo evitarlo: %s") % self._shrimp_how_to_avoid(),
            _("Con %(a)s strikes activos tus productos bajan en el orden del marketplace y no salen en "
              "destacados; con %(s)s se suspende la publicación, que se levanta sola cuando tus strikes "
              "activos bajan de %(s)s (cada uno vence a los %(d)s días). Si crees que es un error, "
              "solicita su anulación.") % {"a": aviso, "s": suspension, "d": dias},
        ]
        Suspension = self.env["shrimp.seller.suspension"].sudo()
        if activos >= suspension and not Suspension._shrimp_active_for(self.partner_id):
            Suspension._shrimp_open(self.partner_id, activos, self)
            partes.insert(1, _("Llegaste a %(n)s strikes activos: la publicación de tus productos "
                               "queda SUSPENDIDA. Se levanta sola cuando tus strikes activos bajen "
                               "de %(s)s (al vencer o anularse) o antes, si la plataforma la revisa "
                               "y la levanta. Tus ventas en curso siguen normalmente.") % {
                "n": activos, "s": suspension})
        elif aviso <= activos < suspension:
            partes.insert(1, _("ADVERTENCIA: tienes %(n)s strikes activos. Tus productos bajan en el "
                               "orden del marketplace y no salen en destacados. Con %(s)s se suspende "
                               "la publicación.") % {"n": activos, "s": suspension})
        self._shrimp_post(_("Strike por la venta %s") % (self.transaction_id.name or ""), partes)

    def _shrimp_post(self, subject, partes):
        self.ensure_one()
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("").join(Markup("<p>%s</p>") % p for p in partes),
                subject=subject, partner_ids=self._shrimp_seller_users().ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 — un aviso nunca tumba el proceso
            _logger.warning("No se pudo avisar el strike %s", self.id, exc_info=True)

    # ------------------------------------------------------------------
    # Plataforma
    # ------------------------------------------------------------------
    def _shrimp_check_admin(self):
        if not (self.env.su or self.env.user.has_group(
                "shrimp_marketplace.group_marketplace_manager")):
            raise AccessError(_("Solo un administrador de CamaronMarket anula strikes o levanta "
                                "suspensiones."))

    def action_open_form(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current"}

    def action_annul(self, justification=None):
        """Anula el strike con una justificación (queda en su historial)."""
        self._shrimp_check_admin()
        for rec in self:
            texto = (justification or rec.annul_justification or "").strip()
            if not texto:
                raise UserError(_("Escribe la justificación de la anulación."))
            if rec.annulled:
                continue
            rec.sudo().write({"annulled": True, "annulled_by_id": self.env.user.id,
                              "annulled_at": fields.Datetime.now(),
                              "annul_justification": texto})
            rec._shrimp_post(_("Strike anulado (%s)") % (rec.transaction_id.name or ""), [
                _("La plataforma anuló el strike de la venta %(r)s: %(j)s") % {
                    "r": rec.transaction_id.name or "—", "j": texto},
                _("Strikes activos: %s.") % rec._shrimp_active_count(rec.partner_id)])
        # D-42: si con la anulación los strikes activos bajan del umbral, la
        # suspensión se levanta sola.
        Suspension = self.env["shrimp.seller.suspension"].sudo()
        for cuenta in self.mapped("partner_id"):
            Suspension._shrimp_auto_lift_for(cuenta)
        return True


class ShrimpSellerSuspension(models.Model):
    _name = "shrimp.seller.suspension"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Suspensión de publicación por strikes"
    _order = "date_start desc, id desc"

    partner_id = fields.Many2one("res.partner", string="Socio (cuenta)", required=True,
                                 index=True, ondelete="cascade")
    date_start = fields.Datetime(string="Suspendida el", default=fields.Datetime.now, required=True)
    strike_count = fields.Integer(string="Strikes activos al suspender")
    trigger_strike_id = fields.Many2one("shrimp.seller.strike", string="Strike que la causó",
                                        ondelete="set null")
    state = fields.Selection([("active", "Vigente"), ("lifted", "Levantada")], string="Estado",
                             default="active", required=True, index=True, tracking=True)
    lifted_by_id = fields.Many2one("res.users", string="Levantada por", readonly=True)
    lifted_at = fields.Datetime(string="Levantada el", readonly=True)
    lift_note = fields.Text(string="Nota de la revisión")

    @api.model
    def _shrimp_active_for(self, partner):
        if not partner or not shrimp_delay_ready(self.env):
            return self.browse()
        cuenta = self.env["shrimp.seller.strike"]._shrimp_account(partner)
        if cuenta.id not in self.env["res.partner"]._shrimp_suspended_ids():
            return self.browse()
        return self.sudo().search([("partner_id", "=", cuenta.id), ("state", "=", "active")], limit=1)

    def _shrimp_clear_cache(self):
        self.env.cr.cache.pop(SUSPENDED_CACHE_KEY, None)

    def write(self, vals):
        res = super().write(vals)
        if "state" in vals:
            self._shrimp_clear_cache()
        return res

    @api.model
    def _shrimp_open(self, partner, count, strike=None):
        sus = self.sudo().with_context(mail_create_nosubscribe=True).create({
            "partner_id": partner.id, "strike_count": count,
            "trigger_strike_id": strike.id if strike else False})
        sus._shrimp_clear_cache()
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False)
        admins = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal()) if grupo else []
        texto = _("%(p)s llegó a %(n)s strikes activos: su publicación quedó suspendida. Revisa la "
                  "cuenta y, cuando corresponda, usa «Levantar suspensión» en su ficha.") % {
            "p": partner.name, "n": count}
        try:
            for admin in admins[:5]:
                sus.activity_schedule("mail.mail_activity_data_todo", user_id=admin.id,
                                      summary=_("Revisar suspensión por strikes"), note=texto)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo crear la actividad de la suspensión %s", sus.id, exc_info=True)
        sus.message_post(body=texto)
        return sus

    def action_lift(self, note=None):
        self.env["shrimp.seller.strike"]._shrimp_check_admin()
        for rec in self.filtered(lambda s: s.state == "active"):
            rec.sudo().write({"state": "lifted", "lifted_by_id": self.env.user.id,
                              "lifted_at": fields.Datetime.now(),
                              "lift_note": (note or rec.lift_note or "").strip() or False})
            rec._shrimp_after_lift(_(
                "La plataforma revisó tu cuenta y levantó la suspensión: ya puedes volver a "
                "publicar y tus productos vuelven al catálogo."))
        return True

    def _shrimp_after_lift(self, texto):
        """Cierra la actividad de revisión y avisa al vendedor."""
        self.ensure_one()
        try:
            self.sudo().activity_unlink(["mail.mail_activity_data_todo"])
        except Exception:  # noqa: BLE001
            _logger.debug("Sin actividades que cerrar en la suspensión %s", self.id)
        strike = self.env["shrimp.seller.strike"].sudo().search(
            [("partner_id", "=", self.partner_id.id)], limit=1)
        if strike:
            strike._shrimp_post(_("Suspensión levantada"), [
                texto,
                _("Strikes activos: %s. Si vuelves a llegar al límite, la publicación se "
                  "suspende otra vez.") % strike._shrimp_active_count(self.partner_id)])

    # ------------------------------------------------------------------
    # D-42: levantamiento AUTOMÁTICO al bajar del umbral
    # ------------------------------------------------------------------
    def _shrimp_lift_auto(self, activos, umbral):
        """Los strikes activos bajaron del umbral de suspensión (vencieron a
        los N días o se anularon): la suspensión se levanta sola, con aviso al
        vendedor y constancia para la plataforma."""
        ahora = fields.Datetime.now()
        for rec in self.sudo().filtered(lambda s: s.state == "active"):
            nota = _("Levantada automáticamente el %(f)s: los strikes activos bajaron a %(n)s "
                     "(la suspensión se aplica con %(u)s).") % {
                "f": self.env["shrimp.transaction"].shrimp_fmt_dt(ahora), "n": activos, "u": umbral}
            rec.write({"state": "lifted", "lifted_by_id": False, "lifted_at": ahora,
                       "lift_note": nota})
            rec._shrimp_after_lift(_(
                "Tus strikes activos bajaron a %(n)s, por debajo del límite de %(u)s: la suspensión "
                "se levantó automáticamente. Ya puedes volver a publicar y tus productos vuelven "
                "al catálogo.") % {"n": activos, "u": umbral})
            grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager",
                                 raise_if_not_found=False)
            admins = grupo.sudo().all_user_ids.filtered(
                lambda u: u.active and not u._is_superuser() and u._is_internal()) if grupo else []
            try:
                rec.message_post(body=nota, partner_ids=admins.mapped("partner_id").ids if admins else [],
                                 subject=_("Suspensión levantada: %s") % rec.partner_id.name)
            except Exception:  # noqa: BLE001
                _logger.warning("Sin aviso del levantamiento de %s", rec.id, exc_info=True)
        return True

    @api.model
    def _shrimp_auto_lift_for(self, partner):
        if not partner or not shrimp_delay_ready(self.env):
            return self.browse()
        Strike = self.env["shrimp.seller.strike"]
        umbral = Strike._shrimp_thresholds()[1]
        cuenta = Strike._shrimp_account(partner)
        suspensiones = self.sudo().search([("partner_id", "=", cuenta.id), ("state", "=", "active")])
        if not suspensiones:
            return self.browse()
        activos = Strike._shrimp_active_count(cuenta)
        if activos >= umbral:
            return self.browse()
        suspensiones._shrimp_lift_auto(activos, umbral)
        return suspensiones

    @api.model
    def _shrimp_cron_auto_lift(self, limit=500):
        """Cron (con el de entregas y recepción, cada hora)."""
        if not shrimp_delay_ready(self.env):
            return self.browse()
        levantadas = self.browse()
        for cuenta in self.sudo().search([("state", "=", "active")], limit=limit).mapped("partner_id"):
            try:
                with self.env.cr.savepoint():
                    levantadas |= self._shrimp_auto_lift_for(cuenta)
            except Exception:  # noqa: BLE001
                _logger.exception("No se pudo revisar la suspensión de %s", cuenta.id)
        return levantadas


class ResPartnerStrikes(models.Model):
    """Indicadores de cumplimiento del vendedor. Todo calculado (sin columnas
    nuevas en res_partner)."""
    _inherit = "res.partner"

    shrimp_strike_ids = fields.One2many("shrimp.seller.strike", "partner_id", string="Strikes")
    shrimp_suspension_ids = fields.One2many("shrimp.seller.suspension", "partner_id",
                                            string="Suspensiones")
    shrimp_active_strike_count = fields.Integer(
        string="Strikes activos", compute="_compute_shrimp_strike_status")
    shrimp_strike_penalized = fields.Boolean(
        string="Penalizado en el catálogo", compute="_compute_shrimp_strike_status",
        help="Tiene los strikes activos del umbral de aviso: baja en el orden y no sale en "
             "destacados.")
    shrimp_publishing_suspended = fields.Boolean(
        string="Publicación suspendida", compute="_compute_shrimp_strike_status",
        search="_search_shrimp_publishing_suspended")
    shrimp_ontime_count = fields.Integer(
        string="Entregas a tiempo", compute="_compute_shrimp_ontime")
    shrimp_ontime_pct = fields.Float(
        string="Entregas a tiempo (%)", compute="_compute_shrimp_ontime", digits=(5, 1),
        help="Entregas cerradas sin retraso (sin fecha movida por un aviso de retraso y sin atraso "
             "sin aviso) sobre el total de entregas cerradas.")

    def _compute_shrimp_strike_status(self):
        listo = shrimp_delay_ready(self.env)
        Strike = self.env["shrimp.seller.strike"]
        Suspension = self.env["shrimp.seller.suspension"]
        aviso = Strike._shrimp_thresholds()[0] if listo else 0
        for rec in self:
            if not listo or not rec.id:
                rec.shrimp_active_strike_count = 0
                rec.shrimp_strike_penalized = False
                rec.shrimp_publishing_suspended = False
                continue
            n = Strike._shrimp_active_count(rec)
            rec.shrimp_active_strike_count = n
            rec.shrimp_strike_penalized = n >= aviso
            rec.shrimp_publishing_suspended = bool(Suspension._shrimp_active_for(rec))

    def _search_shrimp_publishing_suspended(self, operator, value):
        buscados = _bool_values(operator, value)
        if not buscados:
            return [("id", "=", 0)]
        if buscados == {True, False}:
            return []
        ids = self._shrimp_suspended_ids()
        return [("id", "in" if buscados == {True} else "not in", ids)]

    def _compute_shrimp_ontime(self):
        Tx = self.env["shrimp.transaction"].sudo()
        listo = shrimp_delay_ready(self.env)
        for rec in self:
            if not rec.id or not listo:
                rec.shrimp_ontime_count = 0
                rec.shrimp_ontime_pct = 0.0
                continue
            cerradas = Tx.search([("seller_partner_id", "=", rec.id),
                                  ("reception_required", "=", True),
                                  ("reception_status", "in", ("received", "auto"))])
            a_tiempo = cerradas.filtered(lambda t: t._shrimp_delivered_on_time())
            rec.shrimp_ontime_count = len(a_tiempo)
            rec.shrimp_ontime_pct = (100.0 * len(a_tiempo) / len(cerradas)) if cerradas else 0.0

    @api.model
    def _shrimp_penalized_ids(self, partner_ids):
        """De `partner_ids`, los vendedores con los strikes del umbral de aviso."""
        if not partner_ids or not shrimp_delay_ready(self.env):
            return set()
        Strike = self.env["shrimp.seller.strike"].sudo()
        aviso = Strike._shrimp_thresholds()[0]
        grupos = Strike._read_group([("partner_id", "in", list(partner_ids)), ("is_active", "=", True)],
                                    ["partner_id"], ["__count"])
        return {p.id for p, n in grupos if n >= aviso}

    @api.model
    def _shrimp_suspended_ids(self):
        """Cuentas con la publicación suspendida (caché de la transacción:
        el catálogo pregunta por cada lote)."""
        if not shrimp_delay_ready(self.env):
            return []
        cache = self.env.cr.cache
        if SUSPENDED_CACHE_KEY not in cache:
            cache[SUSPENDED_CACHE_KEY] = self.env["shrimp.seller.suspension"].sudo().search(
                [("state", "=", "active")]).mapped("partner_id").ids
        return cache[SUSPENDED_CACHE_KEY]

    def action_lift_publishing_suspension(self):
        """Back-office: «Levantar suspensión» (Administrador de CamaronMarket)."""
        self.env["shrimp.seller.strike"]._shrimp_check_admin()
        for rec in self:
            self.env["shrimp.seller.suspension"]._shrimp_active_for(rec).action_lift()
        return True


class ShrimpTransactionOnTime(models.Model):
    _inherit = "shrimp.transaction"

    @api.model
    def _cron_reception_and_delivery(self, limit=500):
        res = super()._cron_reception_and_delivery(limit=limit)
        try:
            self.env["shrimp.seller.suspension"]._shrimp_cron_auto_lift(limit)
        except Exception:  # noqa: BLE001 — nunca frena la recepción
            _logger.exception("Levantamiento automático de suspensiones: fallo del cron")
        return res

    def _shrimp_delivered_on_time(self):
        """Entrega cerrada a tiempo: sin fecha movida por un aviso aceptado
        (expresa o tácitamente), sin strike por atraso y, si el vendedor
        marcó el despacho, despachada hasta la fecha original."""
        self.ensure_one()
        tx = self.sudo()
        if tx.delay_ids.filtered(lambda d: d.kind == "notice" and d.state in ("accepted", "tacit")):
            return False
        if self.env["shrimp.seller.strike"].sudo().search_count([("transaction_id", "=", tx.id)]):
            return False
        original = tx.shrimp_delay_original_date()
        if tx.dispatched_at and original and tx.shrimp_local_date(tx.dispatched_at) > original:
            return False
        return True


class ShrimpProductStrikes(models.Model):
    """Suspensión de publicación y penalización en el catálogo."""
    _inherit = "shrimp.product"

    def _shrimp_publish_problems(self, check_stock=True):
        problemas = super()._shrimp_publish_problems(check_stock=check_stock)
        if shrimp_delay_ready(self.env) and self.seller_partner_id and \
                self.env["shrimp.seller.suspension"]._shrimp_active_for(self.seller_partner_id):
            problemas.append(_("la publicación de la cuenta está suspendida por incumplimientos de "
                               "entrega (se levanta sola cuando los strikes activos bajan del "
                               "límite, o antes si la plataforma la revisa)"))
        return problemas

    @api.model
    def _shrimp_published_domain(self):
        dominio = super()._shrimp_published_domain()
        suspendidos = self.env["res.partner"]._shrimp_suspended_ids()
        if suspendidos:
            dominio = dominio + [("seller_partner_id", "not in", suspendidos)]
        return dominio

    @api.model
    def _shrimp_featured_sellers(self, tipo="larva", limit=6):
        """Fuera de «destacados» los vendedores con los strikes del umbral de
        aviso (y los suspendidos, que ya no tienen lotes visibles)."""
        filas = super()._shrimp_featured_sellers(tipo=tipo, limit=limit + 20)
        castigados = self.env["res.partner"]._shrimp_penalized_ids([f["partner"].id for f in filas])
        return [f for f in filas if f["partner"].id not in castigados][:limit]

    def motivo_no_comprable(self, partner, role=None):
        motivo = super().motivo_no_comprable(partner, role=role)
        if motivo or not shrimp_delay_ready(self.env):
            return motivo
        if self.env["shrimp.seller.suspension"]._shrimp_active_for(self.seller_partner_id):
            return _("El vendedor tiene la publicación suspendida temporalmente.")
        return motivo

    @api.model
    def shrimp_rank_with_strikes(self, products, key=None):
        """Ordena `products` con `key` (o deja su orden) y manda al final a
        los vendedores penalizados por strikes; dentro de cada grupo se
        conserva el orden (sorted es estable)."""
        if not products:
            return products
        if key:
            products = products.sorted(key=key)
        castigados = self.env["res.partner"]._shrimp_penalized_ids(
            set(products.mapped("seller_partner_id").ids))
        if not castigados:
            return products
        return products.sorted(key=lambda p: p.seller_partner_id.id in castigados)
