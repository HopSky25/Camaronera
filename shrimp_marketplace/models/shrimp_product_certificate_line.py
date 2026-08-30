from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpProductCertificateLine(models.Model):
    _name = "shrimp.product.certificate.line"
    _inherit = "shrimp.uuid.mixin"
    _description = "Línea de certificado del producto"
    _order = "id desc"

    product_id = fields.Many2one(
        "shrimp.product",
        string="Producto",
        required=True,
        ondelete="cascade",
        index=True,
    )

    source_user_certificate_line_id = fields.Many2one(
        "shrimp.user.certificate.line",
        string="Certificado origen del usuario",
        ondelete="set null",
        index=True,
    )

    certificate_id = fields.Many2one(
        "shrimp.certificate",
        string="Certificado",
        required=True,
        ondelete="restrict",
        index=True,
    )

    issuer = fields.Char(
        string="Entidad emisora",
        compute="_compute_issuer",
        store=True,
    )

    number = fields.Char(string="Número de certificado")
    issue_date = fields.Date(string="Fecha de emisión")
    expiry_date = fields.Date(string="Fecha de expiración")

    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Archivo adjunto",
        required=True,
        ondelete="restrict",
    )

    active = fields.Boolean(default=True)

    @api.depends("certificate_id")
    def _compute_issuer(self):
        for rec in self:
            rec.issuer = rec.certificate_id.issuer or False

    _sql_constraints = [
        (
            "unique_product_certificate_number",
            "unique(product_id, certificate_id, number)",
            "Ya existe este certificado con el mismo número para este producto."
        ),
    ]

    @api.constrains("issue_date", "expiry_date")
    def _check_dates(self):
        for rec in self:
            if rec.issue_date and rec.expiry_date and rec.expiry_date < rec.issue_date:
                raise ValidationError(
                    _("La fecha de expiración no puede ser menor que la fecha de emisión.")
                )

    @api.constrains("certificate_id", "expiry_date")
    def _check_expiry_required(self):
        for rec in self:
            if rec.certificate_id and rec.certificate_id.expires_required and not rec.expiry_date:
                raise ValidationError(
                    _("El certificado '%s' requiere fecha de expiración.") % rec.certificate_id.name
                )