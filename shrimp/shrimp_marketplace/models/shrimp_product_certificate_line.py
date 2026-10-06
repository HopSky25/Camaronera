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

    # Revisión interna, igual que los certificados del usuario. Antes un
    # vendedor subía cualquier PDF como "certificado del producto" y se
    # mostraba al público de inmediato, sin que nadie lo mirara. Ahora solo
    # se publica lo aprobado. Los que vienen de un certificado del usuario ya
    # aprobado heredan esa aprobación.
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
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        UserLine = self.env["shrimp.user.certificate.line"].sudo()
        for vals in vals_list:
            origen = vals.get("source_user_certificate_line_id")
            if self.env.context.get("shrimp_keep_cert_status"):
                # Copia de un certificado ya revisado (p. ej. al pasar el lote
                # al comprador): conserva el estado del original.
                continue
            if origen:
                vals["status"] = (
                    "approved" if UserLine.browse(origen).status == "approved" else "pending")
            elif not self.env.su or self.env.user.share:
                # Desde el portal siempre entra pendiente, venga lo que venga.
                vals["status"] = "pending"
        return super().create(vals_list)

    def _notify_seller_status(self):
        for rec in self:
            producto = rec.product_id.sudo()
            etiqueta = dict(self._fields["status"].selection).get(rec.status)
            producto.message_post(body=_(
                "El certificado «%(c)s» del producto quedó %(e)s.") % {
                    "c": rec.certificate_id.name or "", "e": (etiqueta or "").lower()},
                partner_ids=producto.seller_partner_id.ids,
                subtype_xmlid="mail.mt_comment")

    def action_approve(self):
        self.write({"status": "approved"})
        self._notify_seller_status()
        return True

    def action_reject(self):
        self.write({"status": "rejected"})
        self._notify_seller_status()
        return True

    def action_reset_pending(self):
        self.write({"status": "pending"})
        return True

    def action_open_attachment(self):
        self.ensure_one()
        if not self.attachment_id:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.attachment_id.id,
            "target": "new",
        }

    @api.depends("certificate_id")
    def _compute_issuer(self):
        for rec in self:
            rec.issuer = rec.certificate_id.issuer or False

    # Odoo 19 ignora _sql_constraints EN SILENCIO —solo deja un
    # WARNING en el arranque— y la restriccion no llega nunca a
    # PostgreSQL. Se comprobo contra pg_constraint: ninguna de las
    # unicidades declaradas asi existia en la base.
    _unique_product_certificate_number = models.Constraint(
        "unique(product_id, certificate_id, number)",
        "Ya existe este certificado con el mismo número para este producto.",
    )

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
