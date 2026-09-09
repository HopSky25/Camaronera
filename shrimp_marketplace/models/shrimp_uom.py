from odoo import fields, models


class ShrimpUom(models.Model):
    _name = "shrimp.uom"
    _inherit = "shrimp.uuid.mixin"
    _description = "Unidad de medida"
    _order = "sequence, name"

    name = fields.Char(string="Nombre", required=True, translate=True)
    code = fields.Char(
        string="Código", required=True,
        help="Código técnico interno (p. ej. libra, millar, unidad). Se usa para mapear datos.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    commission_cents = fields.Float(
        string="Comisión (centavos por unidad)",
        default=0.0,
        groups="base.group_system",
        help="Centavos que cobra el marketplace por cada unidad vendida en esta "
             "unidad de medida. Solo visible/editable por el administrador del sistema.",
    )

    # Odoo 19 ignora _sql_constraints EN SILENCIO —solo deja un
    # WARNING en el arranque— y la restriccion no llega nunca a
    # PostgreSQL. Se comprobo contra pg_constraint: ninguna de las
    # unicidades declaradas asi existia en la base.
    _shrimp_uom_code_unique = models.Constraint(
        "unique(code)",
        "El código de la unidad de medida debe ser único.",
    )
