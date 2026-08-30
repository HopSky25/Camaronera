from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpReview(models.Model):
    _name = "shrimp.review"
    _inherit = "shrimp.uuid.mixin"
    _description = "Reseña / calificación de vendedor"
    _order = "create_date desc"
    _rec_name = "seller_partner_id"

    seller_partner_id = fields.Many2one(
        "res.partner", string="Vendedor", required=True, ondelete="cascade", index=True)
    reviewer_partner_id = fields.Many2one(
        "res.partner", string="Comprador", required=True, ondelete="cascade", index=True)
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Transacción", ondelete="set null",
        help="Compra que da origen a la reseña (si aplica).")
    rating = fields.Integer(string="Calificación", required=True, default=5,
                            help="Puntuación de 1 a 5 estrellas.")
    comment = fields.Text(string="Comentario")

    @api.constrains("rating")
    def _check_rating(self):
        for rec in self:
            if not (1 <= rec.rating <= 5):
                raise ValidationError(_("La calificación debe estar entre 1 y 5 estrellas."))

    @api.constrains("seller_partner_id", "reviewer_partner_id")
    def _check_not_self(self):
        for rec in self:
            if rec.seller_partner_id == rec.reviewer_partner_id:
                raise ValidationError(_("Un vendedor no puede calificarse a sí mismo."))

    _sql_constraints = [
        # Una reseña por comprador y transacción (evita duplicados en la misma compra)
        ("uniq_reviewer_tx", "unique(reviewer_partner_id, transaction_id)",
         "Ya dejaste una reseña para esta transacción."),
    ]
