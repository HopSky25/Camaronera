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


    # Referencias hacia atras. No son cosmeticas: sin ellas la regla de acceso
    # no puede expresar "dejame leer al contacto de mi contraparte", y el
    # maquilador recibia un 403 al pintar el nombre del cliente en su lista de
    # ordenes.
    copack_request_ids = fields.One2many(
        "shrimp.copack.request", "client_partner_id", string="Solicitudes de empaque")
    copack_order_client_ids = fields.One2many(
        "shrimp.copack.order", "client_partner_id", string="Empaques contratados")
    copack_order_copacker_ids = fields.One2many(
        "shrimp.copack.order", "copacker_partner_id", string="Empaques realizados")

    # --- Tarifa referencial, PUBLICA ---
    # El cliente necesita saber por donde van los precios ANTES de pedir: nadie
    # contrata a ciegas y luego pregunta. Pero la tarifa real no puede ser
    # publica, porque el maquilador cobra distinto a quien le manda un
    # contenedor al ano que a quien le manda cinco al mes, y porque la verian
    # sus competidores.
    #
    # Por eso hay dos niveles: este "desde" sale en el directorio y sirve para
    # preseleccionar; la tarifa firme va dirigida y confidencial, en
    # shrimp.copack.tariff.
    pack_desde_entero = fields.Monetary(
        string="Desde, entero ($/lb)", currency_field="pack_currency_id")
    pack_desde_cola = fields.Monetary(
        string="Desde, cola ($/lb)", currency_field="pack_currency_id")
    pack_desde_valor_agregado = fields.Monetary(
        string="Desde, valor agregado ($/lb)", currency_field="pack_currency_id")
    pack_currency_id = fields.Many2one(
        "res.currency", string="Moneda de la tarifa",
        default=lambda self: self.env.company.currency_id)
    pack_tarifa_nota = fields.Char(
        string="Aclaración de la tarifa",
        help="Ej: «precios referenciales, la tarifa firme depende del volumen».")
    pack_lote_minimo_lb = fields.Float(
        string="Lote mínimo (lb)", digits=(16, 2),
        help="Por debajo de esto la planta no toma el trabajo.")

    pack_en_directorio = fields.Boolean(
        string="Aparecer en el directorio", default=True,
        help="Si se apaga, la planta deja de salir a quien busca servicio de empaque.")

    @api.constrains("pack_desde_entero", "pack_desde_cola",
                    "pack_desde_valor_agregado", "pack_lote_minimo_lb")
    def _check_tarifas_referenciales(self):
        for rec in self:
            for campo in ("pack_desde_entero", "pack_desde_cola",
                          "pack_desde_valor_agregado", "pack_lote_minimo_lb"):
                if (rec[campo] or 0.0) < 0:
                    raise ValidationError(_("Las tarifas y el lote mínimo no pueden ser negativos."))

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
