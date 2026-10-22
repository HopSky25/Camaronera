"""Equipo de campo de una verificación: un técnico RESPONSABLE y técnicos de APOYO.

Decisiones del negocio (finales):

* Una verificación tiene UN técnico responsable (``technician_partner_id``, el
  campo de siempre: se conserva por compatibilidad con despacho, reseñas, API,
  correos y PDF) y cero o más técnicos de apoyo (``support_technician_ids``).
* TODOS los técnicos asignados (responsable y apoyo) pueden cargar los datos
  de campo: muestreo, pesos, clasificación, sabor, fotos, notas, iniciar la
  inspección y estampar la llegada a planta.
* SOLO el responsable (o la cuenta de la empresa verificadora) firma el
  veredicto / el informe (``_puede_dictaminar`` no cambia).
* No hay pago por técnico: la plataforma le paga a la empresa verificadora;
  aquí solo queda constancia de quién participó (``participant_partner_ids``,
  informe y PDF de trazabilidad).
* El plazo «asignar técnico» se cumple al fijar el responsable (la orden pasa
  de «Recibida» a «Asignada»): el apoyo solo se puede sumar con un
  responsable ya asignado.
"""
import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class ShrimpVerificationTeam(models.Model):
    _inherit = "shrimp.verification"

    technician_partner_id = fields.Many2one(
        string="Técnico responsable",
        help="Técnico de la empresa que dirige la inspección y FIRMA el informe "
             "(veredicto). Los técnicos de apoyo cargan datos de campo pero no "
             "firman.")
    # Tabla de relación nueva (shrimp_verification_support_tech_rel); ninguna
    # columna nueva en shrimp_verification.
    support_technician_ids = fields.Many2many(
        "res.partner", "shrimp_verification_support_tech_rel",
        "verification_id", "partner_id", string="Técnicos de apoyo", copy=False,
        help="Técnicos activos de la misma empresa que ayudan en campo: cargan "
             "muestreo, fotos, sabor y notas, pero no firman el veredicto.")
    participant_partner_ids = fields.Many2many(
        "res.partner", string="Equipo de campo", compute="_compute_participants",
        help="Responsable + apoyo, en ese orden.")
    support_technician_count = fields.Integer(
        string="N.º de técnicos de apoyo", compute="_compute_participants")

    @api.depends("technician_partner_id", "support_technician_ids")
    def _compute_participants(self):
        for rec in self:
            rec.participant_partner_ids = rec.technician_partner_id | rec.support_technician_ids
            rec.support_technician_count = len(rec.support_technician_ids)

    # ------------------------------------------------------------------
    # Reglas del equipo
    # ------------------------------------------------------------------
    @api.constrains("support_technician_ids", "technician_partner_id", "verifier_partner_id",
                    "verification_mode", "buyer_partner_id", "seller_partner_id")
    def _check_support_technicians(self):
        for rec in self:
            apoyo = rec.support_technician_ids.sudo()
            if not apoyo:
                continue
            if rec.verification_mode == "declared":
                raise ValidationError(_(
                    "Una verificación declarada por las partes no lleva técnicos de la plataforma."))
            if not rec.technician_partner_id:
                raise ValidationError(_(
                    "Primero asigna el técnico responsable; el apoyo se suma después."))
            if rec.technician_partner_id in apoyo:
                raise ValidationError(_(
                    "«%s» ya es el técnico responsable: no puede ser también de apoyo."
                ) % rec.technician_partner_id.name)
            empresa = rec.verifier_partner_id.sudo()
            activos = empresa.field_tech_ids.filtered("active")
            partes = rec.buyer_partner_id | rec.seller_partner_id
            for tec in apoyo:
                if tec == empresa:
                    raise ValidationError(_(
                        "La cuenta de la empresa no se suma como apoyo: ya tiene acceso a "
                        "toda la verificación."))
                if tec in partes or (tec.parent_id and tec.parent_id in partes):
                    raise ValidationError(_(
                        "El técnico no puede ser el comprador ni el vendedor de la compra."))
                if not (tec.shrimp_is_field_tech and tec.parent_id == empresa and tec in activos):
                    raise ValidationError(_(
                        "El técnico «%s» no es un técnico activo de la empresa verificadora «%s»."
                    ) % (tec.name, empresa.name))

    # ------------------------------------------------------------------
    # Quién es quién
    # ------------------------------------------------------------------
    def shrimp_tech_role_of(self, partner):
        """«responsible», «support» o False para `partner` en ESTA verificación."""
        self.ensure_one()
        if not partner:
            return False
        if self.technician_partner_id and partner == self.technician_partner_id:
            return "responsible"
        if partner in self.sudo().support_technician_ids:
            return "support"
        return False

    def shrimp_is_assigned_tech(self, partner):
        """True si `partner` es técnico asignado (responsable o apoyo)."""
        self.ensure_one()
        return bool(self.shrimp_tech_role_of(partner))

    def shrimp_can_load_field(self, partner):
        """Cargar datos de campo: cualquier técnico asignado o la empresa."""
        self.ensure_one()
        return bool(partner) and (self.shrimp_is_assigned_tech(partner)
                                  or partner == self.verifier_partner_id)

    @api.model
    def _shrimp_assigned_tech_domain(self, partner):
        """Dominio «la tengo asignada» (responsable o apoyo)."""
        pid = partner.id if partner else 0
        return ["|", ("technician_partner_id", "=", pid),
                ("support_technician_ids", "in", [pid])]

    # ------------------------------------------------------------------
    # Acciones (las llaman el portal y la API con sudo, después de validar
    # que quien actúa es la empresa / su administrador)
    # ------------------------------------------------------------------
    def _shrimp_check_team_editable(self):
        for rec in self:
            if rec.verification_mode != "platform":
                raise UserError(_("Esta verificación no la hace una verificadora de la plataforma."))
            if rec.is_final or rec.state == "cancelled":
                raise UserError(_("Esta verificación ya está cerrada."))

    def action_add_support_technician(self, technicians):
        """Suma técnicos de apoyo (y les avisa)."""
        self._shrimp_check_verifier_caller()
        self._shrimp_check_team_editable()
        for rec in self:
            if not rec.technician_partner_id:
                raise UserError(_("Primero asigna el técnico responsable; el apoyo se suma después."))
            nuevos = technicians - rec.support_technician_ids - rec.technician_partner_id
            if not nuevos:
                continue
            rec.write({"support_technician_ids": [(4, t.id) for t in nuevos]})
            rec._shrimp_log_team(_("Técnico(s) de apoyo sumados: %s.") % ", ".join(nuevos.mapped("name")))
            rec._notify_support_assigned(nuevos)
        return True

    def action_remove_support_technician(self, technicians):
        """Quita técnicos de apoyo. Lo que ya cargaron se queda en el informe."""
        self._shrimp_check_verifier_caller()
        self._shrimp_check_team_editable()
        for rec in self:
            fuera = technicians & rec.support_technician_ids
            if not fuera:
                continue
            rec.write({"support_technician_ids": [(3, t.id) for t in fuera]})
            rec._shrimp_unschedule_for(fuera)
            rec._shrimp_log_team(_("Técnico(s) de apoyo retirados: %s.") % ", ".join(fuera.mapped("name")))
        return True

    def action_make_responsible(self, technician):
        """Cambia el técnico responsable. El responsable anterior, si es un
        técnico (no la cuenta de la empresa), pasa a apoyo: sigue en el equipo
        y no pierde el acceso a lo que estaba cargando."""
        self._shrimp_check_verifier_caller()
        self._shrimp_check_team_editable()
        for rec in self:
            anterior = rec.technician_partner_id
            if anterior == technician:
                continue
            vals_apoyo = [(3, technician.id)]
            if anterior and anterior != rec.verifier_partner_id and anterior.shrimp_is_field_tech \
                    and anterior.active:
                vals_apoyo.append((4, anterior.id))
            # En UN solo write: la regla no admite que alguien sea responsable
            # y apoyo a la vez, y se comprueba al final de la escritura.
            rec.write({"support_technician_ids": vals_apoyo,
                       "technician_partner_id": technician.id})
            rec.action_assign_technician()
        return True

    # ------------------------------------------------------------------
    # Avisos y bitácora
    # ------------------------------------------------------------------
    def _shrimp_log_team(self, texto):
        self.ensure_one()
        try:
            self.sudo().message_post(body=escape(texto), message_type="comment",
                                     subtype_xmlid="mail.mt_note")
        except Exception:  # noqa: BLE001 - la bitácora no debe tumbar la acción
            _logger.warning("No se pudo registrar el cambio de equipo en %s", self.name)

    def _shrimp_unschedule_for(self, partners):
        usuarios = self.env["res.users"].sudo().search([("partner_id", "in", partners.ids)])
        if not usuarios:
            return
        self.sudo().activity_ids.filtered(lambda a: a.user_id in usuarios).unlink()

    def _notify_support_assigned(self, technicians):
        """Cada técnico de apoyo nuevo recibe su actividad y su correo."""
        self.ensure_one()
        entregados = []
        base = self.get_base_url()
        for tec in technicians:
            usuario = self.env["res.users"].sudo().search([("partner_id", "=", tec.id)], limit=1)
            if usuario:
                try:
                    self.activity_schedule(
                        "mail.mail_activity_data_todo",
                        user_id=usuario.id,
                        summary=_("Apoyo en campo: %s") % (self.product_id.display_name or ""),
                        note=_("Compra %s. Responsable: %s.") % (
                            self.transaction_id.name, self.technician_partner_id.name or ""),
                    )
                except Exception:  # noqa: BLE001
                    pass
            ok = self._send_template(
                "shrimp_verification.mail_template_verification_support_assigned", tec.email,
                ctx={"portal_url": "%s/verifier/verifications/%s" % (base, self.uuid_ref),
                     "destinatario": tec.name})
            entregados.append((tec, ok))
        self._log_notificacion([(p, ok) for p, ok in entregados if p.email])
        # Si ya hay cita en planta, también es la suya.
        if self.dispatch_id and self.dispatch_id.eta:
            self.dispatch_id.sudo()._notify_eta(solo=technicians)

    def shrimp_team_text(self):
        """«Ana (responsable); apoyo: Luis, Marta» para correos y PDF."""
        self.ensure_one()
        if not self.technician_partner_id:
            return ""
        txt = _("%s (responsable)") % self.technician_partner_id.name
        if self.support_technician_ids:
            txt += _("; apoyo: %s") % ", ".join(self.support_technician_ids.mapped("name"))
        return txt
