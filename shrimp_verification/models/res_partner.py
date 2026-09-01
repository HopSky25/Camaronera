from odoo import api, fields, models


def _bool_search_domain(operator, value, ids):
    """Traduce la busqueda sobre un booleano calculado a un dominio de ids.

    OJO: Odoo 19 normaliza los dominios booleanos a operadores de CONJUNTO, asi
    que un `("campo", "=", True)` llega aqui como operator='in' con
    value=OrderedSet([True]). Tratarlo como '=' invierte el resultado.
    """
    if operator in ("in", "not in"):
        aceptados = {bool(v) for v in value}
        if operator == "not in":
            aceptados = {True, False} - aceptados
    elif operator in ("=", "=="):
        aceptados = {bool(value)}
    elif operator == "!=":
        aceptados = {not bool(value)}
    else:
        raise NotImplementedError("Operador no soportado: %s" % operator)

    if aceptados == {True, False}:
        return []                       # acepta ambos: sin filtro
    if not aceptados:
        return [("id", "=", False)]     # no acepta ninguno
    return [("id", "in" if True in aceptados else "not in", ids)]


class ResPartner(models.Model):
    # _name explicito: Odoo 19 lo exige al heredar sin cambiar de modelo.
    _name = "res.partner"
    _inherit = "res.partner"

    # El verificador es un cuarto rol del marketplace, no una entidad aparte:
    # asi reutiliza el registro web, los certificados con aprobacion, la
    # reputacion y el uuid_ref que ya existen para los demas roles.
    shrimp_user_type = fields.Selection(
        selection_add=[("verificador", "Verificador")],
        ondelete={"verificador": "set null"},
    )

    # ---- Datos propios del verificador (paso 3 del registro) ----
    ver_razon_social = fields.Char(string="Razón social (Verificador)")
    ver_representante = fields.Char(string="Responsable técnico")
    ver_telefono = fields.Char(string="Teléfono (Verificador)")
    ver_ubicacion = fields.Char(string="Ubicación / base de operaciones")
    ver_cobertura = fields.Char(
        string="Zona de cobertura",
        help="Provincias o sectores donde presta el servicio de verificación.")
    ver_registro_num = fields.Char(
        string="N.º de registro o licencia",
        help="Número de la habilitación que lo autoriza a verificar.")

    # ---- Empresa verificadora y sus técnicos de campo ----
    # Se aprovecha el parent_id nativo de Odoo: la empresa es el partner con la
    # acreditación, y los técnicos son sus contactos hijos.
    shrimp_is_field_tech = fields.Boolean(
        string="Técnico de campo",
        help="Contacto de una empresa verificadora que hace las inspecciones en campo.",
    )

    field_tech_ids = fields.One2many(
        "res.partner", "parent_id", string="Técnicos de campo",
        domain=[("shrimp_is_field_tech", "=", True)],
    )

    field_tech_count = fields.Integer(
        string="N.º de técnicos", compute="_compute_field_tech_count")

    verification_ids = fields.One2many(
        "shrimp.verification",
        "verifier_partner_id",
        string="Verificaciones de la empresa",
    )

    technician_verification_ids = fields.One2many(
        "shrimp.verification",
        "technician_partner_id",
        string="Verificaciones como técnico",
    )

    # ---- Reputación como verificador ----
    verifier_review_ids = fields.One2many(
        "shrimp.verifier.review", "verifier_partner_id",
        string="Reseñas recibidas como verificador")

    # Reseñas del trabajo que hizo esta persona como técnico. NO se muestran al
    # comprador: la reputación pública es la de la empresa, que es a quien
    # contrata. Esto es para que el admin sepa a quién apoyar.
    tech_review_ids = fields.One2many(
        "shrimp.verifier.review", "technician_partner_id",
        string="Reseñas de su trabajo en campo")

    tech_rating_avg = fields.Float(
        string="Nota media en campo", compute="_compute_tech_rating",
        store=True, digits=(3, 2))
    tech_rating_count = fields.Integer(
        string="N.º de reseñas de su trabajo", compute="_compute_tech_rating",
        store=True)

    verifier_rating_avg = fields.Float(
        string="Calificación como verificador", compute="_compute_verifier_rating",
        store=True, digits=(3, 2))
    verifier_rating_count = fields.Integer(
        string="N.º de reseñas como verificador", compute="_compute_verifier_rating",
        store=True)

    verification_count = fields.Integer(
        string="N.º de verificaciones",
        compute="_compute_verification_stats",
    )
    verification_done_count = fields.Integer(
        string="Verificaciones completadas",
        compute="_compute_verification_stats",
    )

    # Acreditacion: un verificador sin certificado aprobado y vigente no deberia
    # poder ser elegido para inspeccionar.
    verifier_is_accredited = fields.Boolean(
        string="Acreditado",
        compute="_compute_verifier_is_accredited",
        search="_search_verifier_is_accredited",
    )

    # La acreditación que lo autoriza a verificar: certificado del catálogo
    # marcado con rol "verificador", aprobado y vigente.
    verifier_accreditation_line_id = fields.Many2one(
        "shrimp.user.certificate.line",
        string="Acreditación vigente",
        compute="_compute_verifier_accreditation",
    )

    def _compute_verifier_accreditation(self):
        today = fields.Date.context_today(self)
        for rec in self:
            lines = rec.certificate_line_ids.filtered(
                lambda c: c.certificate_id.role == "verificador"
                and c.status == "approved"
                and (not c.expiry_date or c.expiry_date >= today)
                and c.file_attachment_id
            )
            # La de expiración más lejana: es la que mejor acredita hoy.
            rec.verifier_accreditation_line_id = lines.sorted(
                key=lambda c: (c.expiry_date or fields.Date.today()), reverse=True)[:1]

    @api.depends("tech_review_ids.rating")
    def _compute_tech_rating(self):
        for rec in self:
            reseñas = rec.tech_review_ids
            rec.tech_rating_count = len(reseñas)
            rec.tech_rating_avg = (
                sum(reseñas.mapped("rating")) / len(reseñas) if reseñas else 0.0)

    @api.depends("verifier_review_ids.rating")
    def _compute_verifier_rating(self):
        for rec in self:
            reseñas = rec.verifier_review_ids
            rec.verifier_rating_count = len(reseñas)
            rec.verifier_rating_avg = (
                sum(reseñas.mapped("rating")) / len(reseñas) if reseñas else 0.0)

    @api.depends("child_ids.shrimp_is_field_tech", "child_ids.active")
    def _compute_field_tech_count(self):
        for rec in self:
            rec.field_tech_count = len(rec.child_ids.filtered("shrimp_is_field_tech"))

    def shrimp_verifier_company(self):
        """Empresa verificadora a la que pertenece este contacto.

        Un técnico devuelve su empresa; la propia empresa se devuelve a sí misma.
        Cualquier otro contacto devuelve vacío.
        """
        self.ensure_one()
        if self.shrimp_is_field_tech and self.parent_id.shrimp_user_type == "verificador":
            return self.parent_id
        if self.shrimp_user_type == "verificador":
            return self
        return self.browse()

    def shrimp_is_verifier_admin(self):
        """True si es la cuenta de la empresa (no un técnico)."""
        self.ensure_one()
        return self.shrimp_user_type == "verificador" and not self.shrimp_is_field_tech

    @api.depends("verification_ids.state")
    def _compute_verification_stats(self):
        for rec in self:
            rec.verification_count = len(rec.verification_ids)
            rec.verification_done_count = len(
                rec.verification_ids.filtered(lambda v: v.state == "approved"))

    def _accredited_domain(self):
        today = fields.Date.context_today(self)
        return [
            ("status", "=", "approved"),
            "|", ("expiry_date", "=", False), ("expiry_date", ">=", today),
        ]

    @api.depends("certificate_line_ids.status", "certificate_line_ids.expiry_date")
    def _compute_verifier_is_accredited(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.verifier_is_accredited = bool(rec.certificate_line_ids.filtered(
                lambda c: c.certificate_id.role == "verificador"
                and c.status == "approved"
                and (not c.expiry_date or c.expiry_date >= today)
            ))

    def _search_verifier_is_accredited(self, operator, value):
        lines = self.env["shrimp.user.certificate.line"].sudo().search(
            self._accredited_domain() + [("certificate_id.role", "=", "verificador")])
        ids = lines.mapped("partner_id").ids
        return _bool_search_domain(operator, value, ids)

    @api.model
    def available_verifiers(self, only_accredited=True):
        """Verificadores que puede elegir el comprador.

        Por defecto solo los que tienen una acreditación de rol verificador
        aprobada y vigente: dejar elegir a uno sin acreditar vaciaría de sentido
        la verificación.
        """
        domain = [("shrimp_user_type", "=", "verificador"), ("active", "=", True)]
        if only_accredited:
            domain.append(("verifier_is_accredited", "=", True))
        # Mejor calificados primero; a igualdad, los que más reseñas acumulan,
        # para que una única nota de 5 no adelante a quien lleva veinte trabajos.
        return self.sudo().search(
            domain, order="verifier_rating_avg desc, verifier_rating_count desc, name asc")
