"""D-21: la verificadora ACEPTA o DECLINA la orden (auditoría de procesos, AL-06).

ANTES: la orden nacía «Recibida» en la bandeja de la verificadora y no había
forma de devolverla (sin capacidad, conflicto de interés...): solo quedaba
dejarla vencer.

AHORA (decisión del usuario, definitiva):

  orden creada ──▶ verifier_response = «Por responder»
     · la verificadora la ACEPTA (botón, o al asignar el técnico / dictaminar);
     · o la DECLINA con un motivo, dentro de N horas desde la asignación
       (Ajustes › Verificación, 24 h; configurable);
     · si en ese plazo no la acepta ni la declina, cuenta como declinada.
  declinada (o sin respuesta) ──▶ el COMPRADOR elige otra verificadora
     (mismo flujo de «cambiar de verificadora» de la orden vencida: el
     honorario se recalcula con la tarifa de la nueva, D-40) o cancela la
     compra sin costo (sin strike al vendedor). Se avisa al comprador, al
     vendedor y a la plataforma; si nadie hace nada, la compra se cancela sola
     a los N días (los mismos de la orden vencida).

Las órdenes que ya existían al instalar esto quedan «Aceptada» (migración
19.0.1.24.0): no se les abre un plazo de respuesta retroactivo.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.shrimp_user_registry.authz import shrimp_actor, shrimp_is_trusted

_logger = logging.getLogger(__name__)

RESPONSE_HOURS_PARAM = "shrimp_verification.verifier_response_hours"
RESPONSE_HOURS_DEFAULT = 24


class ShrimpVerificationResponse(models.Model):
    _inherit = "shrimp.verification"

    verifier_response = fields.Selection(
        [("pending", "Por responder"), ("accepted", "Aceptada"), ("declined", "Declinada")],
        string="Respuesta de la verificadora", copy=False, index=True, tracking=True,
        help="La verificadora acepta la orden (o la acepta al asignar el técnico) o la declina "
             "con un motivo dentro del plazo. Declinada o sin respuesta, el comprador elige otra "
             "verificadora o cancela sin costo.")
    verifier_response_date = fields.Datetime(string="Respondió el", readonly=True, copy=False)
    verifier_decline_reason = fields.Text(string="Motivo para declinar", readonly=True, copy=False)

    # Calculado, sin columna: hasta cuándo puede declinar.
    verifier_response_deadline = fields.Datetime(
        string="Responder (aceptar o declinar) hasta", compute="_compute_response_deadline")

    deadline_overdue = fields.Selection(selection_add=[
        ("declined", "La verificadora declinó la orden"),
        ("response", "La verificadora no respondió a tiempo"),
    ])

    @api.model
    def _shrimp_response_hours(self):
        return self.env["shrimp.settings"].get_int(
            RESPONSE_HOURS_PARAM, RESPONSE_HOURS_DEFAULT, minimum=1, maximum=24 * 30)

    def _shrimp_response_limit(self, hours=None):
        self.ensure_one()
        inicio = self.assigned_date or self.create_date
        if not inicio:
            return False
        return inicio + timedelta(hours=hours or self._shrimp_response_hours())

    @api.depends("assigned_date", "verifier_response", "verification_mode")
    def _compute_response_deadline(self):
        horas = self._shrimp_response_hours()
        for rec in self:
            rec.verifier_response_deadline = (
                rec._shrimp_response_limit(horas)
                if rec.verification_mode == "platform" and rec.id else False)

    @api.depends("state", "assigned_date", "transaction_id.delivery_date", "verification_mode",
                 "verifier_response")
    def _compute_deadlines(self):
        super()._compute_deadlines()
        ahora = fields.Datetime.now()
        horas = self._shrimp_response_hours()
        for rec in self:
            if not rec.id or rec.verification_mode != "platform" \
                    or not rec._shrimp_deadline_applies():
                continue
            if rec.verifier_response == "declined":
                rec.deadline_overdue = "declined"
            elif rec.verifier_response == "pending" and rec.state == "received":
                limite = rec._shrimp_response_limit(horas)
                if limite and ahora > limite:
                    rec.deadline_overdue = "response"

    def shrimp_overdue_at(self):
        self.ensure_one()
        if self.deadline_overdue == "declined":
            return self.verifier_response_date or fields.Datetime.now()
        if self.deadline_overdue == "response":
            return self._shrimp_response_limit()
        return super().shrimp_overdue_at()

    # ------------------------------------------------------------------
    # Acciones de la verificadora
    # ------------------------------------------------------------------
    def shrimp_can_decline(self):
        """'' si la verificadora puede declinar ahora; si no, por qué."""
        self.ensure_one()
        if self.verification_mode != "platform":
            return _("Esta verificación no la hace una verificadora de la plataforma.")
        if self.state not in ("received", "assigned") or self.field_start_date:
            return _("La orden ya está en campo o cerrada: ya no se puede declinar.")
        if self.verifier_response == "declined":
            return _("Ya declinaste esta orden.")
        limite = self._shrimp_response_limit()
        if limite and fields.Datetime.now() > limite:
            return _("Venció el plazo para declinar la orden (%s).") % \
                self.transaction_id.shrimp_fmt_dt(limite)
        return ""

    def _shrimp_check_response_caller(self):
        """Por RPC, solo el administrador de la verificadora asignada (el
        mismo socio que pasan el portal y la API). El controlador y la API
        lo comprueban y llaman en sudo; la plataforma (interno) no tiene
        límite."""
        if shrimp_is_trusted(self.env):
            return True
        yo = shrimp_actor(self.env)
        for rec in self:
            # sudo() conserva env.user: _shrimp_user_allows mira sus permisos.
            rec.sudo()._shrimp_check_response_admin(yo.sudo())
        return True

    def action_verifier_accept(self):
        self._shrimp_check_response_caller()
        for rec in self.sudo():
            if rec.verification_mode != "platform" or rec.state not in ("received", "assigned"):
                raise UserError(_("Esta orden ya no espera tu respuesta."))
            if rec.verifier_response == "declined":
                raise UserError(_("Ya declinaste esta orden: el comprador elige otra verificadora."))
            if rec.verifier_response == "accepted":
                continue
            rec.write({"verifier_response": "accepted",
                       "verifier_response_date": fields.Datetime.now()})
            rec._message_log(body=_("La verificadora %s aceptó la orden.")
                             % rec.verifier_partner_id.name)
        return True

    def action_verifier_decline(self, reason):
        """La verificadora devuelve la orden. El comprador elige otra o cancela."""
        self.ensure_one()
        self._shrimp_check_response_caller()
        rec = self.sudo()
        motivo = (reason or "").strip()
        if not motivo:
            raise ValidationError(_("Explica por qué declinas la orden: el comprador lo verá."))
        problema = rec.shrimp_can_decline()
        if problema:
            raise ValidationError(problema)
        contactos = rec._shrimp_verifier_contacts()
        rec.write({"verifier_response": "declined", "verifier_response_date": fields.Datetime.now(),
                   "verifier_decline_reason": motivo, "state": "received",
                   "technician_partner_id": False, "support_technician_ids": [(5, 0, 0)]})
        try:
            rec.activity_unlink(["mail.mail_activity_data_todo"])
        except Exception:  # noqa: BLE001
            _logger.debug("Sin actividades que cerrar en %s", rec.name)
        rec._shrimp_notify_declined(contactos, motivo, auto=False)
        return True

    def _shrimp_check_response_admin(self, partner):
        """Acepta o declina el administrador de la empresa verificadora (o un
        subusuario con el permiso «verify»), nunca un técnico."""
        self.ensure_one()
        empresa = partner.shrimp_verifier_company() if partner else partner
        if not (empresa and partner.shrimp_is_verifier_admin()
                and self.verifier_partner_id == empresa
                and partner.sudo()._shrimp_user_allows("verify", "verificador")):
            raise AccessError(_("La orden la acepta o la declina el administrador de la empresa "
                                "verificadora."))
        return True

    def _shrimp_notify_declined(self, contactos, motivo, auto=False):
        self.ensure_one()
        tx = self.transaction_id.sudo()
        if auto:
            causa = _("no aceptó ni declinó la orden dentro de %s horas") % \
                self._shrimp_response_hours()
        else:
            causa = _("declinó la orden (motivo: %s)") % motivo
        auto_txt = ""
        dias = self._shrimp_deadline_params()["auto_cancel_days"]
        if dias:
            auto_txt = " " + _("Si no eliges, la compra se cancela sola, sin costo, en %s días.") % dias
        self._shrimp_ver_notify(contactos, _("Orden %s declinada") % self.name, _(
            "La orden %(o)s quedó como declinada: %(c)s. El comprador elegirá otra "
            "verificadora.") % {"o": self.name, "c": causa})
        tx._shrimp_notify("buyer", _("Elige otra verificadora para %s") % tx.name, _(
            "La verificadora %(v)s %(c)s. Elige otra verificadora acreditada desde la compra (el "
            "honorario se recalcula con su tarifa) o cancela la compra sin costo.") % {
            "v": self.verifier_partner_id.name, "c": causa} + auto_txt)
        tx._shrimp_notify("seller", _("Venta %s: la verificadora declinó") % tx.name, _(
            "La verificadora %(v)s %(c)s. El comprador elegirá otra verificadora o cancelará sin "
            "costo; no es un incumplimiento tuyo.") % {"v": self.verifier_partner_id.name,
                                                         "c": causa})
        self._shrimp_ver_escalate(_(
            "La verificadora %(v)s %(c)s (compra %(r)s). El comprador debe elegir otra o "
            "cancelar.") % {"v": self.verifier_partner_id.name, "c": causa, "r": tx.name})

    def action_assign_technician(self, technician=None):
        # Antes del write en sudo de abajo (asignar es aceptar la orden).
        self._shrimp_check_verifier_caller()
        for rec in self:
            if rec.verifier_response == "declined":
                raise UserError(_("Declinaste esta orden: ya no puedes asignarle técnico."))
        res = super().action_assign_technician(technician=technician)
        # Asignar el técnico es aceptar la orden.
        pendientes = self.filtered(lambda r: r.verification_mode == "platform"
                                   and r.verifier_response != "accepted")
        if pendientes:
            pendientes.sudo().write({"verifier_response": "accepted",
                                     "verifier_response_date": fields.Datetime.now()})
        return res

    def action_start_field(self):
        if any(r.verifier_response == "declined" for r in self):
            raise UserError(_("Declinaste esta orden: ya no puedes iniciarla."))
        return super().action_start_field()

    # ------------------------------------------------------------------
    # Cron (se suma al de plazos de la verificadora)
    # ------------------------------------------------------------------
    def _shrimp_deadline_step(self, params, ahora):
        """Declinada o sin respuesta: un solo aviso (al comprador, al
        vendedor, a la plataforma) y la cancelación automática a los N días
        si nadie la resuelve. El resto de plazos, como siempre."""
        self.ensure_one()
        if self.verifier_response not in ("declined", "pending") or self.state != "received":
            return super()._shrimp_deadline_step(params, ahora)
        tx = self.transaction_id.sudo()
        if self.verifier_response == "pending":
            limite = self._shrimp_response_limit()
            if not limite or ahora <= limite:
                return super()._shrimp_deadline_step(params, ahora)
            clave = "vrsp_late:%s" % limite.date().isoformat()
            if clave not in tx._shrimp_marks():
                self._shrimp_notify_declined(self._shrimp_verifier_contacts(), "", auto=True)
                tx._shrimp_add_marks([clave])
                return
        else:
            limite = self.verifier_response_date or ahora
        dias = params["auto_cancel_days"]
        if dias and ahora >= limite + timedelta(days=dias):
            tx._shrimp_cancel_apply(
                "system", "verification_overdue",
                _("La verificadora %(v)s declinó la orden (o no respondió) y el comprador no eligió "
                  "otra en %(d)s días: se cancela sin costo.") % {
                    "v": self.verifier_partner_id.name, "d": dias})

    def _shrimp_reassign_verifier(self, nueva, motivo, quien):
        res = super()._shrimp_reassign_verifier(nueva, motivo, quien)
        # La nueva verificadora también acepta o declina, con su propio plazo.
        self.sudo().write({"verifier_response": "pending", "verifier_response_date": False,
                           "verifier_decline_reason": False})
        tx = self.transaction_id.sudo()
        tx.write({"reception_notify_marks": " ".join(sorted(
            m for m in tx._shrimp_marks() if not m.startswith("vrsp_"))) or False})
        return res
