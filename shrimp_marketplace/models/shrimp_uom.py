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

    _sql_constraints = [
        ("shrimp_uom_code_unique", "unique(code)", "El código de la unidad de medida debe ser único."),
    ]
