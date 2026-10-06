from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpStockMove(models.Model):
    _name = "shrimp.stock.move"
    _inherit = "shrimp.uuid.mixin"
    _description = "Movimiento de stock (trazabilidad)"
    _order = "create_date desc"

    product_id = fields.Many2one("shrimp.product", string="Producto", required=True, index=True)

    source_partner_id = fields.Many2one("res.partner", string="Origen")
    dest_partner_id = fields.Many2one("res.partner", string="Destino")

    qty = fields.Float(string="Cantidad", required=True)

    parent_move_id = fields.Many2one(
        "shrimp.stock.move",
        string="Movimiento padre (trazabilidad)",
        index=True,
    )

    transaction_id = fields.Many2one("shrimp.transaction", string="Transacción", index=True)

    date = fields.Datetime(string="Fecha", default=fields.Datetime.now)

    # Qué representa el movimiento. Hasta ahora solo existían las ventas
    # (transfer: cambia el dueño). Los demás son movimientos INTERNOS del
    # dueño del lote, que documentan lo que le pasa físicamente al producto
    # sin cambiar de manos: sembrarlo, declarar lo que de verdad salió de la
    # larvicultura, el peso real en planta, la merma del empaque y la salida
    # de la plataforma (exportación). La cantidad es siempre positiva; si un
    # ajuste SUBE el lote lo dice `direction`.
    move_type = fields.Selection(
        [("transfer", "Venta / transferencia"),
         ("sowing", "Siembra (consumo)"),
         ("consumption", "Consumo"),
         ("production", "Producción declarada"),
         ("adjustment", "Ajuste"),
         ("packing", "Empaque"),
         ("export", "Salida / exportación")],
        string="Tipo", default="transfer", required=True, index=True)
    direction = fields.Selection(
        [("out", "Sale del lote"), ("in", "Entra al lote")],
        string="Sentido", default="out", required=True,
        help="Solo cuenta en los movimientos internos: si la cantidad se "
             "descontó del lote o se le sumó.")
    lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Lote afectado", index=True, ondelete="set null",
        help="Lote del que sale (o al que entra) la cantidad.")
    reason = fields.Char(string="Motivo")
    qty_before = fields.Float(string="Saldo antes")
    qty_after = fields.Float(string="Saldo después")
    allocation_id = fields.Many2one(
        "shrimp.lot.allocation", string="Siembra", index=True, ondelete="set null")

    def is_internal(self):
        """True si el movimiento no cambia el dueño (siembra, ajuste...)."""
        self.ensure_one()
        return (self.move_type or "transfer") != "transfer"

    def move_type_label(self):
        self.ensure_one()
        return dict(self._fields["move_type"]._description_selection(self.env)).get(
            self.move_type or "transfer", "")

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