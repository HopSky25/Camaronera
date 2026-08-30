from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpCertificate(models.Model):
    _name = "shrimp.certificate"
    _description = "Certificado"
    _order = "sequence, name"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]

    name = fields.Char(
        string="Nombre del certificado",
        required=True,
        tracking=True,
    )
    
    issuer = fields.Char(
        string="Entidad que lo otorga",
        required=True,
        tracking=True,
    )

    role = fields.Selection(
        [
            ("semillero", "Semillero"),
            ("laboratorio", "Laboratorio"),
            ("camaronera", "Camaronera"),
            ("all", "Todos"),
        ],
        string="Aplica a",
        required=True,
        default="all",
        index=True,
        tracking=True,
    )

    certificate_type = fields.Selection(
        [
            ("quality", "Calidad"),
            ("biosecurity", "Bioseguridad"),
            ("sustainability", "Sostenibilidad"),
            ("social", "Social"),
            ("other", "Otro"),
        ],
        string="Tipo",
        default="other",
        required=True,
        tracking=True,
    )

    code = fields.Char(
        string="Código/Norma",
        help="Ej: GLOBALG.A.P., HACCP, ISO 22000",
        tracking=True,
    )
    
    description = fields.Text(
        string="Descripción",
        tracking=True,
    )
    
    expires_required = fields.Boolean(
        string="Requiere fecha de expiración",
        default=True,
        tracking=True,
    )

    duration_value = fields.Integer(
        string="Duración",
        tracking=True,
        help="Valor numérico de vigencia del certificado.",
    )

    duration_period = fields.Selection(
        [
            ("days", "Días"),
            ("weeks", "Semanas"),
            ("months", "Meses"),
            ("years", "Años"),
        ],
        string="Período",
        default="years",
        tracking=True,
        help="Unidad de tiempo de vigencia del certificado.",
    )

    active = fields.Boolean(
        default=True,
        tracking=True,
    )
    
    sequence = fields.Integer(
        default=10,
        tracking=True,
    )

    _sql_constraints = [
        (
            "shrimp_certificate_name_uniq",
            "unique(name)",
            "Ya existe un certificado con ese nombre."
        ),
    ]

    @api.constrains("duration_value")
    def _check_duration_value(self):
        for rec in self:
            if rec.duration_value and rec.duration_value < 0:
                raise ValidationError(_("La duración no puede ser negativa."))

    @api.constrains("expires_required", "duration_value", "duration_period")
    def _check_expiration_rules(self):
        for rec in self:
            if rec.expires_required:
                if not rec.duration_value or rec.duration_value <= 0:
                    raise ValidationError(
                        _("Si el certificado requiere expiración, la duración debe ser mayor a 0.")
                    )
                if not rec.duration_period:
                    raise ValidationError(
                        _("Debe seleccionar el período de duración del certificado.")
                    )

    @api.constrains("name", "issuer", "code")
    def _check_required_texts(self):
        for rec in self:
            if not rec.name or not rec.name.strip():
                raise ValidationError(_("El nombre del certificado es obligatorio."))
            if not rec.issuer or not rec.issuer.strip():
                raise ValidationError(_("La entidad que otorga el certificado es obligatoria."))
            if rec.code and not rec.code.strip():
                raise ValidationError(_("El código del certificado no puede estar vacío."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "name" in vals and vals.get("name"):
                vals["name"] = vals["name"].strip()
            if "issuer" in vals and vals.get("issuer"):
                vals["issuer"] = vals["issuer"].strip()
            if "code" in vals and vals.get("code"):
                vals["code"] = vals["code"].strip()
        return super().create(vals_list)

    def write(self, vals):
        if "name" in vals and vals.get("name"):
            vals["name"] = vals["name"].strip()
        if "issuer" in vals and vals.get("issuer"):
            vals["issuer"] = vals["issuer"].strip()
        if "code" in vals and vals.get("code"):
            vals["code"] = vals["code"].strip()
        return super().write(vals)