from odoo import api, fields, models


class ShrimpPartnerFacility(models.Model):
    _name = "shrimp.partner.facility"
    _inherit = "shrimp.uuid.mixin"
    _description = "Instalaciones del partner"
    _order = "name asc"

    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        required=True,
        ondelete="cascade",
        index=True,
    )

    name = fields.Char(string="Nombre", required=True)
    code = fields.Char(string="Código")

    facility_type = fields.Selection([
        ("hatchery", "Hatchery"),
        ("laboratory", "Laboratorio"),
        ("farm", "Granja"),
        ("warehouse", "Bodega"),
        ("office", "Oficina"),
        ("other", "Otro"),
    ], string="Tipo", required=True, default="farm")

    address = fields.Char(string="Dirección")
    city = fields.Char(string="Ciudad")
    province = fields.Char(string="Provincia / Estado")
    country_id = fields.Many2one("res.country", string="País")

    latitude = fields.Float(string="Latitud")
    longitude = fields.Float(string="Longitud")

    active = fields.Boolean(default=True)
    notes = fields.Text(string="Observaciones")

    pond_ids = fields.One2many(
        "shrimp.partner.pond",
        "facility_id",
        string="Piscinas",
    )

    pond_count = fields.Integer(
        string="Cantidad de piscinas",
        compute="_compute_pond_count",
    )

    @api.depends("pond_ids")
    def _compute_pond_count(self):
        for rec in self:
            rec.pond_count = len(rec.pond_ids)

    _sql_constraints = [
        (
            "shrimp_partner_facility_partner_code_unique",
            "unique(partner_id, code)",
            "Ya existe una instalación con ese código para este partner."
        ),
    ]