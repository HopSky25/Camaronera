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

    # ---- Reputación: como vendedor (calificado por compradores) ----
    shrimp_review_ids = fields.One2many(
        "shrimp.review", "seller_partner_id", string="Reseñas recibidas")
    shrimp_rating_avg = fields.Float(
        string="Calificación como vendedor", compute="_compute_shrimp_rating",
        store=True, digits=(3, 2))
    shrimp_rating_count = fields.Integer(
        string="N.º de calificaciones como vendedor", compute="_compute_shrimp_rating", store=True)

    # ---- Reputación: como comprador (calificado por vendedores) ----
    buyer_rating_avg = fields.Float(
        string="Calificación como comprador", compute="_compute_shrimp_rating",
        store=True, digits=(3, 2))
    buyer_rating_count = fields.Integer(
        string="N.º de calificaciones como comprador", compute="_compute_shrimp_rating", store=True)

    # ------------------------------------------------------------------
    # Capacidades del marketplace
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_capability_matrix(self):
        matriz = super()._shrimp_capability_matrix()
        # Instalaciones, piscinas y SIEMBRA de larva/juvenil (portal).
        matriz.setdefault("manage_ponds", {"camaronera"})
        # Salidas / exportaciones: quien tiene camarón adulto o empacado que
        # vende FUERA de la plataforma. La empacadora y la camaronera (su
        # cosecha, o lo que empacó con un maquilador o en planta propia). La
        # misma regla manda en el menú, el botón de «Mi inventario», la ruta y
        # el modelo (shrimp.stock.lot._shrimp_export_block_reason).
        matriz.setdefault("register_exports", {"empacadora", "camaronera"})
        return matriz

    def _shrimp_seller_reviews(self, limit=None):
        """Reseñas que el socio recibió COMO VENDEDOR (to_seller). Las que
        recibió como comprador no van en su vitrina ni en su nota de
        vendedor (shrimp_rating_avg ya las excluye)."""
        self.ensure_one()
        return self.env["shrimp.review"].sudo().search(
            [("seller_partner_id", "=", self.id), ("direction", "=", "to_seller")],
            order="create_date desc, id desc", limit=limit)

    @api.depends("shrimp_review_ids.rating", "shrimp_review_ids.direction")
    def _compute_shrimp_rating(self):
        for rec in self:
            seller_reviews = rec.shrimp_review_ids.filtered(
                lambda r: r.direction == "to_seller")
            buyer_reviews = rec.shrimp_review_ids.filtered(
                lambda r: r.direction == "to_buyer")
            rec.shrimp_rating_count = len(seller_reviews)
            rec.shrimp_rating_avg = (
                sum(seller_reviews.mapped("rating")) / len(seller_reviews)
                if seller_reviews else 0.0)
            rec.buyer_rating_count = len(buyer_reviews)
            rec.buyer_rating_avg = (
                sum(buyer_reviews.mapped("rating")) / len(buyer_reviews)
                if buyer_reviews else 0.0)

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