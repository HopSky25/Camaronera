from odoo import fields, models


class ShrimpTechRole(models.Model):
    """Cargo de un técnico de campo (tabla maestra editable): p. ej. "Técnico
    de campo", "Supervisor", "Inspector". La empresa verificadora elige el
    cargo al dar de alta o editar a cada técnico."""

    _name = "shrimp.tech.role"
    _description = "Cargo de técnico de campo"
    _order = "sequence, name"

    name = fields.Char(string="Cargo", required=True)
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
