from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpUserCertificateLine(models.Model):
    _name = "shrimp.user.certificate.line"
    _inherit = "shrimp.uuid.mixin"
    _description = "Línea de certificado del usuario"
    _order = "id desc"

    partner_id = fields.Many2one(
        "res.partner",
        string="Contacto",
        required=True,
        ondelete="cascade",
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
        related="certificate_id.issuer",
        store=True,
        readonly=True,
    )

    certificate_number = fields.Char(string="Número de certificado")
    issue_date = fields.Date(string="Fecha de emisión")
    expiry_date = fields.Date(string="Fecha de expiración")

    file_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Archivo adjunto",
        required=True,
        ondelete="restrict",
    )

    status = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("approved", "Aprobado"),
            ("rejected", "Rechazado"),
        ],
        string="Estado",
        default="pending",
        required=True,
        index=True,
    )

    def action_open_attachment(self):
        self.ensure_one()
        if not self.file_attachment_id:
            return False

        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.file_attachment_id.id,
            "target": "new",
        }

    def action_approve(self):
        self.write({"status": "approved"})

    def action_reject(self):
        self.write({"status": "rejected"})

    def action_reset_pending(self):
        self.write({"status": "pending"})

    _sql_constraints = [
        (
            "unique_partner_certificate_number",
            "unique(partner_id, certificate_id, certificate_number)",
            "Ya existe este certificado con el mismo número para este contacto."
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