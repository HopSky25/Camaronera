from odoo import api, fields, models


class ShrimpTransaction(models.Model):
    _inherit = "shrimp.transaction"

    # El empaque no cuelga de la compraventa —en maquila la propiedad nunca
    # cambia de manos— asi que no hay clave ajena entre los dos. El puente es
    # el LOTE: si este lote paso por una planta de empaque, eso forma parte de
    # su trazabilidad y tiene que constar en el certificado.
    #
    # Sin esto la cadena se corta justo donde mas importa: el comprador final
    # no puede saber en que planta habilitada se empaco lo que esta comprando,
    # que es precisamente lo que su propio cliente le va a exigir.
    copack_order_ids = fields.Many2many(
        "shrimp.copack.order", string="Empaques del lote",
        compute="_compute_copack_orders")

    @api.depends("product_id")
    def _compute_copack_orders(self):
        Orden = self.env["shrimp.copack.order"].sudo()
        for rec in self:
            if not rec.product_id:
                rec.copack_order_ids = False
                continue
            # Solo trabajos con el empaque ya hecho: una orden a medias no
            # acredita nada y en el certificado seria ruido.
            rec.copack_order_ids = Orden.search([
                ("product_id", "=", rec.product_id.id),
                ("state", "in", ("packed", "signed", "closed")),
            ], order="packed_date")
