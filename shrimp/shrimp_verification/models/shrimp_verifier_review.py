from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpVerifierReview(models.Model):
    """Calificación que el comprador da al trabajo de verificación.

    Se separa de shrimp.review (que califica al vendedor) porque son cosas
    distintas: una valora el producto y quien lo vendió; esta valora la
    inspección y a quien la hizo.
    """

    _name = "shrimp.verifier.review"
    _inherit = "shrimp.uuid.mixin"
    _description = "Reseña del verificador"
    _order = "create_date desc"
    _rec_name = "verifier_partner_id"

    verification_id = fields.Many2one(
        "shrimp.verification", string="Verificación",
        required=True, ondelete="cascade", index=True)

    verifier_partner_id = fields.Many2one(
        "res.partner", string="Empresa verificadora",
        related="verification_id.verifier_partner_id", store=True, index=True)

    technician_partner_id = fields.Many2one(
        "res.partner", string="Técnico de campo",
        related="verification_id.technician_partner_id", store=True, index=True)

    reviewer_partner_id = fields.Many2one(
        "res.partner", string="Comprador", required=True,
        ondelete="cascade", index=True)

    rating = fields.Integer(
        string="Calificación", required=True, default=5,
        help="Puntuación de 1 a 5 estrellas.")

    comment = fields.Text(string="Comentario")

    # Aspectos concretos del trabajo, para que la nota diga algo más que un número
    punctuality = fields.Integer(string="Puntualidad", default=0)
    thoroughness = fields.Integer(string="Rigor del informe", default=0)
    communication = fields.Integer(string="Comunicación", default=0)

    @api.constrains("rating", "punctuality", "thoroughness", "communication")
    def _check_rating(self):
        for rec in self:
            if not (1 <= rec.rating <= 5):
                raise ValidationError(_("La calificación debe estar entre 1 y 5 estrellas."))
            for valor, etiqueta in (
                (rec.punctuality, _("puntualidad")),
                (rec.thoroughness, _("rigor del informe")),
                (rec.communication, _("comunicación")),
            ):
                if valor and not (1 <= valor <= 5):
                    raise ValidationError(
                        _("La valoración de %s debe estar entre 1 y 5.") % etiqueta)

    @api.constrains("reviewer_partner_id", "verification_id")
    def _check_reviewer_is_buyer(self):
        for rec in self:
            comprador = rec.verification_id.buyer_partner_id
            if comprador and rec.reviewer_partner_id != comprador:
                raise ValidationError(_(
                    "Solo el comprador de la operación puede calificar la verificación."))

    _uniq_review_per_verification = models.Constraint(
        "unique(verification_id)",
        "Ya calificaste esta verificación.",
    )
