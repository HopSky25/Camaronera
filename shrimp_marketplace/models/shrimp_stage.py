from odoo import fields, models


class ShrimpStage(models.Model):
    _name = "shrimp.stage"
    _inherit = "shrimp.uuid.mixin"
    _description = "Estadíos del camarón"
    _order = "sequence asc, name asc"

    name = fields.Char(string="Nombre", required=True)
    code = fields.Char(string="Código", required=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("shrimp_stage_name_unique", "unique(name)", "Ya existe un estadío con ese nombre."),
        ("shrimp_stage_code_unique", "unique(code)", "Ya existe un estadío con ese código."),
    ]