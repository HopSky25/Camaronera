"""Plazos de la verificadora y salida de una verificación atascada
(hueco #2 de la auditoría de procesos).

ANTES: una verificación de plataforma no tenía plazo en received, assigned,
in_field ni done; nadie podía cancelarla desde una pantalla. La compra se
quedaba en «Pendiente de verificación» con el stock RESERVADO para siempre
(57 compras en Pruebas, vendidas entre marzo y julio).

AHORA (Ajustes › CamaronMarket › Verificación, todo configurable):

  orden creada ──(N h, 48)──▶ debe tener técnico asignado
  fecha de entrega/cosecha (o la orden, si es posterior) ──(N días, 3)──▶
      debe tener el veredicto emitido

  · recordatorio a la verificadora N h antes de cada plazo (24);
  · al vencer: aviso a la verificadora (y al técnico), a las dos partes y
    ESCALAMIENTO a la plataforma (mensaje + actividad para los
    administradores);
  · vencida, el COMPRADOR puede:
        - cancelar sin costo: se cancela la verificación, se anula el
          honorario (nota de crédito si ya estaba facturado), se libera la
          reserva de stock; sin strike al vendedor (la culpa es de la
          verificadora);
        - cambiar a otra verificadora acreditada: la misma orden pasa a la
          nueva (vuelve a «Recibida», plazos desde ahora, honorario con la
          tarifa de la nueva, D-40);
        en los dos casos a la verificadora que no cumplió no se le liquida
        nada: no emitió veredicto (regla de pago, 23-oct-2026);
  · si nadie hace nada, la compra se cancela sola N días después del
    vencimiento (7; 0 = nunca), con los mismos efectos que la cancelación
    del comprador;
  · la plataforma (back-office) puede en cualquier momento reasignar la
    verificadora o cancelar la verificación y la compra (motivo obligatorio).

Las órdenes que ya existían al instalar esto no vencen de golpe: los plazos
cuentan desde la fecha del -u (parámetro DEADLINE_SINCE_PARAM, lo fija la
migración 19.0.1.21.0).

Las marcas de avisos ya enviados van en reception_notify_marks de la compra
(sin columnas nuevas; la compra verificada no usa la recepción directa).
"""
import logging
from datetime import datetime, time, timedelta

import pytz

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

ASSIGN_HOURS_PARAM = "shrimp_verification.deadline_assign_hours"
ASSIGN_HOURS_DEFAULT = 48
VERDICT_DAYS_PARAM = "shrimp_verification.deadline_verdict_days"
VERDICT_DAYS_DEFAULT = 3
REMINDER_HOURS_PARAM = "shrimp_verification.deadline_reminder_hours"
REMINDER_HOURS_DEFAULT = 24
AUTO_CANCEL_DAYS_PARAM = "shrimp_verification.deadline_auto_cancel_days"
AUTO_CANCEL_DAYS_DEFAULT = 7
DEADLINE_SINCE_PARAM = "shrimp_verification.deadline_rules_since"

OPEN_PLATFORM_STATES = ("received", "assigned", "in_field", "done")


class ShrimpVerificationDeadline(models.Model):
    _inherit = "shrimp.verification"

    # Calculados, sin columna.
    deadline_assign = fields.Datetime(string="Asignar técnico hasta",
                                      compute="_compute_deadlines")
    deadline_verdict = fields.Datetime(string="Emitir el veredicto hasta",
                                       compute="_compute_deadlines")
    deadline_overdue = fields.Selection(
        [("assign", "Sin técnico asignado a tiempo"), ("verdict", "Sin veredicto a tiempo")],
        string="Plazo vencido", compute="_compute_deadlines")

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_deadline_params(self):
        S = self.env["shrimp.settings"]
        return {
            "assign_hours": S.get_int(ASSIGN_HOURS_PARAM, ASSIGN_HOURS_DEFAULT, minimum=1,
                                      maximum=24 * 60),
            "verdict_days": S.get_int(VERDICT_DAYS_PARAM, VERDICT_DAYS_DEFAULT, minimum=1,
                                      maximum=365),
            "reminder_hours": S.get_int(REMINDER_HOURS_PARAM, REMINDER_HOURS_DEFAULT, minimum=0,
                                        maximum=24 * 30),
            "auto_cancel_days": S.get_int(AUTO_CANCEL_DAYS_PARAM, AUTO_CANCEL_DAYS_DEFAULT,
                                          minimum=0, maximum=365),
        }

    @api.model
    def _shrimp_deadline_since(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(DEADLINE_SINCE_PARAM)
        try:
            return fields.Date.to_date(raw) if raw else False
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Plazos
    # ------------------------------------------------------------------
    def _shrimp_deadline_applies(self):
        self.ensure_one()
        return bool(self.verification_mode == "platform" and self.state in OPEN_PLATFORM_STATES
                    and self.transaction_id.state == "pending_verification")

    def _shrimp_end_of_day_utc(self, day):
        """23:59 del día `day` en la zona de la plataforma, en UTC (naive)."""
        tz = self.env["shrimp.transaction"].with_context(shrimp_public_tz=True)._shrimp_tz()
        local = datetime.combine(day, time(23, 59, 59))
        return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    def _shrimp_compute_deadlines(self, params=None):
        """(asignar hasta, veredicto hasta) en UTC naive, o (False, False)."""
        self.ensure_one()
        if self.verification_mode != "platform":
            return False, False
        p = params or self._shrimp_deadline_params()
        desde = self._shrimp_deadline_since()
        inicio = self.assigned_date or self.create_date or fields.Datetime.now()
        base_asig = inicio
        if desde:
            piso = datetime.combine(desde, time.min)
            base_asig = max(base_asig, piso)
        asignar = base_asig + timedelta(hours=p["assign_hours"])
        tx = self.transaction_id
        ref = tx.with_context(shrimp_public_tz=True).shrimp_local_date(inicio)
        if tx.delivery_date and tx.delivery_date > ref:
            ref = tx.delivery_date
        if desde and desde > ref:
            ref = desde
        veredicto = self._shrimp_end_of_day_utc(ref + timedelta(days=p["verdict_days"]))
        return asignar, veredicto

    @api.depends("state", "assigned_date", "transaction_id.delivery_date", "verification_mode")
    def _compute_deadlines(self):
        params = self._shrimp_deadline_params()
        ahora = fields.Datetime.now()
        for rec in self:
            if not rec.id or rec.verification_mode != "platform":
                rec.deadline_assign = rec.deadline_verdict = False
                rec.deadline_overdue = False
                continue
            asignar, veredicto = rec._shrimp_compute_deadlines(params)
            rec.deadline_assign = asignar
            rec.deadline_verdict = veredicto
            vencido = False
            if rec._shrimp_deadline_applies():
                if rec.state == "received" and asignar and ahora > asignar:
                    vencido = "assign"
                elif veredicto and ahora > veredicto:
                    vencido = "verdict"
            rec.deadline_overdue = vencido

    def shrimp_overdue_at(self):
        """Desde cuándo está vencida (UTC naive) o False."""
        self.ensure_one()
        if self.deadline_overdue == "assign":
            return self.deadline_assign
        if self.deadline_overdue == "verdict":
            return self.deadline_verdict
        return False

    def shrimp_auto_cancel_at(self):
        self.ensure_one()
        vencida = self.shrimp_overdue_at()
        dias = self._shrimp_deadline_params()["auto_cancel_days"]
        return vencida + timedelta(days=dias) if vencida and dias else False

    # ------------------------------------------------------------------
    # Avisos
    # ------------------------------------------------------------------
    def _shrimp_ver_notify(self, partners, subject, text):
        """Mensaje (correo) en la verificación a los contactos dados."""
        self.ensure_one()
        destinatarios = partners.filtered(lambda p: p and p.email)
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("<p>%s</p>") % text, subject=subject,
                partner_ids=destinatarios.ids, message_type="comment",
                subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 — un aviso nunca tumba el cron
            _logger.warning("No se pudo avisar en la verificación %s", self.name, exc_info=True)

    def _shrimp_ver_escalate(self, text):
        """Escalamiento a la plataforma: mensaje y actividad."""
        self.ensure_one()
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager", raise_if_not_found=False)
        admins = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal()) if grupo else []
        if not admins:
            return
        try:
            self.sudo().message_post(
                body=Markup("<p>%s</p>") % text, subject=_("Verificación vencida: %s") % self.name,
                partner_ids=admins.mapped("partner_id").ids, message_type="comment",
                subtype_xmlid="mail.mt_comment")
            for admin in admins[:5]:
                self.sudo().activity_schedule(
                    "mail.mail_activity_data_todo", user_id=admin.id,
                    summary=_("Verificación vencida: reasignar o cancelar"), note=text)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo escalar la verificación %s", self.name, exc_info=True)

    def _shrimp_verifier_contacts(self):
        self.ensure_one()
        return (self.verifier_partner_id | self.technician_partner_id
                | self.support_technician_ids).sudo()

    @api.model
    def _shrimp_cron_deadlines(self, limit=500):
        """Recordatorios, vencimientos (aviso + escalamiento) y cancelación
        automática de las verificaciones de plataforma abiertas."""
        params = self._shrimp_deadline_params()
        ahora = fields.Datetime.now()
        abiertas = self.sudo().search([
            ("verification_mode", "=", "platform"), ("state", "in", OPEN_PLATFORM_STATES),
            ("transaction_id.state", "=", "pending_verification"),
        ], limit=limit, order="id asc")
        for ver in abiertas:
            try:
                with self.env.cr.savepoint():
                    ver._shrimp_deadline_step(params, ahora)
            except Exception:  # noqa: BLE001
                _logger.exception("Plazos de la verificación %s", ver.name)
        return True

    def _shrimp_deadline_step(self, params, ahora):
        self.ensure_one()
        tx = self.transaction_id.sudo()
        asignar, veredicto = self._shrimp_compute_deadlines(params)
        marcas = tx._shrimp_marks()
        nuevas = []
        recordar = timedelta(hours=params["reminder_hours"])
        fmt = lambda d: tx.shrimp_fmt_dt(d)  # noqa: E731
        etapa = None
        if self.state == "received" and asignar:
            etapa, limite = "asg", asignar
        elif veredicto:
            etapa, limite = "vdt", veredicto
        if not etapa:
            return
        k_rem = "v%s_rem:%s" % (etapa, limite.date().isoformat())
        k_late = "v%s_late:%s" % (etapa, limite.date().isoformat())
        que = (_("asignar un técnico a la orden %s") if etapa == "asg"
               else _("emitir el veredicto de la orden %s")) % self.name
        if ahora <= limite:
            if recordar and ahora >= limite - recordar and k_rem not in marcas:
                self._shrimp_ver_notify(self._shrimp_verifier_contacts(),
                                        _("Plazo por vencer: %s") % self.name,
                                        _("Tienes hasta el %(f)s para %(q)s (compra %(r)s). Si vence "
                                          "el plazo, el comprador podrá cancelar sin costo o cambiar "
                                          "de verificadora.") % {"f": fmt(limite), "q": que,
                                                                 "r": tx.name})
                nuevas.append(k_rem)
        elif k_late not in marcas:
            auto = limite + timedelta(days=params["auto_cancel_days"]) \
                if params["auto_cancel_days"] else False
            txt_auto = (" " + _("Si nadie la resuelve, la compra se cancela sola el %s.")
                        % fmt(auto)) if auto else ""
            self._shrimp_ver_notify(self._shrimp_verifier_contacts(),
                                    _("Plazo vencido: %s") % self.name,
                                    _("Venció el plazo para %(q)s (era el %(f)s). La plataforma fue "
                                      "avisada y el comprador puede cancelar sin costo o cambiar de "
                                      "verificadora.") % {"q": que, "f": fmt(limite)} + txt_auto)
            tx._shrimp_notify("buyer", _("La verificación de tu compra %s está vencida") % tx.name, _(
                "La verificadora %(v)s no cumplió el plazo para %(q)s (era el %(f)s). Puedes cancelar "
                "la compra sin costo (se anula el honorario y se libera el stock) o cambiar a otra "
                "verificadora desde la compra.") % {
                "v": self.verifier_partner_id.name, "q": que, "f": fmt(limite)} + txt_auto)
            tx._shrimp_notify("seller", _("Venta %s: verificación vencida") % tx.name, _(
                "La verificadora %(v)s no cumplió el plazo para %(q)s. El comprador puede cancelar "
                "sin costo o cambiar de verificadora; no es un incumplimiento tuyo.") % {
                "v": self.verifier_partner_id.name, "q": que} + txt_auto)
            self._shrimp_ver_escalate(_(
                "Venció el plazo de la verificadora %(v)s para %(q)s (compra %(r)s, plazo %(f)s). "
                "Reasígnala o cancélala desde la orden.") % {
                "v": self.verifier_partner_id.name, "q": que, "r": tx.name, "f": fmt(limite)}
                + txt_auto)
            nuevas += [k_rem, k_late]
        else:
            auto = limite + timedelta(days=params["auto_cancel_days"]) \
                if params["auto_cancel_days"] else False
            if auto and ahora >= auto:
                tx._shrimp_cancel_apply(
                    "system", "verification_overdue",
                    _("La verificadora %(v)s no cumplió el plazo para %(q)s y nadie resolvió la "
                      "orden en %(d)s días: se cancela sin costo.") % {
                        "v": self.verifier_partner_id.name, "q": que,
                        "d": params["auto_cancel_days"]})
                return
        if nuevas:
            tx._shrimp_add_marks(nuevas)

    @api.model
    def _cron_vencer_plazos(self):
        res = super()._cron_vencer_plazos()
        try:
            self._shrimp_cron_deadlines()
        except Exception:  # noqa: BLE001
            _logger.exception("Plazos de verificación: fallo del cron")
        return res

    # ------------------------------------------------------------------
    # Reasignar la verificadora (comprador con la orden vencida, o plataforma)
    # ------------------------------------------------------------------
    def _shrimp_reassign_verifier(self, nueva, motivo, quien):
        """La MISMA orden pasa a otra verificadora (la compra tiene una sola
        verificación): vuelve a «Recibida», sin técnico ni equipo, plazos
        desde ahora y el honorario RECALCULADO con la tarifa publicada por la
        nueva verificadora (D-40; el cobro anterior se anula o se acredita y
        se emite uno nuevo por el importe nuevo)."""
        self.ensure_one()
        ver = self.sudo()
        if ver.verification_mode != "platform" or ver.state not in OPEN_PLATFORM_STATES:
            raise ValidationError(_("Solo se reasigna una verificación de plataforma abierta."))
        nueva = nueva.sudo()
        tx = ver.transaction_id
        if not nueva or nueva == ver.verifier_partner_id:
            raise ValidationError(_("Elige una verificadora distinta de la actual."))
        if not nueva._shrimp_has_role("verificador") or not nueva.verifier_is_accredited:
            raise ValidationError(_("La verificadora «%s» no tiene una acreditación aprobada y "
                                    "vigente.") % nueva.name)
        if nueva.shrimp_same_entity(tx.buyer_partner_id, tx.seller_partner_id) \
                or nueva in (tx.buyer_partner_id, tx.seller_partner_id):
            raise ValidationError(_("La verificadora no puede ser el comprador ni el vendedor."))
        anterior = ver.verifier_partner_id
        anteriores = ver._shrimp_verifier_contacts()
        ver.write({"verifier_partner_id": nueva.id, "technician_partner_id": False,
                   "support_technician_ids": [(5, 0, 0)],
                   "state": "received", "assigned_date": fields.Datetime.now(),
                   "field_start_date": False})
        ver._shrimp_refee_for_verifier()
        # Las marcas de plazos viejas no cuentan para la nueva verificadora.
        tx.sudo().write({"reception_notify_marks": " ".join(sorted(
            m for m in tx._shrimp_marks() if not m.startswith(("vasg_", "vvdt_")))) or False})
        try:
            ver.activity_unlink(["mail.mail_activity_data_todo"])
        except Exception:  # noqa: BLE001
            _logger.debug("Sin actividades que cerrar en %s", ver.name)
        texto = _("La orden %(o)s pasó de %(a)s a %(n)s (%(q)s). Motivo: %(m)s") % {
            "o": ver.name, "a": anterior.name, "n": nueva.name, "q": quien, "m": motivo}
        ver._shrimp_ver_notify(anteriores, _("Orden %s reasignada") % ver.name, _(
            "La orden %(o)s ya no está a tu cargo: se reasignó a otra verificadora. Motivo: "
            "%(m)s") % {"o": ver.name, "m": motivo})
        ver._notify_verifier_assigned()
        tx._shrimp_notify("buyer", _("Nueva verificadora para %s") % tx.name, texto)
        tx._shrimp_notify("seller", _("Nueva verificadora para %s") % tx.name, texto)
        tx.message_post(body=Markup("<p>%s</p>") % texto)
        return True

    def shrimp_fee_for(self, verifier):
        """Honorario que costaría esta orden con `verifier` (su tarifa)."""
        self.ensure_one()
        return self.env["shrimp.verification.fee"].sudo().compute(
            self.transaction_id.transaction_qty, verifier)

    def _shrimp_refee_for_verifier(self):
        """Recalcula el honorario con la tarifa de la verificadora ACTUAL. Si
        cambia, el cobro vigente se anula (o se acredita si ya estaba
        facturado) y se registra uno nuevo por el importe nuevo."""
        for ver in self.sudo():
            if ver.verification_mode != "platform" or ver.is_final:
                continue
            nuevo = ver.shrimp_fee_for(ver.verifier_partner_id)
            if round(nuevo - (ver.fee or 0.0), 2) == 0:
                continue
            anterior = ver.fee or 0.0
            vigente = ver.fee_charge_id
            if vigente:
                vigente.action_cancel_charge(_("Cambio de verificadora: nuevo honorario"))
            ver.with_context(shrimp_fee_from_tariff=True).write({"fee": nuevo})
            ver.invalidate_recordset(["charge_ids", "fee_charge_id"])
            if vigente:
                ver._register_fee_charge()
            ver.message_post(body=_(
                "Honorario recalculado con la tarifa de %(v)s: %(a).2f → %(n).2f.") % {
                    "v": ver.verifier_partner_id.name, "a": anterior, "n": nuevo})

    def action_open_reassign_wizard(self):
        self.ensure_one()
        self.env["shrimp.transaction"]._shrimp_check_platform_admin()
        return {
            "type": "ir.actions.act_window", "res_model": "shrimp.verification.reassign.wizard",
            "view_mode": "form", "target": "new", "name": _("Reasignar verificadora"),
            "context": {"default_verification_id": self.id},
        }

    def action_open_cancel_purchase_wizard(self):
        self.ensure_one()
        return self.transaction_id.with_context(shrimp_cancel_mode="cancel").action_open_cancel_wizard()


class ShrimpVerificationReassignWizard(models.TransientModel):
    _name = "shrimp.verification.reassign.wizard"
    _description = "Reasignar la verificadora (plataforma)"

    verification_id = fields.Many2one("shrimp.verification", string="Verificación", required=True,
                                      readonly=True)
    current_verifier_id = fields.Many2one(related="verification_id.verifier_partner_id",
                                          string="Verificadora actual")
    verifier_partner_id = fields.Many2one(
        "res.partner", string="Nueva verificadora", required=True,
        domain="[('verifier_is_accredited', '=', True), ('id', '!=', current_verifier_id)]")
    reason = fields.Text(string="Motivo", required=True)

    def action_confirm(self):
        self.ensure_one()
        self.env["shrimp.transaction"]._shrimp_check_platform_admin()
        self.verification_id._shrimp_reassign_verifier(
            self.verifier_partner_id, self.reason, _("la plataforma"))
        return {"type": "ir.actions.act_window_close"}


class ShrimpTransactionVerificationDeadline(models.Model):
    _inherit = "shrimp.transaction"

    # ---- comprador: cancelar sin costo o cambiar de verificadora ----------
    def shrimp_verification_overdue(self):
        self.ensure_one()
        ver = self.sudo().verification_ids[:1]
        if not ver or self.state != "pending_verification" or not ver._shrimp_deadline_applies():
            return False
        return ver.deadline_overdue or False

    def shrimp_buyer_cancel_cause(self):
        causa = super().shrimp_buyer_cancel_cause()
        if not causa and self.shrimp_verification_overdue():
            return "verification_overdue"
        return causa

    def _shrimp_buyer_cancel(self, causa, nota):
        if causa == "verification_overdue":
            return self._shrimp_cancel_apply(
                "buyer", "verification_overdue",
                nota or _("La verificadora no cumplió el plazo."), seller_fault=False)
        return super()._shrimp_buyer_cancel(causa, nota)

    def action_switch_verifier(self, verifier, actor=None):
        """El comprador elige otra verificadora cuando la actual venció."""
        self.ensure_one()
        self._shrimp_check_party("buyer", actor)
        if not self.shrimp_verification_overdue():
            raise ValidationError(_("Solo puedes cambiar de verificadora si la actual no cumplió "
                                    "su plazo."))
        ver = self.sudo().verification_ids[:1]
        motivo = _("la verificadora anterior no cumplió el plazo (%s)") % dict(
            ver._fields["deadline_overdue"].selection).get(ver.deadline_overdue, "")
        return ver._shrimp_reassign_verifier(verifier, motivo, _("lo pidió el comprador"))

    # ---- vendedor: cancelar antes de que la verificadora vaya a campo -------
    def shrimp_seller_cancel_problem(self):
        self.ensure_one()
        if self.state in ("pending_verification", "pending_acceptance"):
            ver = self.sudo().verification_ids[:1]
            if self.state == "pending_acceptance" or not ver:
                return _("El informe ya está emitido: la compra solo se cancela de mutuo acuerdo o "
                         "por la plataforma.")
            if ver.verification_mode == "declared":
                return "" if ver.state == "declared_draft" else _(
                    "El informe declarado ya fue presentado.")
            if ver.state not in ("received", "assigned"):
                return _("La verificadora ya fue a campo: la compra solo se cancela de mutuo "
                         "acuerdo o por la plataforma.")
            despacho = self.sudo().dispatch_id
            if despacho and despacho.state in ("en_ruta", "llegado"):
                return _("El producto ya salió hacia la planta: la compra solo se cancela de "
                         "mutuo acuerdo o por la plataforma.")
            return ""
        return super().shrimp_seller_cancel_problem()

    # ---- efectos en la verificación ----------------------------------------
    def _shrimp_cancel_release_hold(self, motivo, kind):
        res = super()._shrimp_cancel_release_hold(motivo, kind)
        ver = self.sudo().verification_ids[:1]
        if ver:
            # Regla de pago (23-oct-2026): lo que decide es el veredicto.
            # Sin veredicto (aunque el técnico ya estuviera en campo o el
            # informe completo) la verificación se cancela, su honorario se
            # anula en _shrimp_void_charges_for_cancel (que corre DESPUÉS de
            # esto y la ve ya «Cancelada») y no se le liquida nada. Con
            # veredicto nunca se toca su estado: conserva el honorario y se le
            # liquida (si la ronda estaba abierta, se cierra aquí).
            finales = ("approved", "approved_obs", "rejected", "cancelled", "declared")
            if ver.state not in finales:
                ver.write({"state": "cancelled"})
                try:
                    ver._notify_stage()
                except Exception:  # noqa: BLE001
                    _logger.warning("Aviso de cancelación de %s", ver.name, exc_info=True)
                if ver.verification_mode == "platform" and ver.verifier_partner_id \
                        and (ver.fee or 0.0) > 0:
                    ver._message_log(body=_(
                        "La verificadora no emitió su veredicto: no se le liquida ningún valor "
                        "y el honorario cobrado se anula."))
            elif ver.acceptance_state == "waiting":
                ver.write({"acceptance_state": "broken"})
            if ver._shrimp_trabajo_hecho():
                ver._shrimp_payout_request()
            try:
                ver.activity_unlink(["mail.mail_activity_data_todo"])
            except Exception:  # noqa: BLE001
                _logger.debug("Sin actividades que cerrar en %s", ver.name)
            ver._message_log(body=_("Compra cancelada: %s") % motivo)
        self.action_cancel_for_verification()
        return res
