from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpPartnerPond(models.Model):
    _name = "shrimp.partner.pond"
    _inherit = "shrimp.uuid.mixin"
    _description = "Piscinas / estanques del partner"
    _order = "name asc"

    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        required=True,
        ondelete="cascade",
        index=True,
    )

    facility_id = fields.Many2one(
        "shrimp.partner.facility",
        string="Instalación",
        ondelete="set null",
        index=True,
    )

    name = fields.Char(string="Nombre", required=True)
    code = fields.Char(string="Código")
    image = fields.Image(string="Foto", max_width=1024, max_height=1024)

    pond_type = fields.Selection([
        ("earth", "Piscina de tierra"),
        ("geomembrane", "Geomembrana"),
        ("tank", "Tanque"),
        ("raceway", "Canal"),
        ("other", "Otro"),
    ], string="Tipo", default="earth", required=True)

    location = fields.Char(string="Ubicación")
    active = fields.Boolean(default=True)

    capacity_mode = fields.Selection([
        ("volume", "Volumen directo"),
        ("dimensions", "Por dimensiones"),
    ], string="Modo de capacidad", default="dimensions", required=True)

    length_m = fields.Float(string="Largo (m)")
    width_m = fields.Float(string="Ancho (m)")
    depth_m = fields.Float(string="Profundidad (m)")

    manual_volume_m3 = fields.Float(string="Volumen manual (m3)")

    area_m2 = fields.Float(
        string="Área (m2)",
        compute="_compute_geometry",
        store=True,
    )

    volume_m3 = fields.Float(
        string="Volumen calculado/usable (m3)",
        compute="_compute_geometry",
        store=True,
    )

    usable_volume_m3 = fields.Float(
        string="Volumen útil (m3)",
        help="Si se desea, puede representar el volumen realmente utilizable.",
    )

    max_stock_units = fields.Float(
        string="Capacidad máxima estimada",
        help="Capacidad máxima estimada en unidades o millares, según tu regla de negocio.",
    )

    notes = fields.Text(string="Observaciones")

    lot_allocation_ids = fields.One2many(
        "shrimp.lot.allocation",
        "pond_id",
        string="Asignaciones de lotes",
    )

    lot_allocation_count = fields.Integer(
        string="Asignaciones",
        compute="_compute_lot_allocation_count",
    )

    @api.depends("capacity_mode", "length_m", "width_m", "depth_m", "manual_volume_m3")
    def _compute_geometry(self):
        for rec in self:
            area = 0.0
            volume = 0.0

            if rec.capacity_mode == "dimensions":
                area = (rec.length_m or 0.0) * (rec.width_m or 0.0)
                volume = area * (rec.depth_m or 0.0)
            else:
                area = (rec.length_m or 0.0) * (rec.width_m or 0.0)
                volume = rec.manual_volume_m3 or 0.0

            rec.area_m2 = area
            rec.volume_m3 = volume

    @api.depends("lot_allocation_ids")
    def _compute_lot_allocation_count(self):
        for rec in self:
            rec.lot_allocation_count = len(rec.lot_allocation_ids)

    @api.constrains("facility_id", "partner_id")
    def _check_facility_partner(self):
        for rec in self:
            if rec.facility_id and rec.facility_id.partner_id != rec.partner_id:
                raise ValidationError(_("La instalación seleccionada no pertenece al mismo partner."))

    @api.constrains("length_m", "width_m", "depth_m", "manual_volume_m3", "usable_volume_m3", "max_stock_units")
    def _check_positive_values(self):
        for rec in self:
            if rec.length_m < 0:
                raise ValidationError(_("El largo no puede ser negativo."))
            if rec.width_m < 0:
                raise ValidationError(_("El ancho no puede ser negativo."))
            if rec.depth_m < 0:
                raise ValidationError(_("La profundidad no puede ser negativa."))
            if rec.manual_volume_m3 < 0:
                raise ValidationError(_("El volumen manual no puede ser negativo."))
            if rec.usable_volume_m3 < 0:
                raise ValidationError(_("El volumen útil no puede ser negativo."))
            if rec.max_stock_units < 0:
                raise ValidationError(_("La capacidad máxima estimada no puede ser negativa."))

    _sql_constraints = [
        (
            "shrimp_partner_pond_partner_code_unique",
            "unique(partner_id, code)",
            "Ya existe una piscina con ese código para este partner."
        ),
    ]

    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            label = rec.name or ""
            if rec.code:
                label = f"[{rec.code}] {label}"
            rec.display_name = label