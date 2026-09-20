from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpVerificationLine(models.Model):
    """Una fila de la clasificacion por talla, dentro de una clase de calidad.

    Corresponde a las lineas del parte de WhatsApp:
        *Clase A*
        16= 7.983,55
        21= 20.142,65
    """

    _name = "shrimp.verification.line"
    _inherit = "shrimp.uuid.mixin"
    _description = "Clasificación por talla de la verificación"
    _order = "quality_class asc, sequence asc, id asc"

    verification_id = fields.Many2one(
        "shrimp.verification",
        string="Verificación",
        required=True,
        ondelete="cascade",
        index=True,
    )

    quality_class = fields.Selection(
        [("a", "Clase A"), ("b", "Clase B"), ("c", "Clase C")],
        string="Clase",
        required=True,
        default="a",
        index=True,
    )

    # Se guarda el codigo de talla tal cual lo usan en campo (U15, 16, 21, 26...)
    # en vez de forzar un Many2one: el parte de planta llega con estos codigos y
    # el catalogo de tallas no siempre los cubre todos.
    size_code = fields.Char(
        string="Talla",
        required=True,
        help="Código de talla tal como llega del parte de planta: U15, 16, 21, 26, 31, 36, 41...",
    )

    weight_lb = fields.Float(
        string="Libras",
        required=True,
        digits=(16, 2),
    )

    sequence = fields.Integer(default=10)

    percent_of_total = fields.Float(
        string="% del total",
        compute="_compute_percent_of_total",
        digits=(5, 2),
        help="Peso de esta talla sobre el total procesado de la verificación.",
    )

    @api.depends("weight_lb", "verification_id.total_processed_lb")
    def _compute_percent_of_total(self):
        for rec in self:
            total = rec.verification_id.total_processed_lb or 0.0
            rec.percent_of_total = (100.0 * rec.weight_lb / total) if total else 0.0

    @api.constrains("weight_lb")
    def _check_weight(self):
        for rec in self:
            if rec.weight_lb <= 0:
                raise ValidationError(_("Las libras de la talla %s deben ser mayores a 0.") % rec.size_code)

    # Odoo 19: _sql_constraints ya no se aplica; models.Constraint si.
    _uniq_size_per_class = models.Constraint(
        "unique(verification_id, quality_class, size_code)",
        "Esa talla ya está registrada en esa clase para esta verificación.",
    )


class ShrimpVerificationCount(models.Model):
    """Los 'Conteo. 19 / 24 / 26' del parte: camarones por libra medidos en planta."""

    _name = "shrimp.verification.count"
    _inherit = "shrimp.uuid.mixin"
    _description = "Conteo de la verificación"
    _order = "sequence asc, id asc"

    verification_id = fields.Many2one(
        "shrimp.verification",
        string="Verificación",
        required=True,
        ondelete="cascade",
        index=True,
    )
    value = fields.Float(string="Conteo", required=True, digits=(16, 2))
    note = fields.Char(string="Observación")
    sequence = fields.Integer(default=10)

    @api.constrains("value")
    def _check_value(self):
        for rec in self:
            if rec.value <= 0:
                raise ValidationError(_("El conteo debe ser mayor a 0."))
