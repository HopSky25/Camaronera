from odoo import fields, models


class ShrimpGeneticsLine(models.Model):
    _name = "shrimp.genetics.line"
    _inherit = "shrimp.uuid.mixin"
    _description = "Líneas genéticas de camarón"
    _order = "name asc"

    name = fields.Char(string="Nombre", required=True)
    code = fields.Char(string="Código")
    species_id = fields.Many2one(
        "shrimp.species",
        string="Especie",
        ondelete="restrict",
        index=True,
    )
    brand = fields.Char(string="Marca / Casa genética")
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("shrimp_genetics_line_name_unique", "unique(name)", "Ya existe una línea genética con ese nombre."),
        ("shrimp_genetics_line_code_unique", "unique(code)", "Ya existe una línea genética con ese código."),
    ]