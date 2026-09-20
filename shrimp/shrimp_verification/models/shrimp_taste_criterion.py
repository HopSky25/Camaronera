from odoo import fields, models


class ShrimpTasteCriterion(models.Model):
    """Criterio de cata (tabla maestra editable): p. ej. "Olor correcto",
    "Color correcto". El verificador marca cuáles se cumplen en cada informe."""

    _name = "shrimp.taste.criterion"
    _inherit = "shrimp.uuid.mixin"
    _description = "Criterio de cata"
    _order = "sequence, name"

    name = fields.Char(string="Criterio", required=True)
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
