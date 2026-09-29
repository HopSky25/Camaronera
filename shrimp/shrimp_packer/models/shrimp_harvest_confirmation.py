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

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class ShrimpHarvestConfirmation(models.Model):

    _name = "shrimp.harvest.confirmation"
    _description = "Confirmación de una cosecha fuera de banda"
    # El uuid es lo que viaja en la URL de la firma: el id entero no sale del
    # servidor, igual que en el resto del proyecto.
    _inherit = ["shrimp.uuid.mixin"]
    _order = "commitment_id, ronda desc, role"

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
        actor = actor or self.env.user.partner_id
        if actor != self.partner_id:
            raise AccessError(_(
                "Cada parte firma la suya. Esta corresponde a «%s».")
                % (self.partner_id.name or ""))
        if not self.active:
            raise ValidationError(_(
                "Esta firma es de una ronda ya cerrada."))
        if self.decision != "pending":
            raise ValidationError(_("Esta parte ya firmó; no se puede cambiar."))
        if decision == "rejected" and not motivo:
            raise ValidationError(_(
                "Para no aceptar la cosecha hay que decir por qué: es lo que "
                "separa «no me sirve este camarón» de «no quiero pagar»."))
        self.write({
            "decision": decision,
            "reason": motivo,
            "decided_by_uid": self.env.user.id,
            "decided_at": fields.Datetime.now(),
        })
        self.commitment_id._evaluar_confirmacion()
        return True

    def _archivar(self, motivo):
        """Retira estas firmas de la ronda vigente dejando constancia."""
        return self.write({
            "active": False,
            "archived_at": fields.Datetime.now(),
            "archived_by_uid": self.env.user.id,
            "archive_reason": motivo,
        })

    def action_accept(self, actor=None):
        return self._firmar("accepted", actor=actor)

    def action_reject(self, motivo=None, actor=None):
        return self._firmar("rejected", motivo or self.reason, actor=actor)
