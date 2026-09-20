from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class ShrimpStockLot(models.Model):
    _name = "shrimp.stock.lot"
    _inherit = "shrimp.uuid.mixin"
    _description = "Stock por lote (trazabilidad)"
    _rec_name = "product_id"

    product_id = fields.Many2one("shrimp.product", string="Producto", required=True, index=True, ondelete="cascade")
    owner_id = fields.Many2one("res.partner", string="Propietario", required=True, index=True)

    origin_move_id = fields.Many2one(
        "shrimp.stock.move",
        string="Movimiento origen",
        index=True,
    )

    initial_qty = fields.Float(string="Cantidad inicial", required=True)
    available_qty = fields.Float(string="Cantidad disponible", required=True)

    uom_id = fields.Many2one("shrimp.uom", string="Unidad de medida")

    state = fields.Selection(
        [("available", "Disponible"), ("consumed", "Consumido")],
        string="Estado",
        default="available",
        index=True,
    )

    @api.constrains("initial_qty", "available_qty")
    def _check_quantities(self):
        for rec in self:
            if float_compare(rec.initial_qty, 0.0, precision_digits=6) == -1:
                raise ValidationError(_("La cantidad inicial no puede ser negativa."))

            if float_compare(rec.available_qty, 0.0, precision_digits=6) == -1:
                raise ValidationError(_("La cantidad disponible no puede ser negativa."))

            if float_compare(rec.available_qty, rec.initial_qty, precision_digits=6) == 1:
                raise ValidationError(_("La cantidad disponible no puede ser mayor a la cantidad inicial."))

    @api.depends("product_id.name", "owner_id.name", "available_qty", "uom_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s - %s - %s %s" % (
                rec.product_id.name or "",
                rec.owner_id.name or "",
                rec.available_qty or 0.0,
                rec.uom_id.name or "",
            )