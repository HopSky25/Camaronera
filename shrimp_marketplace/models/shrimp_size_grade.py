from odoo import api, fields, models


class ShrimpSizeGrade(models.Model):
    _name = "shrimp.size.grade"
    _inherit = "shrimp.uuid.mixin"
    _description = "Talla de camarón (por presentación)"
    _order = "presentation, sequence, name"

    name = fields.Char(string="Talla", required=True, help="Código de talla, p. ej. 20/30.")
    presentation = fields.Selection([
        ("entero", "Entero"),
        ("cola", "Cola"),
    ], string="Presentación", required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("shrimp_size_grade_uniq", "unique(presentation, name)",
         "Ya existe esa talla para esa presentación."),
    ]

    @api.depends("name", "presentation")
    def _compute_display_name(self):
        labels = dict(self._fields["presentation"].selection)
        for rec in self:
            pres = labels.get(rec.presentation, "")
            rec.display_name = ("%s · %s" % (pres, rec.name)) if rec.name else pres
