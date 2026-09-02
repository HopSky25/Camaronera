from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class ShrimpProduct(models.Model):
    _name = "shrimp.product"
    _description = "Productos"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]

    name = fields.Char(string="Nombre", required=True)
    seller_partner_id = fields.Many2one("res.partner", string="Vendedor", required=True, index=True)

    # species = fields.Char(string="Especie")

    # stage = fields.Selection(
    #     [
    #         ("nauplio", "Nauplio"),
    #         ("pl10", "PL10"),
    #         ("pl12", "PL12"),
    #         ("pl15", "PL15"),
    #         ("otro", "Otro"),
    #     ],
    #     default="pl12",
    #     ,
    # )

    # genetics_line = fields.Char(string="Línea Genética/Cepa")

    species_id = fields.Many2one(
        "shrimp.species",
        string="Especie",
        ondelete="restrict",
        index=True,
    )

    stage_id = fields.Many2one(
        "shrimp.stage",
        string="Estadío",
        ondelete="restrict",
        index=True,
    )

    genetics_line_id = fields.Many2one(
        "shrimp.genetics.line",
        string="Línea genética",
        ondelete="restrict",
        index=True,
    )
    avg_size_mg = fields.Float(string="Tamaño promedio (mg)")
    survival_rate = fields.Float(string="Supervivencia (%)")
    health_status = fields.Text(string="Estado sanitario / Observaciones")

    initial_qty = fields.Float(
        string="Cantidad inicial",
        required=True,
        default=0.0,
        help="Cantidad inicial publicada. Al crear el producto, esta cantidad genera el lote base.",
    )

    available_qty = fields.Float(
        string="Cantidad disponible",
        compute="_compute_available_qty",
        store=True,
    )

    check_request_ids = fields.One2many(
        "shrimp.check.request",
        "product_id",
        string="Solicitudes de chequeo",
    )

    reserved_qty = fields.Float(
        string="Cantidad reservada",
        compute="_compute_available_qty",
        store=True,
        help="Cantidad reservada por solicitudes de chequeo activas "
             "(solicitadas o en revisión).",
    )

    uom_id = fields.Many2one(
        "shrimp.uom",
        string="Unidad de medida",
        default=lambda self: self.env.ref("shrimp_marketplace.uom_libra", raise_if_not_found=False),
    )

    presentation = fields.Selection([
        ("entero", "Entero"),
        ("cola", "Cola"),
    ], string="Presentación", index=True)

    size_grade_id = fields.Many2one(
        "shrimp.size.grade",
        string="Talla",
        ondelete="restrict",
        index=True,
    )

    price = fields.Float(string="Precio", required=True, default=0.0)
    location = fields.Char(string="Ubicación")

    active = fields.Boolean(default=True)

    available_from = fields.Datetime(string="Disponible desde")
    available_to = fields.Datetime(string="Disponible hasta")

    cert_attachment_ids = fields.Many2many(
        "ir.attachment",
        "shrimp_prod_cert_rel",
        "product_id",
        "attachment_id",
        string="Certificados (archivos)",
        help="Campo heredado. La fuente principal de certificados debe ser certificate_line_ids.",
    )

    photo_attachment_ids = fields.Many2many(
        "ir.attachment",
        "shrimp_prod_photo_rel",
        "product_id",
        "attachment_id",
        string="Fotos",
    )

    state = fields.Selection(
        [("draft", "Borrador"), ("published", "Publicado"), ("sold", "Vendido"), ("cancel", "Cancelado")],
        string="Estado",
        default="draft",
        required=True,
        index=True,
    )

    published_date = fields.Datetime(string="Fecha de publicación", readonly=True, copy=False)

    seller_role = fields.Selection(
        [("semillero", "Semillero"), ("laboratorio", "Laboratorio")],
        string="Rol del vendedor",
        required=True,
        default="semillero",
        index=True,
    )

    expected_delivery_date = fields.Date(
        string="Fecha tentativa de entrega",
        help="Fecha estimada en la que el lote estaría listo para entrega.",
    )

    transaction_ids = fields.One2many(
        "shrimp.transaction",
        "product_id",
        string="Transacciones",
        readonly=True,
    )

    certificate_line_ids = fields.One2many(
        "shrimp.product.certificate.line",
        "product_id",
        string="Certificados (detalle)",
        copy=False,
    )

    # Certificados del vendedor (heredados por el producto), solo lectura.
    seller_certificate_line_ids = fields.One2many(
        related="seller_partner_id.certificate_line_ids",
        string="Certificados del vendedor",
        readonly=True,
    )

    stock_lot_ids = fields.One2many(
        "shrimp.stock.lot",
        "product_id",
        string="Lotes",
        readonly=True,
    )

    origin_facility_id = fields.Many2one(
        "shrimp.partner.facility",
        string="Instalación de origen",
        ondelete="set null",
        index=True,
    )

    origin_pond_id = fields.Many2one(
        "shrimp.partner.pond",
        string="Piscina de origen",
        ondelete="set null",
        index=True,
    )

    batch_code = fields.Char(
        string="Código de lote / producción",
    )

    production_date = fields.Date(
        string="Fecha de producción",
    )

    traceability_notes = fields.Text(
        string="Notas de trazabilidad",
    )

    evolution_ids = fields.One2many(
        "shrimp.product.evolution",
        "product_id",
        string="Histórico de evolución",
        readonly=True,
    )

    evolution_count = fields.Integer(
        string="Evoluciones",
        compute="_compute_evolution_count",
    )

    def price_for_partner(self, partner):
        """Precio efectivo para un comprador: su precio asignado si existe,
        si no, el precio de publicación."""
        self.ensure_one()
        if partner:
            cp = self.env["shrimp.client.price"].sudo().search([
                ("product_id", "=", self.id),
                ("client_partner_id", "=", partner.id),
                ("active", "=", True),
            ], limit=1)
            if cp:
                return cp.price
        return self.price

    def has_purchases(self):
        """True si el producto ya tiene transacciones de compra (confirmadas/hechas)."""
        self.ensure_one()
        return bool(self.env["shrimp.transaction"].sudo().search_count([
            ("product_id", "=", self.id),
            ("state", "in", ["confirmed", "done"]),
        ]))

    # Campos críticos que NO pueden cambiar una vez que el producto tiene compras.
    _LOCKED_AFTER_PURCHASE = {
        "species_id": "Especie",
        "stage_id": "Estadío",
        "genetics_line_id": "Línea genética",
        "presentation": "Presentación",
        "size_grade_id": "Talla",
        "uom_id": "Unidad de medida",
        "price": "Precio",
        "initial_qty": "Cantidad inicial",
        "seller_role": "Rol del vendedor",
    }

    @staticmethod
    def _field_value_changed(rec, fname, newval):
        """Compara el valor nuevo (tal como llega en vals) con el actual."""
        cur = rec[fname]
        if isinstance(cur, models.BaseModel):        # Many2one
            return cur.id != (newval or False)
        if isinstance(cur, float):                   # Float (precio, cantidad)
            return float_compare(cur, newval or 0.0, precision_digits=2) != 0
        return (cur or False) != (newval or False)   # Selection / Char

    def write(self, vals):
        # Restricción: campos críticos bloqueados si el producto ya tiene compras.
        present = set(self._LOCKED_AFTER_PURCHASE).intersection(vals.keys())
        if present:
            for rec in self:
                if not rec.has_purchases():
                    continue
                blocked = [f for f in present if self._field_value_changed(rec, f, vals[f])]
                if blocked:
                    labels = ", ".join(self._LOCKED_AFTER_PURCHASE[f] for f in blocked)
                    raise ValidationError(_(
                        "No puedes modificar estos campos de «%s» porque ya tiene "
                        "compras registradas: %s."
                    ) % (rec.name, labels))

        tracked_fields = {
            "stage_id",
            "avg_size_mg",
            "survival_rate",
            "health_status",
            "available_qty",
            "state",
        }

        create_snapshot = bool(tracked_fields.intersection(vals.keys()))

        result = super().write(vals)

        if create_snapshot and not self.env.context.get("skip_evolution_snapshot"):
            for rec in self:
                self.env["shrimp.product.evolution"].create({
                    "product_id": rec.id,
                    "stage_id": rec.stage_id.id if rec.stage_id else False,
                    "avg_size_mg": rec.avg_size_mg,
                    "survival_rate": rec.survival_rate,
                    "health_status": rec.health_status,
                    "available_qty": rec.available_qty,
                    "note": _("Actualización automática del producto."),
                })

        return result

    @api.depends("evolution_ids")
    def _compute_evolution_count(self):
        for rec in self:
            rec.evolution_count = len(rec.evolution_ids)

    stock_lot_count = fields.Integer(string="N.º de lotes", compute="_compute_stock_lot_count")
    transaction_count = fields.Integer(string="N.º de transacciones", compute="_compute_transaction_count")

    @api.depends("transaction_ids")
    def _compute_transaction_count(self):
        for rec in self:
            rec.transaction_count = len(rec.transaction_ids)

    @api.depends("stock_lot_ids")
    def _compute_stock_lot_count(self):
        for rec in self:
            rec.stock_lot_count = len(rec.stock_lot_ids)

    @api.depends("stock_lot_ids.available_qty", "stock_lot_ids.state", "stock_lot_ids.owner_id", "seller_partner_id",
                 "check_request_ids.qty", "check_request_ids.state")
    def _compute_available_qty(self):
        for rec in self:
            seller = rec.seller_partner_id
            lots = rec.stock_lot_ids.filtered(
                lambda l: l.owner_id == seller and l.state == "available" and l.available_qty > 0
            )
            on_hand = sum(lots.mapped("available_qty"))
            # Las solicitudes de chequeo activas reservan cantidad: se descuenta
            # del disponible para que no se venda dos veces.
            reserved = sum(rec.check_request_ids.filtered(
                lambda c: c.state in ("requested", "under_review")).mapped("qty"))
            rec.reserved_qty = reserved
            rec.available_qty = max(0.0, on_hand - reserved)

    @api.constrains("seller_partner_id")
    def _check_seller_type(self):
        for rec in self:
            if rec.seller_partner_id.shrimp_user_type not in ("semillero", "laboratorio", "camaronera"):
                raise ValidationError(_("Solo partners tipo Semillero, Laboratorio o Camaronera pueden publicar productos."))

    @api.constrains("initial_qty")
    def _check_initial_qty(self):
        for rec in self:
            if rec.initial_qty <= 0:
                raise ValidationError(_("La cantidad inicial debe ser mayor a 0."))

    @api.model_create_multi
    def create(self, vals_list):
        # Evita mensajes de chatter automáticos al crear (log de creación y
        # auto-suscripción) que generaban correos "vacíos"; el correo real
        # de "producto creado" se envía aparte con su plantilla.
        records = super(
            ShrimpProduct,
            self.with_context(mail_create_nolog=True, mail_create_nosubscribe=True, tracking_disable=True),
        ).create(vals_list)

        if self.env.context.get("skip_initial_lot"):
            return records

        for rec in records:
            rec._create_initial_lot_if_needed()

        return records


    @api.constrains("presentation", "size_grade_id")
    def _check_size_grade_presentation(self):
        for rec in self:
            if rec.size_grade_id and rec.presentation and rec.size_grade_id.presentation != rec.presentation:
                raise ValidationError(_("La talla seleccionada no corresponde a la presentación elegida."))

    @api.constrains("origin_facility_id", "origin_pond_id", "seller_partner_id")
    def _check_origin_facility_pond(self):
        for rec in self:
            if rec.origin_facility_id and rec.origin_facility_id.partner_id != rec.seller_partner_id:
                raise ValidationError(_("La instalación de origen debe pertenecer al mismo vendedor."))

            if rec.origin_pond_id and rec.origin_pond_id.partner_id != rec.seller_partner_id:
                raise ValidationError(_("La piscina de origen debe pertenecer al mismo vendedor."))

            if rec.origin_pond_id and rec.origin_facility_id and rec.origin_pond_id.facility_id != rec.origin_facility_id:
                raise ValidationError(_("La piscina seleccionada no pertenece a la instalación de origen."))

    def _create_initial_lot_if_needed(self):
        self.ensure_one()
        StockLot = self.env["shrimp.stock.lot"]

        has_lot = StockLot.search_count([
            ("product_id", "=", self.id),
            ("owner_id", "=", self.seller_partner_id.id),
        ])

        if not has_lot:
            StockLot.create({
                "product_id": self.id,
                "owner_id": self.seller_partner_id.id,
                "origin_move_id": False,
                "initial_qty": self.initial_qty,
                "available_qty": self.initial_qty,
                "uom_id": self.uom_id.id,
                "state": "available",
            })

    def action_view_transactions(self):
        self.ensure_one()
        action = self.env.ref("shrimp_marketplace.action_shrimp_transaction").read()[0]
        action["domain"] = [("product_id", "=", self.id)]
        action["context"] = {
            "default_product_id": self.id,
            "search_default_product_id": self.id,
        }
        return action

    def action_view_stock_lots(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Lotes"),
            "res_model": "shrimp.stock.lot",
            "view_mode": "list,form",
            "domain": [("product_id", "=", self.id)],
            "context": {
                "default_product_id": self.id,
                "search_default_product_id": self.id,
            },
        }

    def _get_tx_type_by_partners(self, seller_partner, buyer_partner):
        seller_type = seller_partner.shrimp_user_type
        buyer_type = buyer_partner.shrimp_user_type

        if seller_type == "semillero" and buyer_type == "laboratorio":
            return "semillero_to_laboratorio"

        if seller_type == "laboratorio" and buyer_type == "camaronera":
            return "laboratorio_to_camaronera"

        # Camarón adulto: la camaronera vende por libra a cualquier comprador registrado
        if seller_type == "camaronera":
            return "camaronera_to_buyer"

        raise ValidationError(
            _("No existe un tipo de transacción válido para vendedor '%s' y comprador '%s'.")
            % (seller_type, buyer_type)
        )

    def execute_purchase_flow(self, buyer_partner, qty):
        self.ensure_one()

        seller_partner = self.seller_partner_id

        if not self.active:
            raise ValidationError(_("El producto no está activo."))

        if self.state != "published":
            raise ValidationError(_("Solo se pueden comprar productos publicados."))

        if buyer_partner.id == seller_partner.id:
            raise ValidationError(_("No puedes comprar tu propio producto."))

        if qty <= 0:
            raise ValidationError(_("La cantidad debe ser mayor a 0."))

        if float_compare(qty, self.available_qty, precision_digits=6) == 1:
            raise ValidationError(_("La cantidad solicitada supera el stock disponible."))

        source_lots = self.env["shrimp.stock.lot"].search([
            ("product_id", "=", self.id),
            ("owner_id", "=", seller_partner.id),
            ("state", "=", "available"),
            ("available_qty", ">", 0),
        ], order="id asc")

        if not source_lots:
            raise ValidationError(_("Este producto no tiene lotes disponibles para la venta."))

        total_lot_qty = sum(source_lots.mapped("available_qty"))
        if float_compare(qty, total_lot_qty, precision_digits=6) == 1:
            raise ValidationError(_("El vendedor no tiene suficiente stock en sus lotes."))

        tx_type = self._get_tx_type_by_partners(seller_partner, buyer_partner)

        # Precio efectivo: usa el precio asignado al comprador si existe.
        unit_price = self.price_for_partner(buyer_partner)

        tx_vals = {
            "transaction_type": tx_type,
            "product_id": self.id,
            "seller_partner_id": seller_partner.id,
            "buyer_partner_id": buyer_partner.id,
            "location": self.location,
            "state": "draft",
            "transaction_qty": qty,
            "price_unit": unit_price,
            "amount_total": qty * unit_price,
        }

        if tx_type == "semillero_to_laboratorio":
            tx_vals.update({
                "sold_qty": qty,
                "sold_date": fields.Date.context_today(self),
                "production_note": self.name,
            })
        else:
            tx_vals.update({
                "desired_qty": qty,
                "desired_date": self.expected_delivery_date or fields.Date.context_today(self),
                "code": self.name,
            })

        tx = self.env["shrimp.transaction"].create(tx_vals)
        tx.action_confirm()

        buyer_new_product = tx.result_product_id if hasattr(tx, "result_product_id") else False

        source_lot_ids = source_lots.filtered(lambda l: l.available_qty > 0)[:1]

        return {
            "transaction": tx,
            "new_product": buyer_new_product,
            "source_lot_ids": source_lot_ids,
        }

    def _update_state_from_stock(self):
        for rec in self:
            if float_compare(rec.available_qty, 0.0, precision_digits=6) <= 0:
                rec.write({"state": "sold"})
            elif rec.state == "sold" and float_compare(rec.available_qty, 0.0, precision_digits=6) == 1:
                rec.write({"state": "published"})