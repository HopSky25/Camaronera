"""Reglas de la compra decididas en la auditoría de procesos (§10–11).

D-27  Comisión: se factura al confirmar la compra (como siempre) y se ANULA en
      toda cancelación (sin factura: anulada; facturada: nota de crédito).
      Los caminos conocidos ya la anulan (shrimp_transaction_cancel.py y
      shrimp_transaction_delay.py); aquí queda la red de seguridad: cualquier
      paso de una compra a «cancelada» (verificación rechazada o vencida,
      trato caído, código futuro) anula la comisión que siga viva.

D-43  Factura del vendedor (paso 2 de la facturación):
      * obligatoria dentro de N días (Ajustes, 7) desde que la compra se
        completa (received_date);
      * recordatorios al vendedor (administrador y usuarios con permiso de
        vender o de facturación en ese perfil) los días configurados (3, 6 y
        7) y un aviso al vencer;
      * marca «factura vencida» para la plataforma (filtro en el back-office);
      * pasados M días (Ajustes, 30) la factura registrada ya no se puede
        modificar (bloqueada). Si nunca se registró, todavía se puede
        registrar (queda como tardía).
      Solo aplica a las compras completadas DESDE la fecha del -u
      (shrimp_marketplace.seller_invoice_rules_since): las 800+ ventas viejas
      no reciben una tanda de recordatorios ni aparecen todas vencidas. Si el
      parámetro no existe (código nuevo cargado antes del -u) no aplica nada.

Sin columnas nuevas: todo calculado; las marcas de los recordatorios van en
reception_notify_marks («inv_rem:N», «inv_due»).
"""
import logging
from datetime import datetime, time, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

INVOICE_SINCE_PARAM = "shrimp_marketplace.seller_invoice_rules_since"
INVOICE_DAYS_PARAM = "shrimp_marketplace.seller_invoice_days"
INVOICE_DAYS_DEFAULT = 7
INVOICE_LOCK_PARAM = "shrimp_marketplace.seller_invoice_lock_days"
INVOICE_LOCK_DEFAULT = 30
INVOICE_REMINDER_PARAMS = (
    ("shrimp_marketplace.seller_invoice_reminder_1", 3),
    ("shrimp_marketplace.seller_invoice_reminder_2", 6),
    ("shrimp_marketplace.seller_invoice_reminder_3", 7),
)

SELLER_INVOICE_STATUS = [
    ("na", "No aplica"),
    ("pending", "Pendiente (en plazo)"),
    ("overdue", "Vencida"),
    ("registered", "Registrada"),
    ("locked", "Registrada y bloqueada"),
]

_INVOICE_FIELDS = frozenset({
    "seller_invoice_number", "seller_invoice_access_key", "seller_invoice_date",
    "seller_invoice_attachment_id", "seller_invoice_registered_at",
})


class ShrimpTransactionPurchaseRules(models.Model):
    _inherit = "shrimp.transaction"

    seller_invoice_due_date = fields.Date(
        string="Factura del vendedor: registrar hasta", compute="_compute_seller_invoice_rules",
        help="La factura de la mercadería se registra dentro de los días de Ajustes desde que la "
             "compra se completó.")
    seller_invoice_lock_date = fields.Date(
        string="Factura del vendedor: editable hasta", compute="_compute_seller_invoice_rules")
    seller_invoice_status = fields.Selection(
        SELLER_INVOICE_STATUS, string="Factura del vendedor (plazo)",
        compute="_compute_seller_invoice_rules")
    seller_invoice_overdue = fields.Boolean(
        string="Factura del vendedor vencida", compute="_compute_seller_invoice_rules",
        search="_search_seller_invoice_overdue",
        help="Compra completada sin la factura del vendedor registrada y con el plazo vencido.")

    # ------------------------------------------------------------------
    # D-27: red de seguridad de la comisión
    # ------------------------------------------------------------------
    def write(self, vals):
        cancelan = self.browse()
        if vals.get("state") == "cancel":
            cancelan = self.filtered(lambda t: t.state != "cancel")
        # D-43: una factura bloqueada no se cambia ni desde el back-office
        # (los flujos del servidor trabajan en sudo y la comprueban antes).
        if _INVOICE_FIELDS.intersection(vals) and not self.env.su \
                and not self.env.context.get("shrimp_invoice_unlock"):
            bloqueadas = self.filtered(lambda t: t.seller_invoice_status == "locked")
            if bloqueadas:
                raise ValidationError(self._shrimp_invoice_locked_message(bloqueadas[0]))
        res = super().write(vals)
        if cancelan:
            cancelan._shrimp_void_commission_on_cancel()
        return res

    def _shrimp_void_commission_on_cancel(self):
        """Anula la comisión que siga viva en una compra cancelada (D-27)."""
        Charge = self.env["shrimp.charge"].sudo()
        anuladas = Charge
        for tx in self:
            vivas = Charge.search([
                ("transaction_id", "=", tx.id), ("charge_type", "=", "commission"),
                ("state", "not in", ("cancelled", "credited", "to_credit"))])
            if not vivas:
                continue
            vivas.action_cancel_charge(_("Compra %s cancelada: la comisión se anula.") % tx.name)
            anuladas |= vivas
            try:
                tx.sudo().message_post(body=_(
                    "Compra cancelada: se anuló la comisión de la plataforma (%s); si ya estaba "
                    "facturada, se emite la nota de crédito.") % ", ".join(vivas.mapped("name")))
            except Exception:  # noqa: BLE001
                _logger.debug("Sin constancia de la anulación de la comisión de %s", tx.name)
        return anuladas

    # ------------------------------------------------------------------
    # D-43: plazos de la factura del vendedor
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_invoice_days(self):
        return self.env["shrimp.settings"].get_int(
            INVOICE_DAYS_PARAM, INVOICE_DAYS_DEFAULT, minimum=1, maximum=365)

    @api.model
    def _shrimp_invoice_lock_days(self):
        return max(self.env["shrimp.settings"].get_int(
            INVOICE_LOCK_PARAM, INVOICE_LOCK_DEFAULT, minimum=1, maximum=3650),
            self._shrimp_invoice_days())

    @api.model
    def _shrimp_invoice_reminder_days(self):
        S = self.env["shrimp.settings"]
        dias = {S.get_int(k, d, minimum=0, maximum=365) for k, d in INVOICE_REMINDER_PARAMS}
        return sorted(d for d in dias if d > 0)

    @api.model
    def _shrimp_invoice_rules_since(self):
        """Datetime (UTC) desde el que aplica la obligación, o None."""
        valor = self.env["ir.config_parameter"].sudo().get_param(INVOICE_SINCE_PARAM)
        if not valor:
            return None
        try:
            return fields.Datetime.to_datetime(valor)
        except (TypeError, ValueError):
            try:
                return datetime.combine(fields.Date.to_date(valor), time.min)
            except (TypeError, ValueError):
                return None

    @api.model
    def _shrimp_init_purchase_rules(self):
        """Datos (se llama en cada instalación / -u): fija UNA vez desde
        cuándo aplica la obligación de la factura del vendedor."""
        ICP = self.env["ir.config_parameter"].sudo()
        if not ICP.get_param(INVOICE_SINCE_PARAM):
            ICP.set_param(INVOICE_SINCE_PARAM, fields.Datetime.to_string(fields.Datetime.now()))
        return True

    def _shrimp_invoice_subject(self):
        """¿La compra está sujeta a la obligación (completada desde el -u)?"""
        self.ensure_one()
        desde = self._shrimp_invoice_rules_since()
        return bool(desde and self.state == "done" and self.received_date
                    and self.received_date >= desde)

    @api.depends("state", "received_date", "seller_invoice_number", "seller_invoice_access_key",
                 "seller_invoice_attachment_id")
    @api.depends_context("uid")
    def _compute_seller_invoice_rules(self):
        dias = self._shrimp_invoice_days()
        bloqueo = self._shrimp_invoice_lock_days()
        hoy = self._shrimp_today()
        desde = self._shrimp_invoice_rules_since()
        for rec in self:
            sujeta = bool(desde and rec.state == "done" and rec.received_date
                          and rec.received_date >= desde)
            if not sujeta:
                rec.seller_invoice_due_date = False
                rec.seller_invoice_lock_date = False
                rec.seller_invoice_overdue = False
                rec.seller_invoice_status = "registered" if rec.has_seller_invoice else "na"
                continue
            base = rec.with_context(shrimp_public_tz=True).shrimp_local_date(rec.received_date)
            rec.seller_invoice_due_date = base + timedelta(days=dias)
            rec.seller_invoice_lock_date = base + timedelta(days=bloqueo)
            if rec.has_seller_invoice:
                rec.seller_invoice_overdue = False
                rec.seller_invoice_status = "locked" if hoy > rec.seller_invoice_lock_date \
                    else "registered"
            else:
                rec.seller_invoice_overdue = hoy > rec.seller_invoice_due_date
                rec.seller_invoice_status = "overdue" if rec.seller_invoice_overdue else "pending"

    @api.model
    def _shrimp_local_midnight_utc(self, day):
        """Las 00:00 de `day` en la zona de la plataforma, en UTC ingenuo."""
        import pytz
        tz = self.with_context(shrimp_public_tz=True)._shrimp_tz()
        local = tz.localize(datetime.combine(day, time.min))
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _shrimp_invoice_overdue_domain(self):
        desde = self._shrimp_invoice_rules_since()
        if not desde:
            return [("id", "=", 0)]
        # Vencida: fecha local de la recepción + N < hoy  ⇔  recibida antes
        # de las 00:00 locales de (hoy − N).
        corte = self._shrimp_local_midnight_utc(
            self._shrimp_today() - timedelta(days=self._shrimp_invoice_days()))
        return [("state", "=", "done"), ("received_date", ">=", desde),
                ("received_date", "<", corte), ("seller_invoice_number", "=", False),
                ("seller_invoice_access_key", "=", False),
                ("seller_invoice_attachment_id", "=", False)]

    def _search_seller_invoice_overdue(self, operator, value):
        from .shrimp_seller_strike import _bool_values
        buscados = _bool_values(operator, value)
        if buscados == {True, False}:
            return []
        if not buscados:
            return [("id", "=", 0)]
        ids = self.sudo().search(self._shrimp_invoice_overdue_domain()).ids
        return [("id", "in" if buscados == {True} else "not in", ids)]

    def shrimp_seller_invoice_editable(self):
        """¿El vendedor puede registrar o cambiar la factura ahora?"""
        self.ensure_one()
        return self.state in ("confirmed", "done") and self.seller_invoice_status != "locked"

    @api.model
    def _shrimp_invoice_locked_message(self, tx):
        return _("La factura de la venta %(r)s ya no se puede modificar: pasaron más de %(n)s días "
                 "desde que la compra se completó (editable hasta el %(f)s). Si hay un error, "
                 "escribe a la plataforma.") % {
            "r": tx.name, "n": tx._shrimp_invoice_lock_days(),
            "f": tx.seller_invoice_lock_date.strftime("%d/%m/%Y") if tx.seller_invoice_lock_date
            else "—"}

    def action_register_seller_invoice(self, vals, actor=None):
        self.ensure_one()
        user = self.env.user
        if user.share:
            socio = actor or user.shrimp_actor_partner_id
            # Los permisos son del usuario de ESTA petición (self.env.user).
            vendedor = self.seller_partner_id
            if socio == vendedor and hasattr(vendedor, "_shrimp_user_allows") \
                    and self.seller_role \
                    and not (vendedor._shrimp_user_allows("sell_products", self.seller_role)
                             or vendedor._shrimp_user_allows("view_billing", self.seller_role)):
                raise AccessError(_(
                    "Tu usuario no tiene permiso para vender ni de facturación con el perfil de esta "
                    "venta. Pídeselo al administrador de tu cuenta."))
        if self.seller_invoice_status == "locked":
            raise ValidationError(self._shrimp_invoice_locked_message(self))
        tarde = self.seller_invoice_status == "overdue"
        res = super().action_register_seller_invoice(vals, actor=actor)
        if tarde:
            self.sudo().message_post(body=_(
                "Factura registrada fuera de plazo (vencía el %s).")
                % self.seller_invoice_due_date.strftime("%d/%m/%Y"))
        return res

    # ------------------------------------------------------------------
    # Recordatorios (cron de entregas y recepción, cada hora)
    # ------------------------------------------------------------------
    def _shrimp_invoice_recipients(self):
        """Administrador de la cuenta vendedora y usuarios con permiso de
        vender o de facturación en el perfil de la venta."""
        self.ensure_one()
        partner, role = self.seller_partner_id, self.seller_role
        users = self._shrimp_party_users(partner, role, "sell") \
            | self._shrimp_party_users(partner, role, "billing")
        return users.mapped("partner_id").filtered("email") or partner.filtered("email")

    def _shrimp_invoice_notify(self, subject, text):
        self.ensure_one()
        from markupsafe import Markup
        url = "%s/marketplace/sales/%s/invoice" % (self.get_base_url(), self.uuid_ref)
        body = Markup("<p>%s</p><p><a href=\"%s\">%s</a></p>") % (
            text, url, _("Registrar la factura"))
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=body, subject=subject, partner_ids=self._shrimp_invoice_recipients().ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 — un aviso nunca tumba el cron
            _logger.warning("No se pudo recordar la factura de %s", self.name, exc_info=True)

    @api.model
    def _shrimp_cron_seller_invoices(self, limit=500):
        desde = self._shrimp_invoice_rules_since()
        recordatorios = self._shrimp_invoice_reminder_days()
        if not desde:
            return self.browse()
        hoy = self._shrimp_today()
        dias = self._shrimp_invoice_days()
        tope = max(recordatorios + [dias + 1])
        pendientes = self.sudo().search([
            ("state", "=", "done"), ("received_date", ">=", desde),
            ("received_date", ">=", self._shrimp_local_midnight_utc(hoy - timedelta(days=tope + 1))),
            ("seller_invoice_number", "=", False), ("seller_invoice_access_key", "=", False),
            ("seller_invoice_attachment_id", "=", False),
        ], limit=limit, order="received_date asc, id asc")
        avisadas = self.browse()
        for tx in pendientes:
            base = tx.with_context(shrimp_public_tz=True).shrimp_local_date(tx.received_date)
            transcurridos = (hoy - base).days
            vence = base + timedelta(days=dias)
            marcas = tx._shrimp_marks()
            nuevas = ["inv_rem:%s" % r for r in recordatorios
                      if transcurridos >= r and "inv_rem:%s" % r not in marcas]
            vencida = hoy > vence and "inv_due" not in marcas
            if not nuevas and not vencida:
                continue
            f = vence.strftime("%d/%m/%Y")
            if vencida:
                tx._shrimp_invoice_notify(_("Factura vencida: venta %s") % tx.name, _(
                    "Venció el plazo para registrar la factura de la venta %(r)s (era el %(f)s). "
                    "Regístrala cuanto antes: la plataforma ve las ventas con la factura vencida.")
                    % {"r": tx.name, "f": f})
                nuevas = nuevas + ["inv_due"] + ["inv_rem:%s" % r for r in recordatorios]
            else:
                quedan = (vence - hoy).days
                if quedan <= 0:
                    texto = _("Hoy es el último día para registrar la factura de la venta %s.") % tx.name
                elif quedan == 1:
                    texto = _("Te queda 1 día (hasta el %(f)s) para registrar la factura de la venta "
                              "%(r)s.") % {"f": f, "r": tx.name}
                else:
                    texto = _("Te quedan %(d)s días (hasta el %(f)s) para registrar la factura de la "
                              "venta %(r)s.") % {"d": quedan, "f": f, "r": tx.name}
                texto += " " + _("La factura de la mercadería la emites desde tu sistema; en la "
                                 "plataforma registras el número, la clave de acceso o el archivo.")
                tx._shrimp_invoice_notify(_("Registra la factura de la venta %s") % tx.name, texto)
            tx._shrimp_add_marks(set(nuevas))
            avisadas |= tx
        return avisadas

    @api.model
    def _cron_reception_and_delivery(self, limit=500):
        res = super()._cron_reception_and_delivery(limit=limit)
        try:
            self._shrimp_cron_seller_invoices(limit)
        except Exception:  # noqa: BLE001 — nunca frena la recepción
            _logger.exception("Recordatorios de la factura del vendedor: fallo del cron")
        return res
