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

    _sql_constraints = [
        ("shrimp_species_name_unique", "unique(name)", "Ya existe una especie con ese nombre."),
        ("shrimp_species_code_unique", "unique(code)", "Ya existe una especie con ese código."),
    ]