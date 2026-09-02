from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpClientPrice(models.Model):
    """Precio por libra que un vendedor asigna a un cliente para un producto.

    Si un comprador logueado tiene un precio asignado para un producto, en el
    marketplace verá ese precio en vez del precio de publicación.
    """

    _name = "shrimp.client.price"
    _description = "Precio asignado a cliente"
    _inherit = "shrimp.uuid.mixin"
    _order = "seller_partner_id, client_partner_id, product_id"

    seller_partner_id = fields.Many2one(
        "res.partner", string="Vendedor", required=True, index=True, ondelete="cascade")
    client_partner_id = fields.Many2one(
        "res.partner", string="Cliente", required=True, index=True, ondelete="cascade")
    product_id = fields.Many2one(
        "shrimp.product", string="Producto", required=True, index=True, ondelete="cascade")
    price = fields.Float(string="Precio por libra", required=True)
    active = fields.Boolean(string="Activo", default=True)

    _sql_constraints = [
        ("uniq_client_product",
         "unique(client_partner_id, product_id)",
         "Ya existe un precio asignado para este cliente y producto."),
    ]

    @api.constrains("price")
    def _check_price(self):
        for rec in self:
            if rec.price < 0:
                raise ValidationError(_("El precio por libra no puede ser negativo."))

    @api.constrains("seller_partner_id", "client_partner_id")
    def _check_not_self(self):
        for rec in self:
            if rec.seller_partner_id == rec.client_partner_id:
                raise ValidationError(_("El vendedor y el cliente no pueden ser el mismo."))

    @api.constrains("product_id", "seller_partner_id")
    def _check_product_owner(self):
        for rec in self:
            if rec.product_id.seller_partner_id != rec.seller_partner_id:
                raise ValidationError(_(
                    "El producto «%s» no pertenece al vendedor indicado."
                ) % rec.product_id.name)
