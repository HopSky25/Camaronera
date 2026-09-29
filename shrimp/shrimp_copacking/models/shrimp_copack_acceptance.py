from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class ShrimpCopackAcceptance(models.Model):
    """La firma de una de las partes sobre el acta de empaque.

    Hay exactamente dos por orden: la del cliente y la del maquilador. El acta
    queda cerrada cuando las dos aceptan, y en disputa en cuanto una rechaza.

    Se guarda como registro y no como dos casillas en la orden porque hace
    falta el porque, la hora y quien lo hizo: eso es la prueba de a que se
    comprometio cada uno con las libras que declaro.
    """

    _name = "shrimp.copack.acceptance"
    _description = "Firma del acta de empaque"
    _order = "order_id, role"

    order_id = fields.Many2one(
        "shrimp.copack.order", string="Orden", required=True,
        ondelete="cascade", index=True)
    role = fields.Selection(
        [("client", "Cliente"), ("copacker", "Maquilador")],
        string="Parte", required=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Quien firma", required=True,
        ondelete="restrict", index=True)

    decision = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("accepted", "Conforme"),
            ("rejected", "No conforme"),
        ],
        string="Decisión", default="pending", required=True, index=True)
    reason = fields.Text(string="Motivo")
    decided_by_uid = fields.Many2one("res.users", string="Firmado por", readonly=True)
    decided_at = fields.Datetime(string="Fecha de firma", readonly=True)

    _uniq_firma = models.Constraint(
        "UNIQUE(order_id, role)",
        "Cada parte firma una sola vez por orden.")

    def _firmar(self, decision, motivo=None, actor=None):
        """`actor` es quien dice firmar, y hay que comprobarlo SIEMPRE.

        El controlador resuelve el acta en sudo —lo necesita para leer las dos
        filas— y sin este control quedaba en manos del ACL que una parte no
        firmara por la otra. Un acta que el maquilador puede firmar en nombre
        del cliente no es un acta: es justo lo que este registro existe para
        impedir.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.partner_id:
            raise AccessError(_(
                "Cada parte firma la suya. Esta corresponde a «%s».")
                % (self.partner_id.name or ""))
        if self.decision != "pending":
            raise ValidationError(_("Esta parte ya firmó; no se puede cambiar."))
        if decision == "rejected" and not motivo:
            raise ValidationError(_(
                "Para no dar conformidad hay que decir por qué: es lo que abre "
                "la discusión con la otra parte."))
        self.write({
            "decision": decision,
            "reason": motivo,
            "decided_by_uid": self.env.user.id,
            "decided_at": fields.Datetime.now(),
        })
        self.order_id._evaluar_acta()

    def action_accept(self, actor=None):
        self._firmar("accepted", actor=actor)

    def action_reject(self, motivo=None, actor=None):
        self._firmar("rejected", motivo or self.reason, actor=actor)
