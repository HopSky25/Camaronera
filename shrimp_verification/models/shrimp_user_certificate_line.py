from odoo import api, fields, models


class ShrimpUserCertificateLine(models.Model):
    _name = "shrimp.user.certificate.line"
    _inherit = "shrimp.user.certificate.line"

    # Expuesto para poder filtrar la bandeja de aprobación por rol.
    certificate_role = fields.Selection(
        related="certificate_id.role",
        string="Aplica a",
        store=True,
        index=True,
    )

    is_verifier_accreditation = fields.Boolean(
        string="Acredita como verificador",
        compute="_compute_is_verifier_accreditation",
        store=True,
        help="La acreditación que habilita al contacto para inspeccionar en campo.",
    )

    partner_user_type = fields.Selection(
        related="partner_id.shrimp_user_type",
        string="Tipo de contacto",
        store=True,
    )

    is_expired = fields.Boolean(
        string="Vencida",
        compute="_compute_is_expired",
        search="_search_is_expired",
    )

    @api.depends("certificate_id.role")
    def _compute_is_verifier_accreditation(self):
        for rec in self:
            rec.is_verifier_accreditation = rec.certificate_id.role == "verificador"

    def _compute_is_expired(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_expired = bool(rec.expiry_date and rec.expiry_date < today)

    def _search_is_expired(self, operator, value):
        # Odoo 19 normaliza los dominios booleanos a operadores de conjunto:
        # ("is_expired", "=", True) llega como operator='in', value={True}.
        today = fields.Date.context_today(self)
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
            return []
        if not aceptados:
            return [("id", "=", False)]
        if True in aceptados:
            return [("expiry_date", "<", today)]
        return ["|", ("expiry_date", "=", False), ("expiry_date", ">=", today)]

    def action_approve(self):
        res = super().action_approve()
        # Al aprobar la acreditación, el verificador pasa a estar disponible
        # para los compradores: se le avisa.
        for rec in self.filtered(lambda l: l.is_verifier_accreditation):
            rec._notify_accreditation_approved()
        return res

    def _notify_accreditation_approved(self):
        self.ensure_one()
        email_to = self.partner_id.email
        if not email_to:
            return
        try:
            template = self.env.ref(
                "shrimp_verification.mail_template_accreditation_approved",
                raise_if_not_found=False)
            if template:
                template.sudo().send_mail(
                    self.id, force_send=True, email_values={"email_to": email_to})
        except Exception:
            # El aviso nunca debe impedir la aprobación.
            pass
