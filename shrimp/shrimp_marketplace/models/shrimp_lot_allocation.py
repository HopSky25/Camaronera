from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class ShrimpLotAllocation(models.Model):
    _name = "shrimp.lot.allocation"
    _inherit = "shrimp.uuid.mixin"
    _description = "Asignación de lotes a piscinas"
    _order = "allocation_date desc, id desc"

    stock_lot_id = fields.Many2one(
        "shrimp.stock.lot",
        string="Lote",
        required=True,
        ondelete="cascade",
        index=True,
    )

    pond_id = fields.Many2one(
        "shrimp.partner.pond",
        string="Piscina",
        required=True,
        ondelete="cascade",
        index=True,
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        related="pond_id.partner_id",
        store=True,
        readonly=True,
    )

    product_id = fields.Many2one(
        "shrimp.product",
        string="Producto",
        related="stock_lot_id.product_id",
        store=True,
        readonly=True,
    )

    allocated_qty = fields.Float(
        string="Cantidad asignada",
        required=True,
    )

    allocation_date = fields.Date(
        string="Fecha de asignación",
        default=fields.Date.context_today,
        required=True,
    )

    notes = fields.Text(string="Observaciones")

    state = fields.Selection([
        ("draft", "Borrador"),
        ("allocated", "Asignado"),
        ("released", "Liberado"),
        ("cancelled", "Cancelado"),
    ], string="Estado", default="allocated", required=True, index=True)

    @api.constrains("allocated_qty")
    def _check_allocated_qty(self):
        for rec in self:
            if rec.allocated_qty <= 0:
                raise ValidationError(_("La cantidad asignada debe ser mayor a 0."))

    @api.constrains("stock_lot_id", "pond_id")
    def _check_same_partner(self):
        for rec in self:
            if rec.stock_lot_id and rec.pond_id:
                if rec.stock_lot_id.owner_id != rec.pond_id.partner_id:
                    raise ValidationError(_("El lote y la piscina deben pertenecer al mismo partner."))

    @api.constrains("allocated_qty", "stock_lot_id")
    def _check_not_exceed_lot_qty(self):
        for rec in self:
            if rec.stock_lot_id and float_compare(
                rec.allocated_qty,
                rec.stock_lot_id.available_qty,
                precision_digits=6,
            ) == 1:
                raise ValidationError(_("La cantidad asignada no puede superar la cantidad disponible del lote."))