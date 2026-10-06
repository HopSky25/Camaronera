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

    move_ids = fields.One2many(
        "shrimp.stock.move", "lot_id", string="Movimientos del lote")

    def _shrimp_internal_move(self, move_type, qty, reason, direction="out", date=None, **extra):
        """Mueve cantidad DENTRO del lote sin cambiar de dueño y deja el
        movimiento que lo documenta (siembra, producción, ajuste, empaque,
        exportación).

        `qty` es positiva; `direction` dice si se descuenta (out) o se suma
        (in). Una subida por encima de lo inicial también sube la cantidad
        inicial: el lote creció de verdad (p. ej. más peso en planta que en
        la finca). Devuelve el movimiento, o un recordset vacío si la cantidad
        es cero.
        """
        self.ensure_one()
        qty = float(qty or 0.0)
        if float_compare(qty, 0.0, precision_digits=6) <= 0:
            return self.env["shrimp.stock.move"]
        lot = self.sudo()
        antes = lot.available_qty
        if direction == "out":
            if float_compare(qty, antes, precision_digits=6) == 1:
                raise ValidationError(_(
                    "El lote «%(lote)s» solo tiene %(disp)s disponibles; no se pueden "
                    "descontar %(qty)s.") % {"lote": lot.display_name, "disp": antes, "qty": qty})
            despues = antes - qty
            vals = {"available_qty": despues}
        else:
            despues = antes + qty
            vals = {"available_qty": despues,
                    "initial_qty": max(lot.initial_qty, despues)}
        vals["state"] = "consumed" if float_compare(despues, 0.0, precision_digits=6) <= 0 else "available"
        move_vals = {
            "product_id": lot.product_id.id,
            "source_partner_id": lot.owner_id.id,
            "dest_partner_id": False,
            "qty": qty,
            "parent_move_id": lot.origin_move_id.id or False,
            "move_type": move_type,
            "direction": direction,
            "lot_id": lot.id,
            "reason": reason,
            "qty_before": antes,
            "qty_after": despues,
            "date": date or fields.Datetime.now(),
        }
        move_vals.update(extra)
        move = self.env["shrimp.stock.move"].sudo().create(move_vals)
        lot.write(vals)
        # El disponible del producto depende de los lotes del vendedor.
        lot.product_id._compute_available_qty()
        return move

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