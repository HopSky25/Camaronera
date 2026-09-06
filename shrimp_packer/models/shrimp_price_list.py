from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpPriceList(models.Model):
    """Lista de precios que un comprador publica a sus proveedores.

    Es el documento que las empacadoras reparten cada semana a las camaroneras:
    "esto es lo que pago por talla desde tal fecha". No fija el precio de
    ninguna compra —eso se sigue pactando en la operación— sino que le dice al
    productor a cuánto le pagan antes de ofrecer.

    Por eso lleva ventana de despacho: un precio de camarón sin fecha no vale
    nada, se mueve semana a semana.
    """

    _name = "shrimp.price.list"
    _description = "Lista de precios de compra"
    _inherit = ["shrimp.uuid.mixin", "mail.thread"]
    _order = "issue_date desc, id desc"

    name = fields.Char(
        string="Referencia", required=True, tracking=True,
        help="Como la identifica el comprador: «Semana 32», «Lista del 5 de agosto»...")

    issuer_partner_id = fields.Many2one(
        "res.partner", string="Publica", required=True, index=True,
        ondelete="cascade", tracking=True,
        help="Quien compra y publica lo que va a pagar.")

    issue_date = fields.Date(
        string="Fecha de emisión", required=True, tracking=True,
        default=fields.Date.context_today)

    # La ventana de despacho es lo que distingue una lista viva de un papel
    # viejo. "Hasta segunda orden" es literal en las listas del sector: rige
    # hasta que el comprador publique otra.
    dispatch_from = fields.Date(string="Despacho desde", tracking=True)
    dispatch_to = fields.Date(string="Despacho hasta", tracking=True)
    open_ended = fields.Boolean(
        string="Hasta segunda orden", default=True, tracking=True,
        help="Rige sin fecha de fin, hasta que se publique otra lista.")

    state = fields.Selection(
        [("draft", "Borrador"), ("published", "Publicada"), ("archived", "Archivada")],
        string="Estado", default="draft", required=True, index=True, tracking=True)

    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)

    quality_conditions = fields.Text(
        string="Condiciones de calidad",
        help="Lo que el comprador no acepta: branquias sucias, sabores, "
             "picados, hongos, color amarillo o mezclado...")

    # Forma de pago en campos y no en un texto libre: es lo que el productor
    # compara entre compradores, y así se puede ordenar y filtrar.
    advance_pct = fields.Float(string="Anticipo (%)", digits=(5, 2))
    advance_days = fields.Integer(string="Días para el anticipo")
    balance_days = fields.Integer(string="Días para el saldo")
    payment_notes = fields.Char(string="Nota de pago")

    # A quién va dirigida. Es lo que hace confidencial a la lista: en el sector
    # cada productor recibe su propio precio, y ver el del vecino sería un
    # problema comercial. Si el destinatario es la empresa madre de un grupo,
    # la ven también sus camaroneras.
    recipient_ids = fields.Many2many(
        "res.partner", "shrimp_price_list_recipient_rel", "list_id", "partner_id",
        string="Dirigida a",
        help="Los productores que pueden verla. Si eliges la empresa madre de "
             "un grupo, la ven todas sus camaroneras.")
    recipient_count = fields.Integer(
        compute="_compute_counts", string="Destinatarios")

    line_ids = fields.One2many(
        "shrimp.price.list.line", "price_list_id", string="Precios por talla")
    bonus_ids = fields.One2many(
        "shrimp.price.list.bonus", "price_list_id", string="Bonificaciones")

    line_count = fields.Integer(compute="_compute_counts", string="Renglones")
    is_current = fields.Boolean(
        compute="_compute_is_current", search="_search_is_current",
        string="Vigente")

    @api.depends("line_ids", "recipient_ids")
    def _compute_counts(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.recipient_count = len(rec.recipient_ids)

    def visible_para(self, partner):
        """¿Este partner puede ver la lista?

        La ve el que la emite, y la ve el destinatario —o cualquier camaronera
        que cuelgue de un destinatario, para cubrir el caso del grupo con
        varias razones sociales.
        """
        self.ensure_one()
        if not partner:
            return False
        if partner == self.issuer_partner_id:
            return True
        if self.state != "published":
            return False
        return bool(set(partner.shrimp_grupo_ids()) & set(self.recipient_ids.ids))

    @api.model
    def visibles_para(self, partner):
        """Las listas publicadas y vigentes que le tocan a este partner."""
        if not partner:
            return self.browse()
        return self.sudo().search([
            ("state", "=", "published"),
            ("recipient_ids", "in", partner.shrimp_grupo_ids()),
        ]).filtered("is_current")

    @api.depends("state", "dispatch_from", "dispatch_to", "open_ended")
    def _compute_is_current(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            if rec.state != "published":
                rec.is_current = False
                continue
            if rec.dispatch_from and rec.dispatch_from > hoy:
                rec.is_current = False
                continue
            if not rec.open_ended and rec.dispatch_to and rec.dispatch_to < hoy:
                rec.is_current = False
                continue
            rec.is_current = True

    def _search_is_current(self, operator, value):
        # Odoo 19 normaliza los dominios de booleano a operator 'in' con un
        # conjunto: dar por hecho '=' invierte el filtro en silencio.
        if operator in ("in", "not in"):
            valores = set(value) if not isinstance(value, bool) else {value}
            quiere = True in valores
            if operator == "not in":
                quiere = not quiere
        else:
            quiere = bool(value) if operator == "=" else not bool(value)
        hoy = fields.Date.context_today(self)
        vigentes = self.search([("state", "=", "published")]).filtered(
            lambda r: (not r.dispatch_from or r.dispatch_from <= hoy)
            and (r.open_ended or not r.dispatch_to or r.dispatch_to >= hoy))
        return [("id", "in" if quiere else "not in", vigentes.ids)]

    @api.constrains("dispatch_from", "dispatch_to", "open_ended")
    def _check_ventana(self):
        for rec in self:
            if (not rec.open_ended and rec.dispatch_from and rec.dispatch_to
                    and rec.dispatch_to < rec.dispatch_from):
                raise ValidationError(_(
                    "La fecha de fin del despacho es anterior a la de inicio."))

    # ==================================================================
    # Acciones
    # ==================================================================
    def action_publish(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_(
                    "Una lista sin precios no se puede publicar. Agrega al menos "
                    "un renglón de talla."))
            if not rec.recipient_ids:
                raise ValidationError(_(
                    "Indica a qué productores va dirigida. Una lista sin "
                    "destinatarios no la vería nadie."))
            # Solo una lista vigente por comprador: si no, el productor no sabe
            # cuál mirar. La anterior se archiva sola.
            # Solo se archivan las que compiten con esta: mismo emisor y algún
            # destinatario en común. Una empacadora tiene varias listas vivas a
            # la vez, una por cliente, y archivarlas todas sería un desastre.
            anteriores = self.search([
                ("issuer_partner_id", "=", rec.issuer_partner_id.id),
                ("state", "=", "published"),
                ("id", "!=", rec.id),
                ("recipient_ids", "in", rec.recipient_ids.ids),
            ])
            anteriores.write({"state": "archived"})
            rec.state = "published"
            if anteriores:
                rec.message_post(body=_(
                    "Publicada. Se archivaron %s lista(s) anterior(es).") % len(anteriores))
        return True

    def action_archive_list(self):
        self.write({"state": "archived"})
        return True

    def action_back_to_draft(self):
        self.write({"state": "draft"})
        return True

    def action_duplicate_for_next(self):
        """Copia la lista para la semana siguiente: es como se trabaja de verdad,
        se parte de la anterior y se mueven dos o tres renglones."""
        self.ensure_one()
        nueva = self.copy({
            "name": _("%s (copia)") % self.name,
            "state": "draft",
            "issue_date": fields.Date.context_today(self),
            "dispatch_from": False,
            "dispatch_to": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "shrimp.price.list",
            "res_id": nueva.id,
            "view_mode": "form",
            "target": "current",
        }

    # ==================================================================
    # Lectura para el portal
    # ==================================================================
    def matriz(self, presentation):
        """Devuelve la matriz de una presentación lista para pintar.

        {"tallas": [...], "columnas": [(clave, etiqueta), ...],
         "precios": {talla: {clave: precio}}, "uom": "kg"|"lb"}

        Se arma aquí y no en la plantilla porque las dos presentaciones tienen
        columnas distintas: el entero se abre por calidad (A-B y C) y la cola
        además por canal (directa y sobrante).
        """
        self.ensure_one()
        lineas = self.line_ids.filtered(
            lambda l: l.size_grade_id.presentation == presentation)
        if not lineas:
            return {}

        columnas = []
        for linea in lineas.sorted(key=lambda l: (l.channel or "", l.quality)):
            clave = (linea.channel or "", linea.quality)
            if clave not in [c[0] for c in columnas]:
                columnas.append((clave, linea.etiqueta_columna()))

        precios = {}
        for linea in lineas:
            precios.setdefault(linea.size_grade_id.id, {})[
                (linea.channel or "", linea.quality)] = linea.price

        tallas = lineas.mapped("size_grade_id").sorted(
            key=lambda t: (t.sequence, t.name))
        return {
            "tallas": tallas,
            "columnas": columnas,
            "precios": precios,
            "uom": lineas[0].uom,
        }

    def texto_ventana(self):
        """La vigencia en una línea, como la escriben en las listas reales."""
        self.ensure_one()
        def f(d):
            return d.strftime("%d/%m/%Y") if d else ""
        if self.dispatch_from and not self.open_ended and self.dispatch_to:
            return _("Despacho del %(desde)s al %(hasta)s") % {
                "desde": f(self.dispatch_from), "hasta": f(self.dispatch_to)}
        if self.dispatch_from:
            return _("Despacho desde el %s, hasta segunda orden") % f(self.dispatch_from)
        return _("Vigente hasta segunda orden")

    def texto_pago(self):
        self.ensure_one()
        partes = []
        if self.advance_pct:
            t = _("Anticipo del %s%%") % ("{:.0f}".format(self.advance_pct))
            if self.advance_days:
                t += _(", a cancelar dentro de %s días calendarios") % self.advance_days
            partes.append(t)
        if self.balance_days:
            partes.append(_(
                "Saldo pendiente a liquidar dentro de %s días calendarios desde "
                "la recepción de la factura") % self.balance_days)
        if self.payment_notes:
            partes.append(self.payment_notes)
        return partes


class ShrimpPriceListLine(models.Model):
    """Un renglón de la matriz: talla × canal × calidad = precio."""

    _name = "shrimp.price.list.line"
    _description = "Precio por talla"
    _order = "price_list_id, size_grade_id"

    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista", required=True,
        ondelete="cascade", index=True)
    currency_id = fields.Many2one(
        related="price_list_id.currency_id", readonly=True)

    size_grade_id = fields.Many2one(
        "shrimp.size.grade", string="Talla", required=True,
        ondelete="restrict", index=True)
    presentation = fields.Selection(
        related="size_grade_id.presentation", string="Presentación",
        store=True, readonly=True)

    # El canal solo aplica a la cola. En las listas del sector la misma talla
    # vale muy distinto según entre directa o como sobrante de clasificar el
    # entero: en la lista de AQUAGOLD, la U/12 directa A son $3,00 y la
    # sobrante A $1,75.
    channel = fields.Selection(
        [("directa", "Directa"), ("sobrante", "Sobrante")],
        string="Canal",
        help="Solo para cola. Directa: el lote se procesa como cola desde el "
             "inicio. Sobrante: lo que queda tras clasificar el entero.")

    quality = fields.Selection(
        [("ab", "A - B"), ("a", "A"), ("b", "B"), ("c", "C")],
        string="Calidad", required=True, default="a")

    # El entero se cotiza por kilo y la cola por libra. Mezclarlas es un error
    # de 2,2 veces, así que la unidad viaja en cada renglón.
    uom = fields.Selection(
        [("kg", "$ / Kg"), ("lb", "$ / Lb")],
        string="Unidad", required=True, default="lb")

    price = fields.Monetary(string="Precio", required=True, currency_field="currency_id")

    _uniq_renglon = models.Constraint(
        "UNIQUE(price_list_id, size_grade_id, channel, quality)",
        "Esa combinación de talla, canal y calidad ya está en la lista.",
    )

    @api.constrains("price")
    def _check_price(self):
        for rec in self:
            if rec.price < 0:
                raise ValidationError(_("El precio no puede ser negativo."))

    @api.constrains("channel", "size_grade_id")
    def _check_channel(self):
        for rec in self:
            if rec.presentation == "entero" and rec.channel:
                raise ValidationError(_(
                    "El canal directa/sobrante solo tiene sentido en cola: el "
                    "entero no se clasifica así."))
            if rec.presentation == "cola" and not rec.channel:
                raise ValidationError(_(
                    "En cola hay que indicar si el precio es directa o sobrante."))

    @api.onchange("size_grade_id")
    def _onchange_size_grade(self):
        """Los valores por defecto de cada presentación, como en las listas
        reales: el entero por kilo y calidad A-B, la cola por libra y directa."""
        if self.presentation == "entero":
            self.uom = "kg"
            self.channel = False
            if self.quality not in ("ab", "c"):
                self.quality = "ab"
        elif self.presentation == "cola":
            self.uom = "lb"
            if not self.channel:
                self.channel = "directa"
            if self.quality not in ("a", "b"):
                self.quality = "a"

    def etiqueta_columna(self):
        self.ensure_one()
        cal = dict(self._fields["quality"]._description_selection(self.env))
        if self.channel:
            can = dict(self._fields["channel"]._description_selection(self.env))
            return "%s %s" % (can.get(self.channel, ""), cal.get(self.quality, ""))
        return cal.get(self.quality, "")


class ShrimpPriceListBonus(models.Model):
    """Bonificación que se suma al precio del renglón.

    En las listas del sector van en una tabla aparte al pie: SMALL, MEDIUM,
    LARGE, LOCAL, ROJO. No dependen de la talla sino de una condición del lote.
    """

    _name = "shrimp.price.list.bonus"
    _description = "Bonificación de la lista de precios"
    _order = "price_list_id, sequence, id"

    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista", required=True,
        ondelete="cascade", index=True)
    currency_id = fields.Many2one(
        related="price_list_id.currency_id", readonly=True)

    name = fields.Char(string="Concepto", required=True)
    amount = fields.Monetary(string="Importe", currency_field="currency_id")
    note = fields.Char(string="Detalle")
    sequence = fields.Integer(default=10)
