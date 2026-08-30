from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpStockMove(models.Model):
    _name = "shrimp.stock.move"
    _inherit = "shrimp.uuid.mixin"
    _description = "Movimiento de stock (trazabilidad)"
    _order = "create_date desc"

    product_id = fields.Many2one("shrimp.product", required=True, index=True)

    source_partner_id = fields.Many2one("res.partner", string="Origen")
    dest_partner_id = fields.Many2one("res.partner", string="Destino")

    qty = fields.Float(required=True)

    parent_move_id = fields.Many2one(
        "shrimp.stock.move",
        string="Movimiento padre (trazabilidad)",
        index=True,
    )

    transaction_id = fields.Many2one("shrimp.transaction", index=True)

    date = fields.Datetime(default=fields.Datetime.now)

    @api.constrains("qty")
    def _check_qty(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError(_("La cantidad movida debe ser mayor a 0."))

    @api.constrains("source_partner_id", "dest_partner_id")
    def _check_partners(self):
        for rec in self:
            if rec.source_partner_id and rec.dest_partner_id and rec.source_partner_id == rec.dest_partner_id:
                raise ValidationError(_("El origen y destino no pueden ser iguales."))