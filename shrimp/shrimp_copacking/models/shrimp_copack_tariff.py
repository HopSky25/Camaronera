from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpCopackTariff(models.Model):
    """La tarifa firme que un maquilador dirige a clientes concretos.

    No es la lista de precios de la empacadora y no reusa ese modelo: alli se
    cotiza lo que se PAGA por comprar camaron, con matriz de talla, calidad y
    canal. Aqui se cotiza lo que se COBRA por un servicio, siempre por libra
    empacada, y las calidades A/B/C o directa/sobrante no significan nada.

    Lo que si se copia de alli es lo que vale: la confidencialidad. Cada tarifa
    va dirigida a quien el maquilador elige, y nadie ve la del vecino. En este
    sector el precio por volumen es lo primero que se negocia y lo ultimo que
    se ensena.
    """

    _name = "shrimp.copack.tariff"
    _description = "Tarifa de servicio de empaque"
    _inherit = ["mail.thread", "shrimp.uuid.mixin"]
    _order = "valid_from desc, id desc"

    name = fields.Char(string="Nombre", required=True, default="Tarifa de empaque")
    copacker_partner_id = fields.Many2one(
        "res.partner", string="Maquilador", required=True, ondelete="cascade",
        index=True, tracking=True)
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)

    valid_from = fields.Date(string="Vigente desde", required=True,
                             default=fields.Date.context_today, tracking=True)
    valid_to = fields.Date(string="Vigente hasta", tracking=True)
    open_ended = fields.Boolean(string="Sin fecha de fin", default=True)

    # Lo que la hace confidencial. Mismo mecanismo que las listas de precios:
    # si el destinatario es la empresa madre de un grupo, la ven sus filiales.
    recipient_ids = fields.Many2many(
        "res.partner", "shrimp_copack_tariff_recipient_rel", "tariff_id", "partner_id",
        string="Dirigida a",
        help="Los clientes que pueden verla. Nadie mas la ve.")
    recipient_count = fields.Integer(compute="_compute_counts", string="Destinatarios")

    line_ids = fields.One2many(
        "shrimp.copack.tariff.line", "tariff_id", string="Renglones", copy=True)
    line_count = fields.Integer(compute="_compute_counts", string="Renglones")

    min_lot_lb = fields.Float(string="Lote mínimo (lb)", digits=(16, 2))
    payment_notes = fields.Char(string="Forma de pago")
    conditions = fields.Text(string="Condiciones del servicio")

    state = fields.Selection(
        [("draft", "Borrador"), ("published", "Publicada"), ("archived", "Archivada")],
        string="Estado", default="draft", required=True, index=True, tracking=True)

    is_current = fields.Boolean(compute="_compute_is_current", string="Vigente",
                                search="_search_is_current")

    @api.depends("line_ids", "recipient_ids")
    def _compute_counts(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.recipient_count = len(rec.recipient_ids)

    @api.depends("valid_from", "valid_to", "open_ended", "state")
    def _compute_is_current(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.is_current = bool(
                rec.state == "published"
                and rec.valid_from and rec.valid_from <= hoy
                and (rec.open_ended or not rec.valid_to or rec.valid_to >= hoy))

    def _search_is_current(self, operator, value):
        # El search de un booleano computado llega como 'in' con un conjunto;
        # dar por hecho un '=' invierte el filtro sin avisar.
        hoy = fields.Date.context_today(self)
        if operator in ("in", "not in"):
            verdadero = True in (value if isinstance(value, (list, tuple, set)) else [value])
            if operator == "not in":
                verdadero = not verdadero
        else:
            verdadero = bool(value) if operator == "=" else not value
        dom = ["&", ("state", "=", "published"), ("valid_from", "<=", hoy),
               "|", "|", ("open_ended", "=", True), ("valid_to", "=", False),
               ("valid_to", ">=", hoy)]
        return dom if verdadero else ["!"] + dom

    @api.constrains("copacker_partner_id", "recipient_ids")
    def _check_partes(self):
        for rec in self:
            if rec.copacker_partner_id.shrimp_user_type != "maquilador":
                raise ValidationError(_(
                    "La tarifa de empaque la publica un maquilador. «%s» no lo es.")
                    % (rec.copacker_partner_id.name or ""))
            ajenos = rec.recipient_ids.filtered(
                lambda p: p.shrimp_user_type not in ("camaronera", "empacadora"))
            if ajenos:
                raise ValidationError(_(
                    "El servicio de empaque va dirigido a quien es dueño del "
                    "camarón: camaroneras y empacadoras. No lo son: %s.")
                    % ", ".join(ajenos.mapped("name")))

    @api.constrains("valid_from", "valid_to", "open_ended")
    def _check_vigencia(self):
        for rec in self:
            if not rec.open_ended and not rec.valid_to:
                raise ValidationError(_(
                    "O la tarifa no tiene fecha de fin, o hay que indicarla."))
            if rec.valid_to and rec.valid_to < rec.valid_from:
                raise ValidationError(_("La vigencia no puede terminar antes de empezar."))

    @api.constrains("min_lot_lb")
    def _check_minimo(self):
        for rec in self:
            if rec.min_lot_lb and rec.min_lot_lb < 0:
                raise ValidationError(_("El lote mínimo no puede ser negativo."))

    def visible_para(self, partner):
        """Si el destinatario es la empresa madre, la ven tambien sus filiales."""
        self.ensure_one()
        if partner == self.copacker_partner_id:
            return True
        grupo = partner.shrimp_grupo_ids() if hasattr(partner, "shrimp_grupo_ids") \
            else [partner.id]
        return bool(set(grupo) & set(self.recipient_ids.ids))

    def action_publish(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_("Una tarifa sin renglones no se publica."))
            if not rec.recipient_ids:
                raise ValidationError(_(
                    "Hay que indicar a quién va dirigida: una tarifa que no ve "
                    "nadie no sirve de nada."))
            rec.state = "published"

    def action_archive_tariff(self):
        self.write({"state": "archived"})


class ShrimpCopackTariffLine(models.Model):
    """Un renglon de la tarifa: que se empaca, en que formato y desde cuantas libras.

    El tramo de volumen es lo que hace util a este modelo. En empaque el precio
    baja con la cantidad, y sin tramos habria que emitir una tarifa distinta
    por cada cliente y cada volumen.
    """

    _name = "shrimp.copack.tariff.line"
    _description = "Renglón de la tarifa de empaque"
    _order = "tariff_id, presentation, from_lb"

    tariff_id = fields.Many2one(
        "shrimp.copack.tariff", string="Tarifa", required=True,
        ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="tariff_id.currency_id", readonly=True)

    presentation = fields.Selection(
        [("entero", "Entero"), ("cola", "Cola"), ("valor_agregado", "Valor agregado")],
        string="Presentación", required=True, default="entero")
    pack_format = fields.Char(
        string="Formato de empaque", required=True,
        help="Ej: master 5 lb, bloque 2 kg, IQF granel.")
    from_lb = fields.Float(
        string="Desde (lb)", required=True, default=0.0, digits=(16, 2),
        help="Tramo de volumen a partir del cual aplica esta tarifa.")
    rate_per_lb = fields.Monetary(
        string="Tarifa por libra", required=True, currency_field="currency_id")

    _uniq_renglon = models.Constraint(
        "UNIQUE(tariff_id, presentation, pack_format, from_lb)",
        "Ya hay un renglón para esa presentación, formato y tramo.")

    @api.constrains("rate_per_lb", "from_lb")
    def _check_numeros(self):
        for rec in self:
            if rec.rate_per_lb <= 0:
                raise ValidationError(_("La tarifa por libra debe ser mayor que cero."))
            if rec.from_lb < 0:
                raise ValidationError(_("El tramo no puede empezar en negativo."))
