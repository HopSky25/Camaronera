"""La reserva cumplida queda RESERVADA (auditoría de procesos, hueco #8 / CR-10).

Antes, cuando la cosecha de una reserva aceptada salía dentro de la banda, el
lote nacía publicado para cualquier comprador y sin plazo de compra: los
avisos de lotes nuevos se lo mandaban a las empacadoras competidoras (161
avisos de 48 lotes en Pruebas) y cualquiera podía comprarlo antes que la
empacadora comprometida. Y si la camaronera no registraba la cosecha de un
compromiso ACEPTADO, el cron lo dejaba «sin efecto»: romper así salía gratis.

Reglas:

1. Cuando el compromiso queda CUMPLIDO (cosecha dentro de banda, o fuera de
   banda con las dos firmas), el lote queda reservado en exclusiva para la
   empacadora comprometida durante `reserva_purchase_days` días (Ajustes):
   otras empacadoras no lo ven en el catálogo, no reciben aviso y no pueden
   comprarlo (`motivo_no_comprable`). Se avisa a las dos partes.
2. La reserva termina cuando la empacadora compra las libras liquidadas
   («Comprada»; el sobrante por encima de lo liquidado queda libre).
3. Si el plazo vence sin compra, la reserva vence: el compromiso pasa a
   «Incumplido» por la empacadora (cuenta en su fiabilidad de reservas), el
   lote queda libre para todos, vuelve a entrar en los avisos y se avisa
   expresamente a las empacadoras que se habían quedado fuera de esa cosecha.
   Si llegó a comprar una parte, vence sin incumplimiento.
4. Si la declaración vence con un compromiso ACEPTADO y sin cosecha
   registrada, el compromiso pasa a «Incumplido» por la camaronera, con aviso
   a las dos partes. La plataforma puede anular el incumplimiento con una
   justificación (causa justificada) desde el back-office.
5. Cuando un compromiso aceptado se rompe y la declaración vuelve a
   «publicada», las empacadoras descartadas al aceptarlo vuelven a poder
   comprometerse (sus compromisos vigentes vuelven a «enviado»; AL-23).

Los incumplimientos usan el mismo registro que ya existía (`broken` con
`break_side` y `broken_by_partner_id`), que es lo que mide la «Fiabilidad en
reservas» del socio. No se usan los strikes del vendedor: miden entregas y
suspenden la publicación, otra cosa.
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .shrimp_product import LB_POR_KG

_logger = logging.getLogger(__name__)

RESERVA_DIAS_PARAM = "shrimp_packer.reserva_purchase_days"
RESERVA_DIAS_DEFAULT = 3
_TX_ACTIVA = ("cancel",)


def _fmt_dt(rec, dt):
    """Fecha y hora en la zona del usuario (o Guayaquil) para los avisos."""
    if not dt:
        return ""
    local = fields.Datetime.context_timestamp(
        rec.with_context(tz=rec.env.user.tz or "America/Guayaquil"), dt)
    return local.strftime("%d/%m/%Y %H:%M")


class ShrimpHarvestCommitmentReserva(models.Model):
    _inherit = "shrimp.harvest.commitment"

    reservation_state = fields.Selection(
        [("reserved", "Reservado a la empacadora"),
         ("bought", "Comprado"),
         ("lapsed", "Plazo de compra vencido")],
        string="Reserva del lote", readonly=True, copy=False, index=True, tracking=True,
        help="Qué pasó con el lote reservado para la empacadora tras cumplirse el compromiso.")
    purchase_deadline = fields.Datetime(
        string="Comprar antes de", readonly=True, copy=False, tracking=True,
        help="Hasta cuándo el lote está reservado en exclusiva a la empacadora comprometida.")

    # ------------------------------------------------------------------
    # Ajustes
    # ------------------------------------------------------------------
    @api.model
    def _reserva_dias(self):
        return self.env["shrimp.settings"].get_int(
            RESERVA_DIAS_PARAM, RESERVA_DIAS_DEFAULT, minimum=1, maximum=60)

    # ------------------------------------------------------------------
    # Cuánto falta por comprar
    # ------------------------------------------------------------------
    def _es_de_la_empacadora(self, partner):
        """¿`partner` compra por la empacadora comprometida (misma cuenta o grupo)?"""
        self.ensure_one()
        if not partner:
            return False
        packer = self.packer_partner_id
        return bool(
            partner == packer
            or packer.id in partner.shrimp_grupo_ids()
            or partner.id in packer.shrimp_grupo_ids()
            or partner.commercial_partner_id == packer.commercial_partner_id)

    def _reserva_libras_compradas(self):
        """Libras que la empacadora ya compró del lote (compras no canceladas)."""
        self.ensure_one()
        lote = self.product_id.sudo()
        if not lote:
            return 0.0
        compras = self.env["shrimp.transaction"].sudo().search([
            ("product_id", "=", lote.id), ("state", "not in", _TX_ACTIVA)])
        compras = compras.filtered(lambda t: self._es_de_la_empacadora(t.buyer_partner_id))
        qty = sum(compras.mapped("transaction_qty"))
        if lote._clave_uom() == "kg":
            qty *= LB_POR_KG
        return qty

    def _reserva_pendiente_lb(self):
        self.ensure_one()
        return max((self.settled_lb or 0.0) - self._reserva_libras_compradas(), 0.0)

    def _reserva_vigente(self, ahora=None):
        """True si el lote sigue reservado en exclusiva para la empacadora."""
        self.ensure_one()
        ahora = ahora or fields.Datetime.now()
        return bool(
            self.state == "honored"
            and self.reservation_state == "reserved"
            and self.purchase_deadline and self.purchase_deadline > ahora
            and self.product_id
            and float_compare(self._reserva_pendiente_lb(), 0.0, precision_digits=2) == 1)

    # ------------------------------------------------------------------
    # Avisos
    # ------------------------------------------------------------------
    def _reserva_destinatarios(self, partners, role, permiso):
        Tx = self.env["shrimp.transaction"]
        dest = self.env["res.partner"]
        for partner in partners:
            usuarios = Tx._shrimp_party_users(partner, role, permiso)
            dest |= usuarios.mapped("partner_id").filtered("email") or partner.filtered("email")
        return dest

    def _reserva_avisar(self, subject, partes, packer=True, farmer=True, otros=None):
        """Mensaje en el compromiso con correo a las partes indicadas."""
        self.ensure_one()
        dest = self.env["res.partner"]
        if packer:
            dest |= self._reserva_destinatarios(self.packer_partner_id, "empacadora", "buy")
        if farmer:
            dest |= self._reserva_destinatarios(self.farmer_partner_id, "camaronera", "sell")
        if otros:
            dest |= self._reserva_destinatarios(otros, "empacadora", "buy")
        try:
            self.sudo().with_context(mail_notify_force_send=False).message_post(
                body=Markup("").join(Markup("<p>%s</p>") % p for p in partes),
                subject=subject, partner_ids=dest.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 - un aviso nunca tumba el proceso
            _logger.warning("No se pudo avisar la reserva %s", self.id, exc_info=True)

    # ------------------------------------------------------------------
    # Inicio de la reserva
    # ------------------------------------------------------------------
    def _iniciar_reserva(self, desde=None, avisar=True):
        """Reserva el lote del compromiso cumplido para la empacadora."""
        for rec in self:
            if rec.state != "honored" or not rec.product_id or rec.reservation_state:
                continue
            desde = desde or fields.Datetime.now()
            dias = rec._reserva_dias()
            rec.sudo().write({
                "reservation_state": "reserved",
                "purchase_deadline": desde + timedelta(days=dias),
            })
            if not avisar:
                continue
            lote = rec.product_id.sudo()
            rec._reserva_avisar(
                _("Lote reservado: %s") % (lote.name or ""),
                [_("La cosecha %(ref)s cumplió el compromiso. El lote «%(lote)s» queda reservado "
                   "en exclusiva para %(emp)s hasta el %(hasta)s (%(dias)s días): ninguna otra "
                   "empacadora lo ve ni puede comprarlo.") % {
                       "ref": rec.forecast_id.name or "", "lote": lote.name or "",
                       "emp": rec.packer_partner_id.name or "",
                       "hasta": _fmt_dt(rec, rec.purchase_deadline), "dias": dias},
                 _("Libras liquidadas: %(lb)s lb a %(p)s $/lb. La empacadora las compra desde "
                   "«Reservas» › el compromiso › «Comprar».") % {
                       "lb": "{:,.2f}".format(rec.settled_lb or 0.0),
                       "p": "{:,.4f}".format(rec.settled_price_per_lb or 0.0)},
                 _("Si no compra dentro del plazo, la reserva vence, cuenta como incumplimiento de "
                   "la empacadora y el lote se ofrece a las demás empacadoras.")])
        return True

    # ------------------------------------------------------------------
    # Fin de la reserva
    # ------------------------------------------------------------------
    def _reserva_marcar_comprada(self):
        for rec in self.filtered(lambda c: c.reservation_state == "reserved"):
            rec.sudo().reservation_state = "bought"
        return True

    def _reserva_vencer(self):
        """El plazo venció sin la compra de las libras liquidadas."""
        for rec in self.filtered(lambda c: c.reservation_state == "reserved"):
            compradas = rec._reserva_libras_compradas()
            lote = rec.product_id.sudo()
            rec.sudo().reservation_state = "lapsed"
            if rec.state == "honored" and float_compare(compradas, 0.0, precision_digits=2) <= 0:
                rec.sudo()._marcar_incumplido(
                    lado="packer", partner=rec.packer_partner_id,
                    motivo=_("No compró el lote reservado dentro del plazo (vencía el %s).")
                    % _fmt_dt(rec, rec.purchase_deadline))
                culpa = _("Cuenta como incumplimiento de %s en su fiabilidad de reservas.") % (
                    rec.packer_partner_id.name or "")
            else:
                culpa = _("Compró %s lb de las liquidadas: vence sin incumplimiento.") % (
                    "{:,.2f}".format(compradas))
            # Vuelve a ofrecerse: entra de nuevo en los avisos de lotes nuevos.
            if lote and lote.active and lote.state == "published":
                lote.with_context(shrimp_publish_checked=True).write(
                    {"published_date": fields.Datetime.now()})
            otros = rec._reserva_descartadas()
            rec._reserva_avisar(
                _("Venció la reserva del lote %s") % (lote.name or ""),
                [_("La empacadora %(emp)s no compró el lote «%(lote)s» dentro del plazo "
                   "(vencía el %(hasta)s).") % {
                       "emp": rec.packer_partner_id.name or "", "lote": lote.name or "",
                       "hasta": _fmt_dt(rec, rec.purchase_deadline)},
                 culpa,
                 _("El lote queda libre: lo pueden comprar las demás empacadoras desde el "
                   "marketplace (%(n)s lb disponibles).") % {
                       "n": "{:,.2f}".format(lote.available_qty or 0.0) if lote else "0"}],
                otros=otros)
        return True

    def _reserva_descartadas(self):
        """Empacadoras que se quedaron fuera de esa cosecha (para ofrecerles el lote)."""
        self.ensure_one()
        return (self.forecast_id.commitment_ids - self).filtered(
            lambda c: c.state in ("rejected", "lapsed")).mapped("packer_partner_id")

    @api.model
    def _cron_reserva_plazo(self):
        """Cierra las reservas compradas y vence las que pasaron de plazo."""
        ahora = fields.Datetime.now()
        reservas = self.sudo().search([("reservation_state", "=", "reserved")])
        vencidas = 0
        for rec in reservas:
            try:
                with self.env.cr.savepoint():
                    if rec.state != "honored" or not rec.product_id:
                        rec.reservation_state = "lapsed"
                    elif float_compare(rec._reserva_pendiente_lb(), 0.0, precision_digits=2) <= 0:
                        rec._reserva_marcar_comprada()
                    elif rec.purchase_deadline and rec.purchase_deadline <= ahora:
                        rec._reserva_vencer()
                        vencidas += 1
            except Exception:  # noqa: BLE001 - el cron sigue con la siguiente
                _logger.exception("Reservas: falló el cierre de la reserva %s", rec.id)
        return vencidas

    # ------------------------------------------------------------------
    # Ganchos sobre el flujo existente
    # ------------------------------------------------------------------
    def _evaluar_confirmacion(self):
        res = super()._evaluar_confirmacion()
        if self.state == "honored" and self.product_id and not self.reservation_state:
            self._iniciar_reserva()
        return res

    def action_comprar(self, actor=None, verifier=None, fee=None, mode=None):
        self.ensure_one()
        if self.reservation_state == "reserved" and self.purchase_deadline \
                and self.purchase_deadline <= fields.Datetime.now():
            self._reserva_vencer()
            raise ValidationError(_(
                "El plazo para comprar este lote reservado venció el %s.")
                % _fmt_dt(self, self.purchase_deadline))
        tx = super().action_comprar(actor=actor, verifier=verifier, fee=fee, mode=mode)
        if self.reservation_state == "reserved" and float_compare(
                self._reserva_pendiente_lb(), 0.0, precision_digits=2) <= 0:
            self._reserva_marcar_comprada()
        return tx

    def _marcar_incumplido(self, lado, partner, motivo):
        republicar = self.forecast_id.state == "committed"
        res = super()._marcar_incumplido(lado, partner, motivo)
        if self.reservation_state == "reserved":
            self.sudo().reservation_state = "lapsed"
        if republicar and self.forecast_id.state == "published" \
                and not self.env.context.get("shrimp_no_reabrir"):
            self._reabrir_descartadas()
        return res

    def _reabrir_descartadas(self):
        """Compromiso aceptado roto y declaración otra vez publicada: las
        empacadoras descartadas al aceptarlo vuelven a estar sobre la mesa
        (AL-23). Solo las que siguen vigentes; se les avisa."""
        self.ensure_one()
        declaracion = self.forecast_id
        if not declaracion.admite_compromisos:
            return False
        hoy = fields.Date.context_today(self)
        reabrir = (declaracion.commitment_ids - self).filtered(
            lambda c: c.state == "rejected" and c.valid_until and c.valid_until >= hoy)
        if not reabrir:
            return False
        reabrir.sudo().write({"state": "sent"})
        for c in reabrir:
            c._reserva_avisar(
                _("La cosecha %s vuelve a estar disponible") % (declaracion.name or ""),
                [_("El compromiso que se había aceptado para la cosecha %(ref)s se rompió. Tu "
                   "compromiso (%(lb)s lb) vuelve a estar sobre la mesa: la camaronera puede "
                   "aceptarlo hasta el %(hasta)s. Si ya no te sirve, retíralo.") % {
                       "ref": declaracion.name or "",
                       "lb": "{:,.2f}".format(c.committed_lb or 0.0),
                       "hasta": fields.Date.to_string(c.valid_until)}],
                packer=True, farmer=False)
        return True

    # ------------------------------------------------------------------
    # Plataforma: anular un incumplimiento con causa justificada
    # ------------------------------------------------------------------
    def action_anular_incumplimiento(self, justificacion=None):
        if not (self.env.su or self.env.user.has_group(
                "shrimp_marketplace.group_marketplace_manager")):
            raise AccessError(_("Solo un administrador de CamaronMarket anula incumplimientos."))
        texto = (justificacion or "").strip()
        if not texto:
            raise UserError(_("Escribe la causa justificada."))
        for rec in self:
            if rec.state != "broken":
                raise UserError(_("Solo se anula un compromiso incumplido."))
            quien = rec.broken_by_partner_id.name or ""
            rec.sudo().write({"state": "lapsed"})
            rec._reserva_avisar(
                _("Incumplimiento anulado: %s") % (rec.forecast_id.name or ""),
                [_("La plataforma anuló el incumplimiento de %(quien)s en la cosecha %(ref)s por "
                   "causa justificada: %(j)s") % {
                       "quien": quien, "ref": rec.forecast_id.name or "", "j": texto},
                 _("El compromiso queda «sin efecto» y no cuenta en la fiabilidad de reservas.")])
        return True

    def action_open_anular_incumplimiento(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Anular incumplimiento"),
            "res_model": "shrimp.harvest.breach.annul",
            "view_mode": "form",
            "target": "new",
            "context": {"default_commitment_id": self.id},
        }


class ShrimpHarvestBreachAnnul(models.TransientModel):
    _name = "shrimp.harvest.breach.annul"
    _description = "Anular incumplimiento de una reserva de cosecha"

    commitment_id = fields.Many2one(
        "shrimp.harvest.commitment", string="Compromiso", required=True, readonly=True)
    justification = fields.Text(string="Causa justificada", required=True)

    def action_confirm(self):
        self.ensure_one()
        self.commitment_id.action_anular_incumplimiento(self.justification)
        return {"type": "ir.actions.act_window_close"}


class ShrimpHarvestForecastReserva(models.Model):
    _inherit = "shrimp.harvest.forecast"

    def action_cancel(self, motivo=None, actor=None):
        # La declaración se retira: no se reabren las descartadas (no hay
        # cosecha a la que volver a comprometerse).
        return super(ShrimpHarvestForecastReserva, self.with_context(
            shrimp_no_reabrir=True)).action_cancel(motivo=motivo, actor=actor)

    def action_registrar_cosecha(self, actual_lb, actual_size_grade_id,
                                 actual_date=None, actor=None):
        lote = super().action_registrar_cosecha(
            actual_lb, actual_size_grade_id, actual_date=actual_date, actor=actor)
        cumplido = self.commitment_ids.filtered(
            lambda c: c.state == "honored" and c.product_id == lote)
        cumplido._iniciar_reserva()
        return lote

    @api.model
    def _cron_vencer(self):
        """La camaronera no registró la cosecha de un compromiso ACEPTADO.

        Antes el compromiso pasaba a «sin efecto» sin culpa de nadie, y romper
        así salía gratis (la camaronera vendía por fuera). Ahora es un
        incumplimiento de la camaronera, con aviso a las dos partes; la
        plataforma lo puede anular si hubo causa justificada."""
        hoy = fields.Date.context_today(self)
        vencidas = self.search([("state", "=", "committed")]).filtered(
            lambda f: f.expected_date and f._limite_para_comprometerse() < hoy)
        for declaracion in vencidas:
            for compromiso in declaracion.commitment_ids.filtered(lambda c: c.state == "accepted"):
                motivo = _("No registró la cosecha %(ref)s (fecha estimada %(f)s más %(t)s días de "
                           "tolerancia).") % {
                    "ref": declaracion.name or "",
                    "f": fields.Date.to_string(declaracion.expected_date),
                    "t": declaracion.date_tolerance_days or 0}
                compromiso.with_context(shrimp_no_reabrir=True)._marcar_incumplido(
                    lado="farmer", partner=declaracion.farmer_partner_id, motivo=motivo)
                compromiso._reserva_avisar(
                    _("Cosecha %s sin registrar: compromiso incumplido") % (declaracion.name or ""),
                    [_("La camaronera %(cam)s no registró la cosecha %(ref)s comprometida con "
                       "%(emp)s dentro del plazo.") % {
                           "cam": declaracion.farmer_partner_id.name or "",
                           "ref": declaracion.name or "",
                           "emp": compromiso.packer_partner_id.name or ""},
                     _("Cuenta como incumplimiento de la camaronera en su fiabilidad de reservas. "
                       "Si hubo una causa justificada, la plataforma puede anularlo: escríbenos.")])
        return super()._cron_vencer()


class ShrimpProductReserva(models.Model):
    _inherit = "shrimp.product"

    shrimp_reservation_commitment_id = fields.Many2one(
        "shrimp.harvest.commitment", string="Reserva vigente",
        compute="_compute_shrimp_reservation")
    shrimp_reserved_for_id = fields.Many2one(
        "res.partner", string="Reservado para", compute="_compute_shrimp_reservation")
    shrimp_reserved_until = fields.Datetime(
        string="Reservado hasta", compute="_compute_shrimp_reservation")

    def _compute_shrimp_reservation(self):
        Commitment = self.env["shrimp.harvest.commitment"].sudo()
        ids = [i for i in self.ids if isinstance(i, int)]
        por_lote = {}
        if ids:
            ahora = fields.Datetime.now()
            for c in Commitment.search([
                    ("product_id", "in", ids), ("state", "=", "honored"),
                    ("reservation_state", "=", "reserved"), ("purchase_deadline", ">", ahora)]):
                if c._reserva_vigente(ahora):
                    por_lote[c.product_id.id] = c
        for rec in self:
            c = por_lote.get(rec.id)
            rec.shrimp_reservation_commitment_id = c.id if c else False
            rec.shrimp_reserved_for_id = c.packer_partner_id.id if c else False
            rec.shrimp_reserved_until = c.purchase_deadline if c else False

    def motivo_no_comprable(self, partner, role=None):
        motivo = super().motivo_no_comprable(partner, role=role)
        if motivo or not partner:
            return motivo
        reserva = self.sudo().shrimp_reservation_commitment_id
        if reserva and not reserva._es_de_la_empacadora(partner):
            return _("Lote reservado para la empacadora que se comprometió con esta cosecha, "
                     "hasta el %s.") % _fmt_dt(self, reserva.purchase_deadline)
        return ""

    def _shrimp_retire_blockers(self):
        motivos = super()._shrimp_retire_blockers()
        if self.sudo().shrimp_reservation_commitment_id:
            motivos.append(_("una reserva de cosecha vigente para %s")
                           % (self.sudo().shrimp_reserved_for_id.name or ""))
        return motivos

    def _shrimp_profile_change_blockers(self):
        motivos = super()._shrimp_profile_change_blockers()
        if self.sudo().shrimp_reservation_commitment_id:
            motivos.append(_("tiene una reserva de cosecha vigente"))
        return motivos


class ShrimpLotAlertReserva(models.Model):
    _inherit = "shrimp.lot.alert"

    @api.model
    def _digest_para(self, packer, lotes, listas_por_vendedor, competencia_por_lote,
                     ahora, hoy):
        """Un lote reservado no se avisa a las empacadoras competidoras."""
        lotes = lotes.filtered(
            lambda l: not l.shrimp_reservation_commitment_id
            or l.shrimp_reservation_commitment_id._es_de_la_empacadora(packer))
        return super()._digest_para(packer, lotes, listas_por_vendedor,
                                    competencia_por_lote, ahora, hoy)
