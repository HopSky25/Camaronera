from odoo import fields, models


class ShrimpSpecies(models.Model):
    _name = "shrimp.species"
    _inherit = "shrimp.uuid.mixin"
    _description = "Especies de camarón"
    _order = "name asc"

    name = fields.Char(string="Nombre común", required=True)
    scientific_name = fields.Char(string="Nombre científico")
    code = fields.Char(string="Código")
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)

    # Odoo 19 ignora _sql_constraints EN SILENCIO —solo deja un
    # WARNING en el arranque— y la restriccion no llega nunca a
    # PostgreSQL. Se comprobo contra pg_constraint: ninguna de las
    # unicidades declaradas asi existia en la base.
    _shrimp_species_name_unique = models.Constraint(
        "unique(name)",
        "Ya existe una especie con ese nombre.",
    )

    _shrimp_species_code_unique = models.Constraint(
        "unique(code)",
        "Ya existe una especie con ese código.",
    )
