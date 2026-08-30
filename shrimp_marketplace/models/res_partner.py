from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    facility_ids = fields.One2many(
        "shrimp.partner.facility",
        "partner_id",
        string="Instalaciones",
    )

    pond_ids = fields.One2many(
        "shrimp.partner.pond",
        "partner_id",
        string="Piscinas",
    )

    facility_count = fields.Integer(
        string="N.º de instalaciones",
        compute="_compute_facility_count",
    )

    pond_count = fields.Integer(
        string="N.º de piscinas",
        compute="_compute_pond_count",
    )

    # ---- Reputación (calificaciones como vendedor) ----
    shrimp_review_ids = fields.One2many(
        "shrimp.review", "seller_partner_id", string="Reseñas recibidas")
    shrimp_rating_avg = fields.Float(
        string="Calificación promedio", compute="_compute_shrimp_rating",
        store=True, digits=(3, 2))
    shrimp_rating_count = fields.Integer(
        string="N.º de calificaciones", compute="_compute_shrimp_rating", store=True)

    @api.depends("shrimp_review_ids.rating")
    def _compute_shrimp_rating(self):
        for rec in self:
            reviews = rec.shrimp_review_ids
            rec.shrimp_rating_count = len(reviews)
            rec.shrimp_rating_avg = (
                sum(reviews.mapped("rating")) / len(reviews) if reviews else 0.0)

    @api.depends("facility_ids")
    def _compute_facility_count(self):
        for rec in self:
            rec.facility_count = len(rec.facility_ids)

    @api.depends("pond_ids")
    def _compute_pond_count(self):
        for rec in self:
            rec.pond_count = len(rec.pond_ids)

    def get_valid_product_certificates(self, product):
        product.ensure_one()
        today = fields.Date.context_today(self)

        valid_lines = product.certificate_line_ids.filtered(
            lambda line:
                line.attachment_id
                and (
                    not line.expiry_date
                    or line.expiry_date >= today
                )
        )
        return valid_lines