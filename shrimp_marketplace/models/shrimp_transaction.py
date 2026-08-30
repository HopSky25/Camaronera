import base64

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero


class ShrimpTransaction(models.Model):
    _name = "shrimp.transaction"
    _description = "Transacciones Shrimp"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Referencia",
        required=True,
        copy=False,
        default=lambda self: _("Nuevo"),
        tracking=True,
    )

    state = fields.Selection(
        [("draft", "Borrador"), ("confirmed", "Confirmada"), ("done", "Completada"), ("cancel", "Cancelada")],
        default="draft",
        tracking=True,
    )

    transaction_type = fields.Selection(
        [
            ("semillero_to_laboratorio", "Semillero → Laboratorio"),
            ("laboratorio_to_camaronera", "Laboratorio → Camaronera"),
            ("camaronera_to_buyer", "Camaronera → Comprador"),
        ],
        required=True,
        tracking=True,
    )

    product_id = fields.Many2one("shrimp.product", string="Producto", required=True)
    seller_partner_id = fields.Many2one("res.partner", required=True, tracking=True)
    buyer_partner_id = fields.Many2one("res.partner", required=True, tracking=True)

    location = fields.Char(string="Ubicación", tracking=True)

    invoice_attachment_ids = fields.Many2many(
        "ir.attachment", "shrimp_tx_invoice_rel", "tx_id", "attachment_id", string="Factura(s)"
    )

    production_note = fields.Char(string="Producción / Lote")
    sold_qty = fields.Float(string="Cantidad vendida")
    sold_date = fields.Date(string="Fecha de venta")

    desired_qty = fields.Float(string="Cantidad deseada")
    desired_date = fields.Date(string="Fecha")
    code = fields.Char(string="Código")

    transaction_qty = fields.Float(string="Cantidad operativa", required=True, tracking=True)

    # Precio congelado al momento de la operación (el precio del producto puede
    # cambiar después, por eso se guarda aquí).
    price_unit = fields.Float(string="Precio unitario", tracking=True)
    amount_total = fields.Float(string="Total pagado", tracking=True)

    stock_move_ids = fields.One2many(
        "shrimp.stock.move",
        "transaction_id",
        string="Movimientos de stock",
    )

    available_qty_seller = fields.Float(
        compute="_compute_available_qty_seller",
        string="Stock disponible vendedor"
    )

    result_product_id = fields.Many2one(
        "shrimp.product",
        string="Producto resultado",
        readonly=True,
        ondelete="set null",
    )

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]

        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = seq.next_by_code("shrimp.transaction") or _("TXN-000000")

        return super().create(vals_list)

    @api.depends("seller_partner_id", "product_id")
    def _compute_available_qty_seller(self):
        for rec in self:
            if not rec.seller_partner_id or not rec.product_id:
                rec.available_qty_seller = 0.0
                continue

            lots = self.env["shrimp.stock.lot"].search([
                ("owner_id", "=", rec.seller_partner_id.id),
                ("product_id", "=", rec.product_id.id),
                ("state", "=", "available"),
                ("available_qty", ">", 0),
            ])
            rec.available_qty_seller = sum(lots.mapped("available_qty"))

    @api.constrains("transaction_type", "seller_partner_id", "buyer_partner_id")
    def _check_types(self):
        for rec in self:
            if rec.transaction_type == "semillero_to_laboratorio":
                if rec.seller_partner_id.shrimp_user_type != "semillero":
                    raise ValidationError(_("El vendedor debe ser Semillero."))
                if rec.buyer_partner_id.shrimp_user_type != "laboratorio":
                    raise ValidationError(_("El comprador debe ser Laboratorio."))

            if rec.transaction_type == "laboratorio_to_camaronera":
                if rec.seller_partner_id.shrimp_user_type != "laboratorio":
                    raise ValidationError(_("El vendedor debe ser Laboratorio."))
                if rec.buyer_partner_id.shrimp_user_type != "camaronera":
                    raise ValidationError(_("El comprador debe ser Camaronera."))

    @api.constrains("seller_partner_id", "buyer_partner_id")
    def _check_partners_not_equal(self):
        for rec in self:
            if rec.seller_partner_id == rec.buyer_partner_id:
                raise ValidationError(_("El vendedor y comprador no pueden ser el mismo."))

    @api.constrains("transaction_qty")
    def _check_transaction_qty(self):
        for rec in self:
            if rec.transaction_qty <= 0:
                raise ValidationError(_("La cantidad operativa debe ser mayor a 0."))

    @api.constrains("transaction_type", "sold_qty", "desired_qty")
    def _check_qty_by_type(self):
        for rec in self:
            if rec.transaction_type == "semillero_to_laboratorio" and rec.sold_qty <= 0:
                raise ValidationError(_("La cantidad vendida debe ser mayor a 0."))

            if rec.transaction_type == "laboratorio_to_camaronera" and rec.desired_qty <= 0:
                raise ValidationError(_("La cantidad deseada debe ser mayor a 0."))

    @api.constrains("transaction_qty", "seller_partner_id", "product_id")
    def _check_stock_available(self):
        for rec in self:
            if rec.state != "draft":
                continue
            if float_compare(rec.transaction_qty, rec.available_qty_seller, precision_digits=6) == 1:
                raise ValidationError(_("No hay suficiente stock disponible."))

    def _get_transaction_qty(self):
        self.ensure_one()
        return self.transaction_qty

    def _get_source_lots(self):
        self.ensure_one()
        return self.env["shrimp.stock.lot"].search([
            ("owner_id", "=", self.seller_partner_id.id),
            ("product_id", "=", self.product_id.id),
            ("state", "=", "available"),
            ("available_qty", ">", 0),
        ], order="create_date asc, id asc")

    def _consume_source_lots(self):
        self.ensure_one()

        lots = self._get_source_lots()
        qty_to_consume = self._get_transaction_qty()
        consumed = []

        for lot in lots:
            if float_is_zero(qty_to_consume, precision_digits=6):
                break

            take_qty = min(lot.available_qty, qty_to_consume)
            new_qty = lot.available_qty - take_qty

            lot.write({
                "available_qty": new_qty,
                "state": "consumed" if float_is_zero(new_qty, precision_digits=6) else "available",
            })

            consumed.append((lot, take_qty))
            qty_to_consume -= take_qty

        if not float_is_zero(qty_to_consume, precision_digits=6):
            raise ValidationError(_("No fue posible consumir toda la cantidad solicitada desde los lotes disponibles."))

        return consumed

    def _create_stock_moves(self, consumed_lots):
        self.ensure_one()
        move_model = self.env["shrimp.stock.move"]
        moves = self.env["shrimp.stock.move"]

        for lot, take_qty in consumed_lots:
            move = move_model.create({
                "product_id": self.product_id.id,
                "source_partner_id": self.seller_partner_id.id,
                "dest_partner_id": self.buyer_partner_id.id,
                "qty": take_qty,
                "parent_move_id": lot.origin_move_id.id if lot.origin_move_id else False,
                "transaction_id": self.id,
                "date": fields.Datetime.now(),
            })
            moves |= move

        return moves

    def _buyer_can_republish(self):
        self.ensure_one()
        # El camarón adulto es producto final: el comprador no lo revende.
        if self.transaction_type == "camaronera_to_buyer":
            return False
        return self.buyer_partner_id.shrimp_user_type in ("semillero", "laboratorio")

    def _prepare_new_product_vals(self):
        self.ensure_one()
        product = self.product_id
        buyer = self.buyer_partner_id
        qty = self._get_transaction_qty()

        return {
            "name": product.name,
            "seller_partner_id": buyer.id,
            "species_id": product.species_id.id if product.species_id else False,
            "stage_id": product.stage_id.id if product.stage_id else False,
            "genetics_line_id": product.genetics_line_id.id if product.genetics_line_id else False,
            "avg_size_mg": product.avg_size_mg,
            "survival_rate": product.survival_rate,
            "health_status": product.health_status,
            "initial_qty": qty,
            "uom_id": product.uom_id.id,
            "price": product.price,
            "location": product.location,
            "active": True,
            "available_from": product.available_from,
            "available_to": product.available_to,
            "state": "draft",
            "seller_role": "laboratorio" if buyer.shrimp_user_type == "laboratorio" else product.seller_role,
            "expected_delivery_date": product.expected_delivery_date,
            "photo_attachment_ids": [(6, 0, product.photo_attachment_ids.ids)],
        }
    
    def _copy_product_certificates(self, new_product):
        self.ensure_one()
        for cert in self.product_id.certificate_line_ids:
            self.env["shrimp.product.certificate.line"].create({
                "product_id": new_product.id,
                "source_user_certificate_line_id": cert.source_user_certificate_line_id.id if cert.source_user_certificate_line_id else False,
                "certificate_id": cert.certificate_id.id,
                "number": cert.number,
                "issue_date": cert.issue_date,
                "expiry_date": cert.expiry_date,
                "attachment_id": cert.attachment_id.id,
                "active": cert.active,
            })

    def _create_buyer_side_records(self, moves):
        self.ensure_one()

        StockLot = self.env["shrimp.stock.lot"]
        new_product = False

        if self._buyer_can_republish():
            new_product = self.env["shrimp.product"].with_context(skip_initial_lot=True).create(
                self._prepare_new_product_vals()
            )
            self._copy_product_certificates(new_product)

            for move in moves:
                StockLot.create({
                    "product_id": new_product.id,
                    "owner_id": self.buyer_partner_id.id,
                    "origin_move_id": move.id,
                    "initial_qty": move.qty,
                    "available_qty": move.qty,
                    "uom_id": new_product.uom_id.id,
                    "state": "available",
                })

            self.result_product_id = new_product.id
        else:
            for move in moves:
                StockLot.create({
                    "product_id": self.product_id.id,
                    "owner_id": self.buyer_partner_id.id,
                    "origin_move_id": move.id,
                    "initial_qty": move.qty,
                    "available_qty": move.qty,
                    "uom_id": self.product_id.uom_id.id,
                    "state": "available",
                })

        return new_product

    def action_confirm(self):
        for rec in self:
            if rec.state != "draft":
                continue

            consumed_lots = rec._consume_source_lots()
            moves = rec._create_stock_moves(consumed_lots)
            rec._create_buyer_side_records(moves)

            rec.state = "confirmed"

            rec.product_id._compute_available_qty()
            rec.product_id._update_state_from_stock()

    def get_full_traceability_data(self):
        self.ensure_one()

        StockLot = self.env["shrimp.stock.lot"].sudo()
        Allocation = self.env["shrimp.lot.allocation"].sudo()
        Evolution = self.env["shrimp.product.evolution"].sudo()

        moves = self.stock_move_ids.sorted(lambda m: m.date or m.create_date)

        all_moves = self.env["shrimp.stock.move"].sudo()

        def collect_parent_moves(move):
            chain = self.env["shrimp.stock.move"].sudo()
            current = move
            while current:
                chain |= current
                current = current.parent_move_id
            return chain

        for move in moves:
            all_moves |= collect_parent_moves(move)

        # Ordenar siguiendo la cadena padre->hijo (topológico), NO por fecha:
        # el eslabón origen (raíz, sin padre) va primero, aunque su fecha sea
        # posterior por incoherencias en los datos.
        def _move_depth(move):
            depth, cur, seen = 0, move.parent_move_id, set()
            while cur and cur.id not in seen:
                seen.add(cur.id)
                depth += 1
                cur = cur.parent_move_id
            return depth

        all_moves = all_moves.sorted(
            key=lambda m: (_move_depth(m), m.date or m.create_date))

        partner_ids = []
        product_ids = []

        for move in all_moves:
            if move.source_partner_id:
                partner_ids.append(move.source_partner_id.id)
            if move.dest_partner_id:
                partner_ids.append(move.dest_partner_id.id)
            if move.product_id:
                product_ids.append(move.product_id.id)

        # Fallback: incluir el producto y los partners de la propia transacción.
        # Así las ventas directas (sin cadena de movimientos) muestran igualmente
        # el producto, sus lotes y su evolución productiva.
        if self.product_id:
            product_ids.append(self.product_id.id)
        if self.seller_partner_id:
            partner_ids.append(self.seller_partner_id.id)
        if self.buyer_partner_id:
            partner_ids.append(self.buyer_partner_id.id)

        partner_ids = list(set(partner_ids))
        product_ids = list(set(product_ids))

        lots = StockLot.search([
            "|",
            ("origin_move_id", "in", all_moves.ids),
            "&",
            ("product_id", "in", product_ids),
            ("owner_id", "in", partner_ids),
        ])

        allocations = Allocation.search([
            ("stock_lot_id", "in", lots.ids),
        ], order="allocation_date asc, id asc")

        evolutions = Evolution.search([
            ("product_id", "in", product_ids),
        ], order="date asc, id asc")

        return {
            "moves": all_moves,
            "lots": lots,
            "allocations": allocations,
            "evolutions": evolutions,
        }

    def traceability_chain(self):
        """Secuencia ordenada de partners en la cadena de custodia (para el gráfico).
        Devuelve [{'name':.., 'role':..}] desde el origen hasta el destino final."""
        self.ensure_one()
        data = self.get_full_traceability_data()
        seq = []
        for m in data["moves"]:
            for p in (m.source_partner_id, m.dest_partner_id):
                if p and (not seq or seq[-1].id != p.id):
                    seq.append(p)
        role_sel = dict(self.env["res.partner"]._fields["shrimp_user_type"].selection)
        return [{"name": p.name or "—", "role": role_sel.get(p.shrimp_user_type) or "Productor"} for p in seq]

    def _line_chart_svg(self, title, points, color, suffix=""):
        """Genera un gráfico de líneas SVG (inline) a partir de (etiqueta, valor)."""
        W, H, L, R, T, B = 480, 210, 50, 16, 28, 38
        if not points:
            return Markup('<div style="color:#8a98a0;font-size:11px;">Sin datos suficientes.</div>')
        vals = [p[1] for p in points]
        vmin, vmax = min(vals), max(vals)
        if vmax == vmin:
            vmax = vmin + (abs(vmin) or 1.0)
        span = vmax - vmin
        n = len(points)

        def px(i):
            return L + (W - L - R) * (i / (n - 1) if n > 1 else 0.5)

        def py(v):
            return T + (H - T - B) * (1 - (v - vmin) / span)

        poly = " ".join("%.1f,%.1f" % (px(i), py(v)) for i, (_, v) in enumerate(points))
        area = "%.1f,%.1f %s %.1f,%.1f" % (px(0), H - B, poly, px(n - 1), H - B)
        dots = "".join(
            '<circle cx="%.1f" cy="%.1f" r="3.2" fill="#fff" stroke="%s" stroke-width="2"/>' % (px(i), py(v), color)
            for i, (_, v) in enumerate(points))
        grid = labels_y = ""
        for frac in (0.0, 0.5, 1.0):
            val = vmin + span * frac
            yy = py(val)
            grid += '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e8ec" stroke-width="1"/>' % (L, yy, W - R, yy)
            labels_y += '<text x="%d" y="%.1f" font-size="9" fill="#5a6b74" text-anchor="end">%.1f%s</text>' % (L - 6, yy + 3, val, suffix)
        xlab = ""
        for i in sorted(set([0, n // 2, n - 1])):
            xlab += '<text x="%.1f" y="%d" font-size="8.5" fill="#5a6b74" text-anchor="middle">%s</text>' % (px(i), H - B + 16, escape(str(points[i][0])))
        svg = (
            '<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" style="width:100%%;height:auto;">' % (W, H)
            + '<rect x="0" y="0" width="%d" height="%d" rx="8" fill="#f8fafc" stroke="#e3e8ec"/>' % (W, H)
            + '<text x="%d" y="18" font-size="11" font-weight="bold" fill="#123e5c">%s</text>' % (L, escape(title))
            + grid
            + '<polygon points="%s" fill="%s" opacity="0.10"/>' % (area, color)
            + '<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' % (poly, color)
            + dots + labels_y + xlab
            + '</svg>'
        )
        return Markup(svg)

    def evolution_charts(self):
        """Lista de gráficos (SVG) de la evolución productiva: supervivencia y tamaño vs tiempo."""
        self.ensure_one()
        evs = self.get_full_traceability_data()["evolutions"]

        def lbl(e):
            return e.date.strftime("%d/%m/%y") if e.date else "—"

        surv = [(lbl(e), e.survival_rate or 0.0) for e in evs]
        size = [(lbl(e), e.avg_size_mg or 0.0) for e in evs]
        charts = []
        if len(evs) >= 2:
            charts.append(self._line_chart_svg("Supervivencia (%) vs tiempo", surv, "#123e5c", "%"))
            charts.append(self._line_chart_svg("Tamaño promedio (mg) vs tiempo", size, "#14a0b0", ""))
        return charts

    def traceability_url(self):
        """URL pública de la página de trazabilidad on-screen de esta transacción."""
        self.ensure_one()
        return "%s/marketplace/compras/%s/trazabilidad" % (self.get_base_url(), self.id)

    def traceability_qr_uri(self):
        """Devuelve un data-URI PNG con el QR que apunta a la trazabilidad en línea.
        Usa la librería qrcode + Pillow (el backend de barcode de Odoo/reportlab
        requiere rlPyCairo, ausente en este entorno)."""
        self.ensure_one()
        try:
            import io
            import qrcode
            img = qrcode.make(self.traceability_url(), box_size=8, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64,%s" % base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return False
