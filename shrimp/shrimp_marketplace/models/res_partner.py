from odoo import _, api, fields, models


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

    # Hectáreas y piscinas de la camaronera SIN pedirlas al registrarse
    # (19.0.1.22.3): salen de las piscinas registradas (tierra y geomembrana;
    # los tanques no son área de finca) y, si no hay ninguna con medidas, del
    # dato manual (farm_area_ha / shrimp_num_piscinas) si alguien lo llenó.
    shrimp_farm_area_effective_ha = fields.Float(
        string="Área de la finca (ha)", compute="_compute_shrimp_farm_effective",
        digits=(16, 2),
        help="Suma de las piscinas de tierra y geomembrana registradas en "
             "«Mis instalaciones»; si no hay, el área declarada a mano.")
    shrimp_pond_count_effective = fields.Integer(
        string="Piscinas de la finca", compute="_compute_shrimp_farm_effective",
        help="Piscinas de tierra y geomembrana registradas; si no hay, las "
             "declaradas a mano.")
    shrimp_farm_area_source = fields.Selection(
        [("ponds", "Piscinas registradas"), ("manual", "Dato declarado"), ("none", "Sin datos")],
        string="Origen del área", compute="_compute_shrimp_farm_effective")

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
        # Instalaciones y sus piscinas o tanques en «Mi cuenta»
        # (#instalaciones): finca/piscinas de la camaronera, laboratorio/
        # tanques del laboratorio, centro de maduración/tanques del
        # naupliero. Separada de manage_ponds (que es la SIEMBRA, solo de la
        # camaronera): da la entrada «Fincas y piscinas» / «Laboratorios y
        # tanques» / «Centros y tanques» de la barra y de Mi panel.
        matriz.setdefault("manage_facilities", {"camaronera", "laboratorio", "semillero"})
        # Salidas / exportaciones: quien tiene camarón adulto o empacado que
        # vende FUERA de la plataforma. La empacadora y la camaronera (su
        # cosecha, o lo que empacó con un maquilador o en planta propia). La
        # misma regla manda en el menú, el botón de «Mi inventario», la ruta y
        # el modelo (shrimp.stock.lot._shrimp_export_block_reason).
        matriz.setdefault("register_exports", {"empacadora", "camaronera"})
        return matriz

    def _shrimp_profile_switcher_info(self):
        """«Perfil: X ▾» por sitio: cada sitio ofrece solo SUS perfiles (el
        marketplace no ofrece la verificadora ni el maquilador; el sitio de
        verificadores solo la verificadora; el de empaque solo el maquilador).
        Los perfiles de la cuenta que se usan en otro sitio salen como enlace
        a ese sitio, nunca como opción para activar aquí."""
        info = super()._shrimp_profile_switcher_info()
        web = self.env["website"].get_current_website() if info else None
        if not info or not web:
            return info
        web = web.sudo()
        aqui = web._shrimp_role_allowed_here
        fuera = [r for r, _l, _a in info["roles"] if not aqui(r)]
        fuera += [r for r, _l in info["pending"] if not aqui(r) and r not in fuera]
        info["roles"] = [x for x in info["roles"] if aqui(x[0])]
        info["pending"] = [x for x in info["pending"] if aqui(x[0])]
        info["offsite"] = web._shrimp_offsite_links(fuera, "/web/login")
        info["active_offsite"] = not aqui(info["active"])
        if info.get("can_add"):
            tiene = set(self.sudo()._shrimp_roles(include_pending=True))
            info["can_add"] = any(r not in tiene and aqui(r) for r in self._shrimp_addable_roles())
        if info["active_offsite"]:
            # Activo un perfil de otro sitio y ninguno de este (si hubiera
            # alguno, ir.http ya lo habría activado): no se muestra como
            # activo aquí.
            info["active_label"] = _("ninguno en este sitio")
            info["active_pending"] = False
        if not info["roles"] and not info["pending"] and not info["offsite"]:
            return None
        return info

    @api.model
    def _shrimp_capability_permissions(self):
        permisos = super()._shrimp_capability_permissions()
        # Editar instalaciones y tanques pide «Vender» (como manage_ponds);
        # la entrada del menú es de consulta: quien solo puede «ver» el
        # perfil la ve y la sección se muestra en lectura.
        permisos.setdefault("manage_facilities", "sell")
        return permisos

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

    @api.depends("pond_ids.area_m2", "pond_ids.pond_type", "pond_ids.active",
                 "farm_area_ha", "shrimp_num_piscinas")
    def _compute_shrimp_farm_effective(self):
        for rec in self:
            piscinas = rec.sudo().pond_ids.filtered(
                lambda p: p.active and p.pond_type in ("earth", "geomembrane"))
            area_m2 = sum(piscinas.mapped("area_m2"))
            if area_m2 > 0:
                rec.shrimp_farm_area_effective_ha = area_m2 / 10000.0
                rec.shrimp_farm_area_source = "ponds"
            elif rec.farm_area_ha and rec.farm_area_ha > 0:
                rec.shrimp_farm_area_effective_ha = rec.farm_area_ha
                rec.shrimp_farm_area_source = "manual"
            else:
                rec.shrimp_farm_area_effective_ha = 0.0
                rec.shrimp_farm_area_source = "none"
            rec.shrimp_pond_count_effective = len(piscinas) or max(rec.shrimp_num_piscinas or 0, 0)

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