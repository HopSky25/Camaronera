from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    # El maquilador no compra ni vende camaron: vende capacidad de empaque y,
    # sobre todo, su habilitacion. Lo que el cliente alquila de verdad es el
    # codigo de establecimiento, porque sin el su producto no sale del pais.
    shrimp_user_type = fields.Selection(
        selection_add=[("maquilador", "Maquilador (empaque)")],
        ondelete={"maquilador": "set null"},
    )

    pack_razon_social = fields.Char(string="Razón social (Maquilador)")
    pack_representante = fields.Char(string="Representante legal")
    pack_telefono = fields.Char(string="Teléfono (Maquilador)")
    pack_ubicacion = fields.Char(string="Ubicación de la planta")

    # Esto es lo que da valor de exportacion al certificado: el comprador final
    # escanea el QR y ve en que planta habilitada se empaco su camaron.
    pack_codigo_establecimiento = fields.Char(
        string="Código de establecimiento",
        help="El que consta en el listado oficial de plantas habilitadas para "
             "exportar. Sale impreso en el certificado de trazabilidad.")
    pack_habilitacion_desde = fields.Date(string="Habilitación desde")
    pack_habilitacion_hasta = fields.Date(string="Habilitación hasta")

    # Sin esto la bandeja de solicitudes se llena de trabajos que la planta no
    # puede tomar, y en dos semanas deja de mirarla.
    pack_capacidad_lb_semana = fields.Float(
        string="Capacidad (lb/semana)", digits=(16, 2),
        help="Libras que la planta puede empacar en una semana normal.")
    pack_presentaciones = fields.Char(
        string="Presentaciones que maneja",
        help="Texto libre: entero, cola, valor agregado, etc.")

    pack_habilitacion_vigente = fields.Boolean(
        string="Habilitación vigente", compute="_compute_pack_habilitacion_vigente")

    @api.depends("pack_habilitacion_hasta")
    def _compute_pack_habilitacion_vigente(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.pack_habilitacion_vigente = bool(
                rec.pack_habilitacion_hasta and rec.pack_habilitacion_hasta >= hoy)

    @api.constrains("shrimp_user_type", "pack_razon_social", "pack_ubicacion")
    def _check_datos_maquilador(self):
        for rec in self:
            if rec.shrimp_user_type != "maquilador":
                continue
            if not rec.pack_razon_social:
                raise ValidationError(_("La razón social del maquilador es obligatoria."))
            if not rec.pack_ubicacion:
                raise ValidationError(_("La ubicación de la planta es obligatoria."))

    @api.constrains("pack_habilitacion_desde", "pack_habilitacion_hasta")
    def _check_habilitacion(self):
        for rec in self:
            if (rec.pack_habilitacion_desde and rec.pack_habilitacion_hasta
                    and rec.pack_habilitacion_hasta < rec.pack_habilitacion_desde):
                raise ValidationError(_(
                    "La habilitación no puede terminar antes de empezar."))

    @api.constrains("pack_capacidad_lb_semana")
    def _check_capacidad(self):
        for rec in self:
            if rec.pack_capacidad_lb_semana and rec.pack_capacidad_lb_semana < 0:
                raise ValidationError(_("La capacidad no puede ser negativa."))
