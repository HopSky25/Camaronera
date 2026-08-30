from odoo import api, fields, models

class ShrimpProductEvolution(models.Model):
    _name = "shrimp.product.evolution"
    _inherit = "shrimp.uuid.mixin"
    _description = "Histórico de evolución del producto"
    _order = "date desc, id desc"

    product_id = fields.Many2one(
        "shrimp.product",
        string="Producto",
        required=True,
        ondelete="cascade",
        index=True,
    )

    date = fields.Datetime(
        string="Fecha",
        default=fields.Datetime.now,
        required=True,
    )

    stage_id = fields.Many2one(
        "shrimp.stage",
        string="Estadío",
        ondelete="restrict",
    )

    avg_size_mg = fields.Float(
        string="Tamaño promedio (mg)",
    )

    survival_rate = fields.Float(
        string="Supervivencia (%)",
    )

    health_status = fields.Text(
        string="Estado sanitario / Observaciones",
    )

    available_qty = fields.Float(
        string="Cantidad disponible",
    )

    note = fields.Text(string="Nota")
    user_id = fields.Many2one(
        "res.users",
        string="Registrado por",
        default=lambda self: self.env.user,
        readonly=True,
    )