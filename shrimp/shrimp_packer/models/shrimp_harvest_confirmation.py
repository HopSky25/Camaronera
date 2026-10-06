"""La firma de cada parte cuando la cosecha se salió de la banda.

Hay exactamente dos por compromiso y por ronda: la de la camaronera y la de la
empacadora. El compromiso se cumple cuando las dos aceptan la cosecha que
realmente salió, y queda liberado —sin incumplimiento— en cuanto una no la
acepta.

Se guarda como registro y no como dos casillas en el compromiso porque hace
falta el porqué, la hora y quién lo hizo. Es la diferencia entre "no se
cumplió" y "la cosecha salió cuatro tallas más chica y la empacadora dijo que
así no le servía", y esa diferencia es la que se lee el día que alguien decide
si vuelve a reservar con esta contraparte.

Dos cosas que este modelo sostiene, copiadas del acta de empaque porque el
problema es idéntico:

* Las firmas de una ronda que se cierra NO se borran: se archivan
  (`active = False`) con quién, cuándo y por qué. Borrarlas dejaría la
  negociación sin rastro, que es justo el papel que se guarda para el día que
  alguien reclame.
* Cada firma congela las cifras sobre las que se firmó. Sin eso, cualquier
  recálculo posterior dejaría un "conforme" puesto sobre unos números que nadie
  aceptó nunca.
"""

from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpHarvestConfirmation(models.Model):

    _name = "shrimp.harvest.confirmation"
    _description = "Confirmación de una cosecha fuera de banda"
    # El uuid es lo que viaja en la URL de la firma: el id entero no sale del
    # servidor, igual que en el resto del proyecto.
    _inherit = ["shrimp.uuid.mixin", "shrimp.signoff.mixin"]
    _SIGNOFF_ARCHIVED_MSG = "Esta firma es de una ronda ya cerrada."
    _SIGNOFF_REASON_REQUIRED = (
        "Para no aceptar la cosecha hay que decir por qué: es lo que "
        "separa «no me sirve este camarón» de «no quiero pagar».")
    _order = "commitment_id, ronda desc, role"
    _SIGNOFF_PARENT_FIELD = "commitment_id"
    _SIGNOFF_SNAPSHOT_FIELDS = ("decision", "reason", "decided_by_uid", "decided_at")

    commitment_id = fields.Many2one(
        "shrimp.harvest.commitment", string="Compromiso", required=True,
        ondelete="cascade", index=True)
    # La ronda va dentro de la clave única porque un índice UNIQUE de Postgres
    # no sabe nada del campo `active`: sin ella, archivar no bastaría para
    # poder abrir una segunda confirmación del mismo rol.
    ronda = fields.Integer(
        string="Ronda", default=1, required=True, index=True, readonly=True)
    active = fields.Boolean(
        string="Vigente", default=True,
        help="Las firmas de rondas anteriores quedan archivadas, no borradas.")
    role = fields.Selection(
        [("farmer", "Camaronera"), ("packer", "Empacadora")],
        string="Parte", required=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Quién firma", required=True,
        ondelete="restrict", index=True)

    decision = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("accepted", "Acepta la cosecha que salió"),
            ("rejected", "No la acepta"),
        ],
        string="Decisión", default="pending", required=True, index=True)
    reason = fields.Text(string="Motivo")
    decided_by_uid = fields.Many2one("res.users", string="Firmado por", readonly=True)
    decided_at = fields.Datetime(string="Fecha de firma", readonly=True)

    # --- foto de las cifras firmadas ---
    frozen_expected_lb = fields.Float(
        string="Libras declaradas", digits=(16, 2), readonly=True)
    frozen_actual_lb = fields.Float(
        string="Libras cosechadas", digits=(16, 2), readonly=True)
    frozen_lb_min = fields.Float(
        string="Piso de la banda", digits=(16, 2), readonly=True)
    frozen_lb_max = fields.Float(
        string="Techo de la banda", digits=(16, 2), readonly=True)
    frozen_settled_lb = fields.Float(
        string="Libras a liquidar", digits=(16, 2), readonly=True)
    frozen_price_per_lb = fields.Float(
        string="Precio liquidado ($/lb)", digits=(16, 4), readonly=True)
    frozen_steps = fields.Integer(
        string="Escalones de talla", readonly=True)
    frozen_days = fields.Integer(string="Días de desvío", readonly=True)
    frozen_reason = fields.Text(
        string="Qué se salió de la banda", readonly=True)

    # --- traza del archivado ---
    archived_at = fields.Datetime(string="Archivada el", readonly=True)
    archived_by_uid = fields.Many2one(
        "res.users", string="Archivada por", readonly=True)
    archive_reason = fields.Text(string="Motivo del archivado", readonly=True)

    _uniq_firma = models.Constraint(
        "UNIQUE(commitment_id, role, ronda)",
        "Cada parte firma una sola vez en cada ronda.")

    @api.constrains("ronda")
    def _check_ronda(self):
        for rec in self:
            if rec.ronda < 1:
                raise ValidationError(_("La ronda empieza en 1."))

    def _firmar(self, decision, motivo=None, actor=None):
        """`actor` es quien dice firmar, y se comprueba SIEMPRE.

        El controlador resuelve la confirmación en sudo —lo necesita para leer
        las dos filas— así que sin esta comprobación quedaría en manos del ACL
        que una parte no firme por la otra. Una confirmación que la empacadora
        puede firmar en nombre de la camaronera no es una confirmación: es
        justo lo que este registro existe para impedir.
        """
        self.ensure_one()
        # Mismas reglas que las otras firmas de dos partes (shrimp.signoff.mixin).
        self._signoff_check_actor(actor)
        self._signoff_check_open()
        self._signoff_check_reason(decision, motivo)
        antes = self._signoff_snapshot()
        compromiso = self.commitment_id
        estado_declaracion = compromiso.forecast_id.state
        self.write({
            "decision": decision,
            "reason": motivo,
            "decided_by_uid": self.env.user.id,
            "decided_at": fields.Datetime.now(),
        })
        compromiso._evaluar_confirmacion()
        # Lo que la decisión movió fuera de la postura, para poder devolverlo
        # si se deshace dentro de la ventana de gracia.
        extra = {"commitment_state": compromiso.state}
        if compromiso.forecast_id.state != estado_declaracion:
            extra["forecast_state_before"] = estado_declaracion
            extra["forecast_state_after"] = compromiso.forecast_id.state
        self._signoff_log("decision", antes, reason=motivo, extra=extra)
        return True

    def _archivar(self, motivo):
        """Retira estas firmas de la ronda vigente dejando constancia."""
        return self._signoff_archive(motivo)

    def action_accept(self, actor=None):
        return self._firmar("accepted", actor=actor)

    def action_reject(self, motivo=None, actor=None):
        return self._firmar("rejected", motivo or self.reason, actor=actor)

    # ==================================================================
    # Deshacer la firma (shrimp.signoff.mixin)
    # ==================================================================
    # Hasta cuándo:
    #
    # * Con el compromiso «pendiente de confirmar» (la otra parte aún no
    #   firmó): siempre. La firma propia todavía no movió nada.
    # * Con dos conformes el compromiso queda CUMPLIDO: el lote se publica con
    #   el precio liquidado y la empacadora puede comprarlo. Eso no se deshace.
    # * Un «no la acepto» libera el compromiso EN EL ACTO —es la regla de
    #   negocio: una parte sola puede soltarse sin incumplir, y no hay plazo
    #   que esperar—. Para poder arreglar un rechazo por error sin quitarle a
    #   la otra parte esa certeza, se deja una ventana de gracia corta
    #   (shrimp.signoff_undo_minutes, 15 min por defecto) en la que quien
    #   rechazó puede deshacerlo, SOLO si nada irreversible pasó todavía: el
    #   lote sigue en borrador (no se publicó ni se vendió) y no hay compra.
    def _signoff_undo_blocker(self):
        bloqueo = super()._signoff_undo_blocker()
        if bloqueo:
            return bloqueo
        compromiso = self.commitment_id
        if compromiso.state == "to_confirm":
            return False
        if compromiso.state == "released" and self.decision == "rejected":
            if not self._signoff_within_grace():
                return _("Pasó la ventana de %s minutos para deshacer el rechazo: el "
                         "compromiso ya quedó liberado.") % self._signoff_grace_minutes()
            lote = compromiso.product_id
            if compromiso.transaction_id or (lote and lote.state != "draft"):
                return _("El lote de esta cosecha ya se publicó o se vendió: el "
                         "rechazo surtió efecto y no se puede deshacer.")
            return False
        return _("La confirmación ya se resolvió (compromiso %s): no se puede deshacer.") % (
            dict(compromiso._fields["state"]._description_selection(self.env)).get(
                compromiso.state, compromiso.state))

    def _signoff_undo_until(self):
        compromiso = self.commitment_id
        if compromiso.state == "released" and self.decided_at:
            limite = self.decided_at + timedelta(minutes=self._signoff_grace_minutes())
            local = fields.Datetime.context_timestamp(self, limite)
            return _("hasta las %s (ventana de gracia tras tu rechazo), si el lote "
                     "sigue sin publicarse") % local.strftime("%H:%M")
        return _("mientras la otra parte no firme")

    def _signoff_after_undo(self, evento):
        """Un rechazo deshecho dentro de la gracia devuelve el compromiso a
        «pendiente de confirmar» (si nadie más lo rechazó)."""
        compromiso = self.commitment_id.sudo()
        vivas = compromiso.confirmation_ids.filtered("active")
        if compromiso.state == "released" and not any(
                c.decision == "rejected" for c in vivas):
            compromiso.state = "to_confirm"
            extra = (evento.extra or {}) if evento else {}
            antes = extra.get("forecast_state_before")
            if antes and compromiso.forecast_id.state == extra.get("forecast_state_after"):
                compromiso.forecast_id.state = antes
            compromiso.message_post(body=_(
                "Se deshizo el rechazo de la cosecha fuera de banda: el compromiso "
                "vuelve a estar pendiente de confirmar."))
        return True

    def _signoff_portal_url(self, partner):
        declaracion = self.commitment_id.forecast_id
        if partner == self.commitment_id.farmer_partner_id:
            return "/marketplace/reservations/%s" % declaracion.uuid_ref
        return "/packer/reservations/%s" % declaracion.uuid_ref
