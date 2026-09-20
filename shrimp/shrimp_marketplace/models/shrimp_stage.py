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

    # Odoo 19 ignora _sql_constraints EN SILENCIO —solo deja un
    # WARNING en el arranque— y la restriccion no llega nunca a
    # PostgreSQL. Se comprobo contra pg_constraint: ninguna de las
    # unicidades declaradas asi existia en la base.
    _shrimp_stage_name_unique = models.Constraint(
        "unique(name)",
        "Ya existe un estadío con ese nombre.",
    )

    _shrimp_stage_code_unique = models.Constraint(
        "unique(code)",
        "Ya existe un estadío con ese código.",
    )
