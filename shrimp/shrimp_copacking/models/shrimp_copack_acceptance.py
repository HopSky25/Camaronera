from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


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

    def _firmar(self, decision, motivo=None):
        self.ensure_one()
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

    def action_accept(self):
        self._firmar("accepted")

    def action_reject(self, motivo=None):
        self._firmar("rejected", motivo or self.reason)
