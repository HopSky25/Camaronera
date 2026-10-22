"""Recepción de la compra y avisos de la fecha de entrega.

QUÉ RESUELVE
------------
Antes, una compra directa (nauplio, larva) quedaba «Confirmada» hasta que el
comprador pulsara «Confirmar recepción»; si no lo hacía, la compra quedaba
abierta para siempre, el comprador no tenía el lote en su inventario y nadie
sabía si el vendedor había cumplido. Tampoco había ningún aviso de la fecha de
entrega.

Ahora, en TODA compra directa (D-26; antes solo en las parejas marcadas en la
columna «Confirmación de recepción» de la matriz, que hoy es informativa):

  confirmada ──(vendedor: «Marcar como despachado», opcional)──▶ despachada
      │                                                             │
      └──────── fecha de entrega ──────────┬────────────────────────┘
                                           ▼
                         el comprador tiene N días (Ajustes) para
                  «Confirmar recepción» (nota y foto opcionales) ──▶ Completada
                  «Reportar un problema» ──▶ Con incidencia (avisa a la plataforma)
                  no hace nada ──▶ el cron la confirma sola ──▶ Completada
                                   («Confirmada automáticamente»)
                                   SOLO si el vendedor marcó el despacho;
                                   sin despacho ──▶ «No entregada»: aviso a la
                                   plataforma, el comprador confirma (si la
                                   recibió) o cancela sin costo; si no hace
                                   nada en N días (Ajustes) se cancela sola
                                   sin costo y con strike al vendedor.

El plazo empieza en el despacho marcado por el vendedor o, si no lo marcó, en
la fecha de entrega comprometida. Un único punto de entrada para confirmar:
``shrimp_confirm_reception`` (portal, API y cron).

La comisión NO se mueve: es el cobro del servicio de la plataforma y se
registra al cerrar la compra (action_confirm), que es el «paso 1» de la
facturación en dos pasos. La mercadería la factura el vendedor (paso 2) y la
puede registrar desde la confirmación; al recibirse la compra se le recuerda.

Los avisos se guardan en ``reception_notify_marks`` (una marca por aviso y
por fecha), así el cron horario no repite ninguno y, si la fecha de entrega
cambia, los avisos de la fecha nueva salen otra vez.
"""
import base64
import logging
from datetime import datetime, timedelta

from markupsafe import Markup

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .shrimp_transaction import ShrimpTransaction as _BaseTransaction
from odoo.addons.shrimp_user_registry.authz import (
    shrimp_actor, shrimp_is_trusted, shrimp_strip_context)

# Banderas internas de la recepción: un RPC del portal no las puede mandar
# (saltarse la fecha, darla por verificada o automática, callar los avisos).
RECEPTION_CONTEXT_FLAGS = ("shrimp_reception_",)

_logger = logging.getLogger(__name__)

# Ajustes › CamaronMarket › Entregas y recepción (ver res_config_settings.py).
RECEPTION_DAYS_PARAM = "shrimp_marketplace.reception_days"
RECEPTION_DAYS_DEFAULT = 10
REMINDER_1_PARAM = "shrimp_marketplace.reception_reminder_1"
REMINDER_1_DEFAULT = 3
REMINDER_2_PARAM = "shrimp_marketplace.reception_reminder_2"
REMINDER_2_DEFAULT = 1
DELIVERY_ALERT_PARAM = "shrimp_marketplace.delivery_alert_days"
DELIVERY_ALERT_DEFAULT = 2

# Parejas (vendedor, comprador) que piden confirmación de recepción cuando no
# está instalada la matriz de verificación (shrimp_verification la sustituye
# por su columna «Confirmación de recepción»).
DEFAULT_RECEPTION_PAIRS = frozenset({
    ("semillero", "laboratorio"),
    ("laboratorio", "camaronera"),
    ("semillero", "semillero"),
    ("laboratorio", "laboratorio"),
})

RECEPTION_STATUS = [
    ("waiting", "Esperando recepción"),
    ("incidence", "Con incidencia"),
    ("received", "Recepción confirmada"),
    ("auto", "Confirmada automáticamente"),
    # Venció el plazo de recepción y el vendedor nunca marcó el despacho: NO
    # se confirma sola (no se da por cumplida una entrega sin despacho). El
    # comprador puede confirmar si en realidad la recibió o cancelar sin
    # costo; la plataforma recibe el aviso (shrimp_transaction_cancel.py).
    ("not_delivered", "No entregada (sin despacho)"),
    # Compra verificada: la recepción la acredita el informe aceptado por las
    # dos partes (shrimp_verification).
    ("verified", "Recibida con la verificación"),
]

# Ajustes › CamaronMarket › Entregas y recepción: días que el comprador tiene,
# con la compra «No entregada», antes de que se cancele sola sin costo
# (0 = nunca; la resuelve el comprador o la plataforma).
NOT_DELIVERED_CANCEL_PARAM = "shrimp_marketplace.not_delivered_cancel_days"
NOT_DELIVERED_CANCEL_DEFAULT = 7

# Fotos que se aceptan como evidencia de la recepción o de la incidencia.
PHOTO_MIMETYPES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}


class ShrimpTransactionReception(models.Model):
    _inherit = "shrimp.transaction"

    reception_required = fields.Boolean(
        string="Requiere confirmación de recepción", readonly=True, copy=False, index=True,
        help="Se fija al confirmar la compra según la matriz (pareja de perfiles). "
             "Si el comprador no confirma en el plazo, la compra se confirma sola.")
    reception_status = fields.Selection(
        RECEPTION_STATUS, string="Recepción", readonly=True, copy=False, index=True,
        tracking=True)
    dispatched_at = fields.Datetime(string="Despachado el", readonly=True, copy=False,
                                    tracking=True)
    dispatched_by_id = fields.Many2one("res.users", string="Despacho marcado por",
                                       readonly=True, copy=False)
    dispatch_note = fields.Text(string="Nota del despacho", readonly=True, copy=False)
    # Técnico: nadie empieza a contar el plazo antes de esta fecha. La pone
    # la migración (compras que ya estaban entregadas al instalar esto) y la
    # reapertura de una incidencia.
    reception_floor_date = fields.Date(string="Plazo de recepción desde (mínimo)",
                                       readonly=True, copy=False)
    reception_start_date = fields.Date(
        string="Inicio del plazo de recepción", compute="_compute_reception_start_date",
        store=True, index=True,
        help="El despacho marcado por el vendedor o, si no lo marcó, la fecha de entrega.")
    reception_deadline = fields.Date(
        string="Confirmar recepción hasta", compute="_compute_reception_deadline",
        help="Último día para confirmar la recepción; al día siguiente se confirma sola.")
    reception_days_left = fields.Integer(
        string="Días para confirmar", compute="_compute_reception_deadline")
    reception_auto_confirmed = fields.Boolean(
        string="Confirmada automáticamente", readonly=True, copy=False)
    received_by_id = fields.Many2one("res.users", string="Recepción confirmada por",
                                     readonly=True, copy=False)
    reception_note = fields.Text(string="Nota de la recepción", readonly=True, copy=False)
    reception_photo_id = fields.Many2one("ir.attachment", string="Foto de la recepción",
                                         readonly=True, copy=False, ondelete="set null")
    incidence_date = fields.Datetime(string="Incidencia reportada el", readonly=True, copy=False)
    incidence_note = fields.Text(string="Problema reportado", readonly=True, copy=False)
    incidence_photo_id = fields.Many2one("ir.attachment", string="Foto de la incidencia",
                                         readonly=True, copy=False, ondelete="set null")
    incidence_reported_by_id = fields.Many2one("res.users", string="Incidencia reportada por",
                                               readonly=True, copy=False)
    incidence_resolution = fields.Text(string="Resolución de la incidencia", copy=False,
                                       tracking=True)
    reception_had_incidence = fields.Boolean(string="Tuvo incidencia", readonly=True, copy=False)
    reception_notify_marks = fields.Char(
        string="Avisos enviados (técnico)", readonly=True, copy=False,
        help="Marcas de los avisos ya enviados (recordatorios y fecha de entrega).")

    # Defensa en profundidad (ver shrimp_transaction.write): el portal no
    # puede escribir nada de la recepción; todo pasa por las acciones.
    _SHRIMP_PROTECTED_FIELDS = _BaseTransaction._SHRIMP_PROTECTED_FIELDS | frozenset({
        "reception_required", "reception_status", "dispatched_at", "dispatched_by_id",
        "dispatch_note", "reception_floor_date", "reception_auto_confirmed",
        "received_by_id", "received_date", "reception_note", "reception_photo_id",
        "incidence_date", "incidence_note", "incidence_photo_id", "incidence_reported_by_id",
        "incidence_resolution", "reception_had_incidence", "reception_notify_marks",
    })

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_reception_days(self):
        return self.env["shrimp.settings"].get_int(
            RECEPTION_DAYS_PARAM, RECEPTION_DAYS_DEFAULT, minimum=1, maximum=365)

    @api.model
    def _shrimp_reception_reminders(self):
        """Días antes del vencimiento en que se recuerda al comprador (sin 0
        ni repetidos, de mayor a menor)."""
        S = self.env["shrimp.settings"]
        dias = {S.get_int(REMINDER_1_PARAM, REMINDER_1_DEFAULT, minimum=0, maximum=365),
                S.get_int(REMINDER_2_PARAM, REMINDER_2_DEFAULT, minimum=0, maximum=365)}
        return sorted((d for d in dias if d > 0), reverse=True)

    @api.model
    def _shrimp_delivery_alert_days(self):
        return self.env["shrimp.settings"].get_int(
            DELIVERY_ALERT_PARAM, DELIVERY_ALERT_DEFAULT, minimum=0, maximum=60)

    @api.model
    def _shrimp_today(self):
        """Hoy en la zona de la PLATAFORMA (la de la compañía principal o
        Ecuador continental), no la del usuario: el cron corre como root y en
        UTC «hoy» ya es mañana desde las 19:00 en Ecuador; así el plazo vence
        el mismo día para el comprador, el vendedor y el cron."""
        return datetime.now(self.with_context(shrimp_public_tz=True)._shrimp_tz()).date()

    # ------------------------------------------------------------------
    # Aplicabilidad
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_reception_policy(self, seller_role, buyer_role, product=None):
        """¿Esta pareja de perfiles pide confirmación de recepción? Sin la
        matriz (shrimp_verification la redefine), la tabla por defecto."""
        return (seller_role, buyer_role) in DEFAULT_RECEPTION_PAIRS

    def _shrimp_is_direct_delivery(self):
        """Compra que se entrega sin verificación en campo (la verificada tiene
        su propio seguimiento: shrimp.dispatch con cita y llegada a planta, y
        se cierra con la aceptación del informe). shrimp_verification la
        restringe."""
        self.ensure_one()
        return True

    def _shrimp_compute_reception_required(self):
        """D-26 (auditoría de procesos): TODA compra directa lleva la
        confirmación de recepción con su plazo, la marque o no la matriz.
        Antes una pareja sin la columna «Confirmación de recepción» (p. ej.
        camaronera → empacadora en compra directa) quedaba «Confirmada» para
        siempre si el comprador no la recibía. La confirmación automática
        sigue exigiendo el despacho (sin despacho: «No entregada»).
        La compra verificada no la lleva: se cierra con el informe aceptado.
        `_shrimp_reception_policy` (la columna de la matriz) queda solo como
        dato informativo."""
        self.ensure_one()
        return bool(self._shrimp_is_direct_delivery())

    def _shrimp_init_reception(self):
        """Al confirmar la compra: fija si lleva recepción y abre el estado."""
        hoy = self._shrimp_today()
        for rec in self:
            requerida = rec._shrimp_compute_reception_required()
            rec.sudo().write({
                "reception_required": requerida,
                "reception_status": "waiting" if requerida else False,
                # El plazo nunca empieza antes de la compra: un lote con la
                # fecha de entrega ya pasada no puede nacer con el plazo
                # vencido (el cron la confirmaría en la siguiente pasada).
                "reception_floor_date": hoy if requerida else False,
            })

    @api.model
    def _shrimp_backfill_reception(self, reevaluate=False):
        """Compras que ya existían al instalar esto (migración y demo).

        * Confirmadas: se decide si llevan recepción y, si la llevan, el
          plazo empieza HOY (reception_floor_date): ninguna compra vieja se
          confirma sola sin que el comprador tenga los N días completos.
        * Los avisos de fecha de entrega de fechas ya pasadas se dan por
          enviados: el primer cron no manda una tanda de «entrega atrasada»
          de compras viejas (siguen en «Entregas atrasadas» de Mi panel).
        * Completadas: quedan como «Recepción confirmada» (informativo).
        `reevaluate`: vuelve a decidir las confirmadas que aún esperan
        (shrimp_verification la llama con la matriz ya instalada).
        Devuelve cuántas compras confirmadas se tocaron.
        """
        hoy = self._shrimp_today()
        Tx = self.sudo().with_context(active_test=False, tracking_disable=True,
                                      mail_notrack=True)
        dominio = [("state", "=", "confirmed"), ("reception_status", "in", (False, "waiting")),
                   ("dispatched_at", "=", False)]
        if not reevaluate:
            dominio.append(("reception_status", "=", False))
        tocadas = 0
        for tx in Tx.search(dominio):
            requerida = tx._shrimp_compute_reception_required()
            vals = {"reception_required": requerida,
                    "reception_status": "waiting" if requerida else False}
            if requerida and not tx.reception_floor_date:
                vals["reception_floor_date"] = hoy
            if tx.delivery_date and tx.delivery_date < hoy:
                fecha = tx.delivery_date
                marcas = tx._shrimp_marks() | {"dlv_pre:%s" % fecha, "dlv_day:%s" % fecha,
                                               "dlv_late:%s" % fecha}
                vals["reception_notify_marks"] = " ".join(sorted(marcas))
            tx.write(vals)
            tocadas += 1
        Tx.search([("state", "=", "done"), ("reception_status", "=", False)]).write(
            {"reception_status": "received"})
        return tocadas

    def _shrimp_after_confirm(self):
        res = super()._shrimp_after_confirm()
        self._shrimp_init_reception()
        return res

    # ------------------------------------------------------------------
    # Plazos
    # ------------------------------------------------------------------
    @api.depends("reception_required", "dispatched_at", "delivery_date",
                 "reception_floor_date", "create_date")
    def _compute_reception_start_date(self):
        for rec in self:
            if not rec.reception_required:
                rec.reception_start_date = False
                continue
            base = (rec.shrimp_local_date(rec.dispatched_at) if rec.dispatched_at
                    else rec.delivery_date)
            if not base and rec.create_date:
                base = rec.create_date.date()
            if rec.reception_floor_date and (not base or base < rec.reception_floor_date):
                base = rec.reception_floor_date
            rec.reception_start_date = base or False

    @api.depends("reception_start_date")
    @api.depends_context("uid")
    def _compute_reception_deadline(self):
        dias = self._shrimp_reception_days()
        hoy = self._shrimp_today()
        for rec in self:
            if rec.reception_start_date:
                rec.reception_deadline = rec.reception_start_date + timedelta(days=dias)
                rec.reception_days_left = (rec.reception_deadline - hoy).days
            else:
                rec.reception_deadline = False
                rec.reception_days_left = 0

    def shrimp_reception_window_open(self):
        """¿Puede el comprador confirmar ya? Desde el despacho o desde la
        fecha de entrega (sin ninguna de las dos, desde ya)."""
        self.ensure_one()
        if self.state != "confirmed":
            return False
        if self.dispatched_at or not self.delivery_date:
            return True
        return self._shrimp_today() >= self.delivery_date

    def shrimp_reception_pending(self):
        """Compra que espera la confirmación del comprador (con o sin plazo)."""
        self.ensure_one()
        return self.state == "confirmed" and self._shrimp_is_direct_delivery() \
            and self.reception_status in (False, "waiting", "incidence", "not_delivered")

    def shrimp_delivery_overdue(self):
        self.ensure_one()
        return bool(self.state == "confirmed" and self.delivery_date and not self.dispatched_at
                    and self.reception_status not in ("incidence", "not_delivered")
                    and self.delivery_date < self._shrimp_today())

    # ------------------------------------------------------------------
    # Permisos (cuentas con subusuarios)
    # ------------------------------------------------------------------
    def _shrimp_check_party(self, side, actor=None):
        """`side`: "buyer" o "seller". Quien actúa tiene que ser esa parte y,
        si es subusuario, tener el permiso del perfil (comprar / vender)."""
        self.ensure_one()
        user = self.env.user
        if user._is_internal() or user._is_superuser():
            return True
        # El `actor` que pasa el controlador o la API solo vale si ya
        # autorizaron (sudo); por RPC, el socio del propio usuario.
        actor = shrimp_actor(self.env, actor)
        partner = self.buyer_partner_id if side == "buyer" else self.seller_partner_id
        if actor != partner:
            raise AccessError(_("Esta acción la hace el %s de la compra.")
                              % (_("comprador") if side == "buyer" else _("vendedor")))
        if side == "buyer":
            capacidad = "buy_from_%s" % (self.seller_role or "laboratorio")
            rol = self.buyer_role
        else:
            capacidad, rol = "sell_products", self.seller_role
        # env.user sigue siendo quien hace la petición aunque se trabaje en sudo.
        if hasattr(partner, "_shrimp_user_allows") and rol \
                and not partner._shrimp_user_allows(capacidad, rol):
            raise AccessError(_(
                "Tu usuario no tiene permiso para %s con el perfil de esta compra. "
                "Pídeselo al administrador de tu cuenta.")
                % (_("comprar") if side == "buyer" else _("vender")))
        return True

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_photo_attachment(self, rec, photo, kind):
        """`photo`: {"content": bytes, "mimetype": str, "name": str} ya validado."""
        if not photo or not photo.get("content"):
            return False
        mime = photo.get("mimetype")
        if mime not in PHOTO_MIMETYPES:
            raise ValidationError(_("La foto tiene que ser una imagen (JPG, PNG, WEBP) o un PDF."))
        return self.env["ir.attachment"].sudo().create({
            "name": photo.get("name") or "%s-%s" % (kind, rec.name),
            "datas": base64.b64encode(photo["content"]),
            "mimetype": mime, "res_model": "shrimp.transaction", "res_id": rec.id,
        }).id

    def action_mark_dispatched(self, note=None, actor=None):
        """El vendedor marca que despachó: el comprador ya puede confirmar y,
        si la compra lleva recepción, el plazo empieza a correr desde hoy."""
        for rec in self:
            rec._shrimp_check_party("seller", actor)
            if rec.state != "confirmed" or not rec._shrimp_is_direct_delivery():
                raise ValidationError(_("Solo se marca el despacho de una compra confirmada "
                                        "que espera su entrega."))
            if rec.dispatched_at:
                raise ValidationError(_("El despacho de esta compra ya está marcado."))
            if rec.reception_status == "not_delivered":
                raise ValidationError(_(
                    "Venció el plazo de recepción sin despacho y la compra quedó «No entregada»: "
                    "ya no se puede marcar el despacho. Si la mercadería llegó, el comprador "
                    "confirma la recepción; si no, la plataforma puede reabrir el plazo."))
            rec.sudo().write({
                "dispatched_at": fields.Datetime.now(),
                "dispatched_by_id": self.env.user.id,
                "dispatch_note": (note or "").strip() or False,
            })
            cuerpo = _("El vendedor marcó la compra %s como despachada.") % rec.name
            if rec.reception_required:
                cuerpo += " " + _("Tienes hasta el %(f)s para confirmar la recepción; si no lo "
                                  "haces ni reportas un problema, se confirmará automáticamente.") % {
                    "f": rec.reception_deadline.strftime("%d/%m/%Y")}
            if rec.dispatch_note:
                cuerpo += " " + _("Nota: %s") % rec.dispatch_note
            rec._shrimp_notify("buyer", _("Tu compra %s fue despachada") % rec.name, cuerpo)
        return True

    def shrimp_confirm_reception(self, note=None, photo=None, auto=False, actor=None):
        """ÚNICO punto para confirmar la recepción (portal, API y cron).

        `auto`: la confirma el cron porque venció el plazo; no comprueba la
        parte ni la fecha. Sube el inventario del comprador (action_receive)
        y deja constancia y avisos.

        `auto` y `actor` solo valen para quien ya está autorizado (sudo o
        usuario interno): por RPC, un usuario del portal confirmaba con
        auto=True cualquier compra sin ser el comprador.
        """
        if not shrimp_is_trusted(self.env):
            auto, actor = False, None
            self = shrimp_strip_context(self, RECEPTION_CONTEXT_FLAGS)
        for rec in self:
            if not auto:
                rec._shrimp_check_party("buyer", actor)
            if rec.state != "confirmed":
                raise ValidationError(_(
                    "Solo puedes confirmar la recepción de una compra confirmada."))
            vals = {"reception_note": (note or "").strip() or False}
            adjunto = self._shrimp_photo_attachment(rec, photo, "recepcion")
            if adjunto:
                vals["reception_photo_id"] = adjunto
            rec.sudo().write(vals)
            rec.sudo().with_context(shrimp_reception_auto=auto,
                                    shrimp_reception_user=self.env.user.id).action_receive()
        return True

    @api.private
    def action_receive(self):
        # Las banderas shrimp_reception_* solo las pone el servidor; si no
        # hay sudo ni usuario interno, se ignoran.
        self = shrimp_strip_context(self, RECEPTION_CONTEXT_FLAGS)
        auto = self.env.context.get("shrimp_reception_auto")
        verificada = self.env.context.get("shrimp_reception_verified")
        recs = self
        if auto:
            # El plazo empieza en la fecha de entrega o en el despacho, así
            # que al vencer ya pasó; aun así el cron no se frena por la fecha.
            recs = self.with_context(shrimp_reception_skip_date=True)
        # El lote (y el producto en borrador) que nace en manos del comprador
        # lo crea la PLATAFORMA, no quien pulsa el botón: un subusuario con
        # permiso de comprar pero no de vender (o el cron) tiene que poder
        # recibir. shrimp.product.create frena a los usuarios de portal que no
        # venden aunque se trabaje en sudo.
        recs = recs.with_user(SUPERUSER_ID).sudo()
        res = super(ShrimpTransactionReception, recs).action_receive()
        uid = self.env.context.get("shrimp_reception_user") or self.env.uid
        for rec in self:
            estado = "verified" if verificada else ("auto" if auto else "received")
            rec.sudo().write({
                "reception_status": estado,
                "reception_auto_confirmed": bool(auto),
                "received_by_id": False if (auto or verificada) else uid,
            })
            if verificada:
                rec._shrimp_after_verified_reception()
            elif self.env.context.get("shrimp_reception_quiet"):
                # Quien llama avisa con su propio texto (entrega parcial).
                try:
                    rec.sudo().activity_unlink(["mail.mail_activity_data_todo"])
                except Exception:  # noqa: BLE001
                    _logger.debug("Sin actividades que cerrar en %s", rec.name)
            else:
                rec._shrimp_after_reception(auto)
        return res

    def _shrimp_after_reception(self, auto):
        self.ensure_one()
        fecha = self.shrimp_fmt_dt(self.received_date)
        if auto:
            log = _("Recepción confirmada automáticamente el %(f)s: el comprador no la confirmó "
                    "ni reportó problemas en %(n)s días. Se considera que el vendedor cumplió.") % {
                "f": fecha, "n": self._shrimp_reception_days()}
            self._shrimp_notify("buyer", _("Recepción de %s confirmada automáticamente") % self.name,
                                log + " " + _("El lote ya está en tu inventario."))
            self._shrimp_notify("seller", _("Venta %s: recepción confirmada") % self.name,
                                log + " " + self._shrimp_invoice_hint())
        else:
            log = _("El comprador confirmó la recepción el %s.") % fecha
            if self.reception_note:
                log += " " + _("Nota: %s") % self.reception_note
            self._shrimp_notify("seller", _("Venta %s: el comprador confirmó la recepción") % self.name,
                                log + " " + self._shrimp_invoice_hint())
        if self.reception_status in ("received", "auto"):
            # Cierra la actividad de la incidencia si la había.
            try:
                self.sudo().activity_unlink(["mail.mail_activity_data_todo"])
            except Exception:  # noqa: BLE001
                _logger.debug("Sin actividades que cerrar en %s", self.name)

    def _shrimp_after_verified_reception(self):
        """Compra verificada cerrada con la aceptación del informe (lo
        extiende shrimp_verification con la referencia del informe)."""
        self.ensure_one()
        if self.env.context.get("shrimp_silent"):
            return  # migración: solo la constancia (shrimp_verification)
        texto = _("La compra %s quedó completada: la recepción la acredita la verificación que "
                  "aceptaron las dos partes.") % self.name
        self._shrimp_notify("buyer", _("Compra %s completada") % self.name,
                            texto + " " + _("El lote ya está en tu inventario."))
        self._shrimp_notify("seller", _("Venta %s completada") % self.name,
                            texto + " " + self._shrimp_invoice_hint())

    def _shrimp_invoice_hint(self):
        self.ensure_one()
        if self.has_seller_invoice:
            return ""
        texto = _("Recuerda registrar en la compra la factura de la mercadería que emites "
                  "desde tu sistema.")
        if hasattr(self, "_shrimp_invoice_days") and self._shrimp_invoice_rules_since():
            texto += " " + _("Es obligatorio dentro de %s días desde que la compra se completó.") \
                % self._shrimp_invoice_days()
        return texto

    def shrimp_report_reception_issue(self, note, photo=None, actor=None):
        """El comprador reporta un problema en vez de confirmar: la compra
        queda «Con incidencia» (el cron no la confirma) y se avisa al vendedor
        y a la plataforma, que la resuelve."""
        self = shrimp_strip_context(self, RECEPTION_CONTEXT_FLAGS)
        for rec in self:
            rec._shrimp_check_party("buyer", actor)
            if rec.state != "confirmed" or rec.reception_status not in (
                    False, "waiting", "not_delivered"):
                raise ValidationError(_("Esta compra no está esperando la recepción."))
            if not rec._shrimp_is_direct_delivery():
                raise ValidationError(_("Esta compra se resuelve con su verificación."))
            nota = (note or "").strip()
            if not nota:
                raise ValidationError(_("Describe el problema: es lo que revisa la plataforma."))
            vals = {
                "reception_status": "incidence",
                "reception_had_incidence": True,
                "incidence_date": fields.Datetime.now(),
                "incidence_note": nota,
                "incidence_reported_by_id": self.env.user.id,
            }
            adjunto = self._shrimp_photo_attachment(rec, photo, "incidencia")
            if adjunto:
                vals["incidence_photo_id"] = adjunto
            rec.sudo().write(vals)
            cuerpo = _("El comprador reportó un problema con la compra %(r)s: %(n)s") % {
                "r": rec.name, "n": nota}
            rec._shrimp_notify("seller", _("Incidencia en la venta %s") % rec.name,
                               cuerpo + " " + _("La plataforma revisará el caso."))
            rec._shrimp_notify("buyer", _("Recibimos tu reporte sobre %s") % rec.name,
                               _("Tu reporte quedó registrado y la plataforma lo revisará. "
                                 "Mientras tanto, la compra no se confirmará automáticamente."))
            rec._shrimp_notify_platform(cuerpo)
        return True

    def action_incidence_resolve_received(self):
        """Plataforma: la incidencia se resolvió y la compra se da por recibida."""
        self._shrimp_check_platform()
        for rec in self.filtered(lambda t: t.state == "confirmed"):
            rec.sudo().write({"incidence_resolution": rec.incidence_resolution or _(
                "Resuelta por la plataforma: se da por recibida.")})
            rec.with_context(shrimp_reception_skip_date=True).action_receive()
        return True

    def action_incidence_reopen(self):
        """Plataforma: la incidencia se cerró sin recibir; el comprador vuelve
        a tener el plazo completo desde hoy."""
        self._shrimp_check_platform()
        for rec in self.filtered(lambda t: t.state == "confirmed"
                                 and t.reception_status in ("incidence", "not_delivered")):
            rec.sudo().write({
                "reception_status": "waiting" if rec.reception_required else False,
                "reception_floor_date": self._shrimp_today(),
                "reception_notify_marks": " ".join(sorted(
                    m for m in rec._shrimp_marks() if not m.startswith("nd"))) or False,
            })
            rec._shrimp_notify("buyer", _("Incidencia de %s cerrada") % rec.name, _(
                "La plataforma cerró la incidencia. Confirma la recepción cuando corresponda%s.") % (
                (_(" (hasta el %s)") % rec.reception_deadline.strftime("%d/%m/%Y"))
                if rec.reception_required and rec.reception_deadline else ""))
        return True

    def _shrimp_check_platform(self):
        if not (self.env.su or self.env.user.has_group("shrimp_marketplace.group_shrimp_manager")):
            raise AccessError(_("Las incidencias las resuelve la plataforma."))

    # ------------------------------------------------------------------
    # Avisos
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_party_users(self, partner, role, permission):
        """Usuarios de la cuenta que tienen que enterarse: el administrador
        (o el dueño, en cuentas sin miembros) y los subusuarios con el permiso
        `permission` («buy» / «sell») en el perfil `role`."""
        Users = self.env["res.users"].sudo()
        if not partner:
            return Users
        holder = partner.sudo()
        if hasattr(holder, "_shrimp_role_holder"):
            holder = holder._shrimp_role_holder()
        users = Users.search([("partner_id", "=", holder.id), ("active", "=", True)])
        if "shrimp.account.member" in self.env:
            miembros = self.env["shrimp.account.member"].sudo().search([
                ("company_id", "=", holder.id), ("active", "=", True)])
            for m in miembros:
                if not m.user_id.active:
                    continue
                if m.is_admin or (role and m._allows(role, permission)):
                    users |= m.user_id
        return users

    def _shrimp_portal_url(self):
        self.ensure_one()
        return "%s/marketplace/purchases/%s/traceability#recepcion" % (
            self.get_base_url(), self.uuid_ref)

    def _shrimp_notify(self, side, subject, text):
        """Correo (y constancia en el chatter) a una de las partes."""
        self.ensure_one()
        partner = self.buyer_partner_id if side == "buyer" else self.seller_partner_id
        role = self.buyer_role if side == "buyer" else self.seller_role
        users = self._shrimp_party_users(partner, role, "buy" if side == "buyer" else "sell")
        destinatarios = users.mapped("partner_id").filtered("email") or partner.filtered("email")
        body = Markup("<p>%s</p><p><a href=\"%s\">%s</a></p>") % (
            text, self._shrimp_portal_url(), _("Ver la compra"))
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=body, subject=subject, partner_ids=destinatarios.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 — un aviso nunca tumba la compra
            _logger.warning("No se pudo avisar (%s) de la compra %s", side, self.name, exc_info=True)

    def _shrimp_notify_platform(self, text, subject=None, summary=None):
        """Incidencia: aviso y actividad para los administradores de la plataforma."""
        self.ensure_one()
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False)
        admins = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal()) if grupo else []
        if not admins:
            return
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("<p>%s</p>") % text,
                subject=subject or _("Incidencia en la recepción de %s") % self.name,
                partner_ids=admins.mapped("partner_id").ids,
                message_type="comment", subtype_xmlid="mail.mt_comment")
            for admin in admins[:5]:
                self.sudo().activity_schedule(
                    "mail.mail_activity_data_todo", user_id=admin.id,
                    summary=summary or _("Revisar incidencia de recepción"), note=text)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo avisar a la plataforma de %s", self.name, exc_info=True)

    def _shrimp_marks(self):
        return set((self.reception_notify_marks or "").split())

    def _shrimp_add_marks(self, keys):
        marcas = self._shrimp_marks() | set(keys)
        self.sudo().write({"reception_notify_marks": " ".join(sorted(marcas))})

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_reception_and_delivery(self, limit=500):
        """Cada hora: confirma las recepciones vencidas, recuerda las que
        están por vencer y avisa de las entregas próximas, de hoy y atrasadas."""
        hoy = self._shrimp_today()
        self._shrimp_cron_auto_confirm(hoy, limit)
        self._shrimp_cron_not_delivered(hoy, limit)
        self._shrimp_cron_reminders(hoy, limit)
        self._shrimp_cron_delivery_alerts(hoy, limit)
        return True

    @api.model
    def _shrimp_cron_auto_confirm(self, hoy, limit=500):
        dias = self._shrimp_reception_days()
        tope = hoy - timedelta(days=dias + 1)
        # Solo las DESPACHADAS: sin despacho no se da por cumplida una entrega
        # (ver _shrimp_cron_not_delivered).
        vencidas = self.sudo().search([
            ("state", "=", "confirmed"), ("reception_required", "=", True),
            ("reception_status", "=", "waiting"), ("reception_start_date", "<=", tope),
            ("dispatched_at", "!=", False),
        ], limit=limit, order="reception_start_date asc, id asc")
        hechas = self.browse()
        for tx in vencidas:
            try:
                with self.env.cr.savepoint():
                    tx.shrimp_confirm_reception(auto=True)
                hechas |= tx
            except Exception:  # noqa: BLE001
                _logger.exception("No se pudo confirmar automáticamente la recepción de %s", tx.name)
        return hechas

    @api.model
    def _shrimp_not_delivered_cancel_days(self):
        return self.env["shrimp.settings"].get_int(
            NOT_DELIVERED_CANCEL_PARAM, NOT_DELIVERED_CANCEL_DEFAULT, minimum=0, maximum=365)

    def _shrimp_mark_date(self, prefix):
        """Fecha guardada en las marcas técnicas («prefix:AAAA-MM-DD»)."""
        self.ensure_one()
        for m in sorted(self._shrimp_marks()):
            if m.startswith(prefix + ":"):
                try:
                    return fields.Date.to_date(m.split(":", 1)[1])
                except (TypeError, ValueError):
                    return False
        return False

    def shrimp_not_delivered_since(self):
        self.ensure_one()
        return self._shrimp_mark_date("nd_since")

    def shrimp_not_delivered_cancel_date(self):
        """Día en que la compra «No entregada» se cancela sola (o False)."""
        self.ensure_one()
        desde = self.shrimp_not_delivered_since()
        dias = self._shrimp_not_delivered_cancel_days()
        return desde + timedelta(days=dias) if desde and dias else False

    def _shrimp_mark_not_delivered(self, hoy):
        """Venció el plazo de recepción SIN despacho: la compra no se confirma
        sola. Queda «No entregada», nunca cuenta como entrega cumplida y se
        avisa a las tres partes."""
        self.ensure_one()
        self.sudo().write({"reception_status": "not_delivered", "reception_had_incidence": True})
        self._shrimp_add_marks(["nd_since:%s" % hoy.isoformat()])
        cancela = self.shrimp_not_delivered_cancel_date()
        auto = (" " + _("Si no haces nada, la compra se cancelará automáticamente sin costo el %s.")
                % cancela.strftime("%d/%m/%Y")) if cancela else ""
        self._shrimp_notify("buyer", _("Tu compra %s no fue despachada") % self.name, _(
            "Venció el plazo de recepción de tu compra %(r)s y el vendedor nunca marcó el despacho, "
            "así que NO se confirmó automáticamente. Si la recibiste, confirma la recepción; si no, "
            "puedes cancelarla sin costo desde la compra.") % {"r": self.name} + auto)
        self._shrimp_notify("seller", _("Venta %s: no entregada") % self.name, _(
            "Venció el plazo de recepción de la venta %(r)s sin que marcaras el despacho. La "
            "entrega NO se da por cumplida: el comprador puede cancelar sin costo y, si se cancela, "
            "recibirás un strike. Si la mercadería sí llegó, pídele al comprador que confirme la "
            "recepción o escribe a la plataforma.") % {"r": self.name})
        self._shrimp_notify_platform(
            _("La compra %(r)s venció su plazo de recepción sin despacho del vendedor: quedó «No "
              "entregada». El comprador puede confirmar o cancelar sin costo.%(a)s") % {
                "r": self.name, "a": (" " + _("Se cancela sola el %s.") % cancela.strftime("%d/%m/%Y"))
                if cancela else ""},
            subject=_("Compra no entregada: %s") % self.name,
            summary=_("Revisar compra no entregada"))

    @api.model
    def _shrimp_cron_not_delivered(self, hoy, limit=500):
        """Plazo de recepción vencido sin despacho ─▶ «No entregada»; y las
        «No entregadas» sin respuesta del comprador en N días se cancelan sin
        costo (shrimp_transaction_cancel.py)."""
        dias = self._shrimp_reception_days()
        tope = hoy - timedelta(days=dias + 1)
        vencidas = self.sudo().search([
            ("state", "=", "confirmed"), ("reception_required", "=", True),
            ("reception_status", "=", "waiting"), ("reception_start_date", "<=", tope),
            ("dispatched_at", "=", False),
        ], limit=limit, order="reception_start_date asc, id asc")
        marcadas = self.browse()
        for tx in vencidas:
            if hasattr(tx, "shrimp_delay_open") and tx.shrimp_delay_open():
                continue  # un aviso de retraso en negociación decide primero
            try:
                with self.env.cr.savepoint():
                    tx._shrimp_mark_not_delivered(hoy)
                marcadas |= tx
            except Exception:  # noqa: BLE001
                _logger.exception("No se pudo marcar como no entregada la compra %s", tx.name)
        if self._shrimp_not_delivered_cancel_days() and hasattr(self, "_shrimp_cancel_not_delivered_auto"):
            for tx in self.sudo().search([("state", "=", "confirmed"),
                                          ("reception_status", "=", "not_delivered"),
                                          ("dispatched_at", "=", False)], limit=limit):
                cancela = tx.shrimp_not_delivered_cancel_date()
                if not cancela or hoy < cancela:
                    continue
                try:
                    with self.env.cr.savepoint():
                        tx._shrimp_cancel_not_delivered_auto()
                except Exception:  # noqa: BLE001
                    _logger.exception("No se pudo cancelar la compra no entregada %s", tx.name)
        return marcadas

    @api.model
    def _shrimp_cron_reminders(self, hoy, limit=500):
        recordatorios = self._shrimp_reception_reminders()
        if not recordatorios:
            return self.browse()
        dias = self._shrimp_reception_days()
        pendientes = self.sudo().search([
            ("state", "=", "confirmed"), ("reception_required", "=", True),
            ("reception_status", "=", "waiting"), ("reception_start_date", "<=", hoy),
            ("reception_start_date", ">=", hoy - timedelta(days=dias)),
        ], limit=limit)
        avisadas = self.browse()
        for tx in pendientes:
            if not tx.shrimp_reception_window_open():
                continue
            limite = tx.reception_deadline
            quedan = (limite - hoy).days
            marcas = tx._shrimp_marks()
            tocan = ["rem:%s:%s" % (limite.isoformat(), r) for r in recordatorios if quedan <= r]
            nuevas = [k for k in tocan if k not in marcas]
            if not nuevas:
                continue
            # Un solo correo aunque se hayan juntado varios umbrales (p. ej.
            # la compra se despachó tarde): el más urgente.
            texto = (_("Tienes %(d)s días para confirmar la recepción de la compra %(r)s "
                       "(hasta el %(f)s).") if quedan != 1 else
                     _("Tienes %(d)s día para confirmar la recepción de la compra %(r)s "
                       "(hasta el %(f)s).")) % {
                "d": quedan, "r": tx.name, "f": limite.strftime("%d/%m/%Y")}
            if quedan <= 0:
                texto = _("Hoy es el último día para confirmar la recepción de la compra %s.") % tx.name
            if tx.dispatched_at:
                texto += " " + _("Si no la confirmas ni reportas un problema, se confirmará "
                                 "automáticamente y se dará por cumplida la entrega.")
            else:
                texto += " " + _("El vendedor todavía no marcó el despacho: si no la recibiste, no "
                                 "hagas nada; sin despacho la compra no se confirma sola y podrás "
                                 "cancelarla sin costo.")
            tx._shrimp_notify("buyer", _("Confirma la recepción de %s") % tx.name, texto)
            tx._shrimp_add_marks(nuevas)
            avisadas |= tx
        return avisadas

    @api.model
    def _shrimp_cron_delivery_alerts(self, hoy, limit=500):
        anticipacion = self._shrimp_delivery_alert_days()
        candidatas = self.sudo().search([
            ("state", "=", "confirmed"), ("delivery_date", "!=", False),
            ("delivery_date", "<=", hoy + timedelta(days=anticipacion)),
            ("delivery_date", ">=", hoy - timedelta(days=60)),
            ("reception_status", "not in", ("incidence", "not_delivered")),
        ], limit=limit, order="delivery_date asc, id asc")
        avisadas = self.browse()
        for tx in candidatas:
            if not tx._shrimp_is_direct_delivery():
                continue
            fecha = tx.delivery_date
            f = fecha.strftime("%d/%m/%Y")
            faltan = (fecha - hoy).days
            marcas = tx._shrimp_marks()
            k_pre, k_dia, k_late = ("dlv_pre:%s" % fecha, "dlv_day:%s" % fecha,
                                    "dlv_late:%s" % fecha)
            nuevas = []
            if faltan > 0 and anticipacion and k_pre not in marcas:
                cuando = _("mañana") if faltan == 1 else _("en %s días") % faltan
                if not tx.dispatched_at:
                    tx._shrimp_notify("seller", _("Prepara el despacho de %s") % tx.name, _(
                        "La entrega de la venta %(r)s a %(c)s es el %(f)s (%(d)s). "
                        "Cuando despaches, márcalo en la compra.") % {
                        "r": tx.name, "c": tx.buyer_partner_id.name, "f": f, "d": cuando})
                tx._shrimp_notify("buyer", _("Tu compra %s llega pronto") % tx.name, _(
                    "La entrega de tu compra %(r)s (%(p)s) está prevista para el %(f)s "
                    "(%(d)s). Prepárate para recibirla.") % {
                    "r": tx.name, "p": tx.product_id.name, "f": f, "d": cuando})
                nuevas.append(k_pre)
            elif faltan == 0 and k_dia not in marcas:
                if not tx.dispatched_at:
                    tx._shrimp_notify("seller", _("Hoy es la entrega de %s") % tx.name, _(
                        "Hoy (%(f)s) es la fecha de entrega de la venta %(r)s a %(c)s. "
                        "Marca el despacho en la compra cuando salga.") % {
                        "r": tx.name, "c": tx.buyer_partner_id.name, "f": f})
                tx._shrimp_notify("buyer", _("Hoy llega tu compra %s") % tx.name, _(
                    "Hoy (%(f)s) es la fecha de entrega de tu compra %(r)s. Cuando la recibas, "
                    "confírmalo en la plataforma.") % {"r": tx.name, "f": f})
                nuevas += [k_pre, k_dia]
            elif faltan < 0 and not tx.dispatched_at and k_late not in marcas:
                tx._shrimp_notify("seller", _("Entrega atrasada: %s") % tx.name, _(
                    "La entrega de la venta %(r)s a %(c)s era el %(f)s y no está marcada como "
                    "despachada. Despacha o avisa al comprador.") % {
                    "r": tx.name, "c": tx.buyer_partner_id.name, "f": f})
                tx._shrimp_notify("buyer", _("Tu compra %s está atrasada") % tx.name, _(
                    "La entrega de tu compra %(r)s era el %(f)s y el vendedor todavía no la marcó "
                    "como despachada. Si ya la recibiste, confírmalo; si hay un problema, "
                    "repórtalo desde la compra.") % {"r": tx.name, "f": f})
                nuevas += [k_pre, k_dia, k_late]
            if nuevas:
                tx._shrimp_add_marks(nuevas)
                avisadas |= tx
        return avisadas


class ResPartnerDeliveryMetrics(models.Model):
    _inherit = "res.partner"

    shrimp_delivery_count = fields.Integer(
        string="Entregas cerradas", compute="_compute_shrimp_delivery_metrics",
        help="Ventas con confirmación de recepción ya recibidas (por el comprador o "
             "automáticamente).")
    shrimp_delivery_ok_count = fields.Integer(
        string="Entregas sin incidencia", compute="_compute_shrimp_delivery_metrics")
    shrimp_delivery_auto_count = fields.Integer(
        string="Confirmadas automáticamente", compute="_compute_shrimp_delivery_metrics")
    shrimp_delivery_ok_pct = fields.Float(
        string="Entregas cumplidas (%)", compute="_compute_shrimp_delivery_metrics",
        digits=(5, 1))

    def _compute_shrimp_delivery_metrics(self):
        Tx = self.env["shrimp.transaction"].sudo()
        for rec in self:
            if not rec.id:
                rec.shrimp_delivery_count = rec.shrimp_delivery_ok_count = 0
                rec.shrimp_delivery_auto_count = 0
                rec.shrimp_delivery_ok_pct = 0.0
                continue
            cerradas = Tx.search([("seller_partner_id", "=", rec.id),
                                  ("reception_required", "=", True),
                                  ("reception_status", "in", ("received", "auto"))])
            ok = cerradas.filtered(lambda t: not t.reception_had_incidence)
            rec.shrimp_delivery_count = len(cerradas)
            rec.shrimp_delivery_ok_count = len(ok)
            rec.shrimp_delivery_auto_count = len(cerradas.filtered("reception_auto_confirmed"))
            rec.shrimp_delivery_ok_pct = (100.0 * len(ok) / len(cerradas)) if cerradas else 0.0
