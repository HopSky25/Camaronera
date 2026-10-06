"""Salida / exportación: el último eslabón de la cadena.

La empacadora (o la camaronera que vende fuera su producto empacado) compra
en la plataforma pero vende fuera de ella: a un importador de Valencia, de
Miami o de Shanghái. Hasta ahora esa salida solo se podía dejar como un
mensaje en el chatter de la compra, así que la trazabilidad terminaba en la
planta y el inventario seguía mostrando las libras como disponibles para
siempre.

Aquí se registra con sus documentos (DAE, factura de exportación,
contenedor), sale del lote con un movimiento «export» y aparece como el paso
final en la trazabilidad. Lo comercial (precio, comprador de destino) es
privado; si se marca como confidencial, la página pública solo dice el país.
"""

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare


class ShrimpStockMove(models.Model):
    _inherit = "shrimp.stock.move"

    export_id = fields.Many2one(
        "shrimp.export", string="Salida / exportación", index=True, ondelete="set null")


class ShrimpStockLotExport(models.Model):
    _inherit = "shrimp.stock.lot"

    def _shrimp_export_block_reason(self, partner=None):
        """None si `partner` (por defecto el dueño) puede registrar la salida
        de este lote; si no, el motivo.

        Es LA regla de las salidas: la usan el modelo (shrimp_register, que
        cubre portal y API), las rutas del portal, el botón «Registrar salida»
        de «Mi inventario» y, por la capacidad, el menú «Salidas».
        - El lote tiene que ser suyo.
        - La cuenta tiene que poder registrar salidas («register_exports» de la
          matriz: empacadora y camaronera, que venden fuera de la plataforma).
        - Y tiene que ser camarón adulto (entero o empacado): la larva, la
          postlarva y el juvenil se siembran, no se exportan.
        """
        self.ensure_one()
        lot = self.sudo()
        partner = (partner or lot.owner_id).sudo()
        if not lot.owner_id or lot.owner_id != partner:
            return _("Ese lote no es tuyo.")
        if not partner._shrimp_can_any("register_exports"):
            return _("Las salidas y exportaciones las registra quien vende camarón fuera "
                     "de la plataforma: %s.") % partner._shrimp_types_label("register_exports")
        prod = lot.product_id
        if prod and (prod._shrimp_tipo() != "camaron" or prod._shrimp_is_growout_seed()):
            return _("Solo se registra la salida de camarón adulto o empacado; la larva y "
                     "el juvenil se siembran (Instalaciones y piscinas).")
        return None

    def shrimp_can_register_export(self, partner=None):
        self.ensure_one()
        return not self._shrimp_export_block_reason(partner)


class ShrimpExport(models.Model):
    _name = "shrimp.export"
    _description = "Salida / exportación de producto"
    _inherit = ["mail.thread", "shrimp.uuid.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(string="Referencia", required=True, copy=False, readonly=True,
                       default=lambda self: _("Nueva"))
    partner_id = fields.Many2one(
        "res.partner", string="Quién despacha", required=True, index=True,
        ondelete="restrict", help="Dueño de los lotes de los que sale el producto.")
    date = fields.Date(string="Fecha de salida", required=True,
                       default=fields.Date.context_today, tracking=True)
    destination_buyer = fields.Char(string="Comprador de destino", tracking=True)
    destination_country_id = fields.Many2one(
        "res.country", string="País de destino", tracking=True)
    destination_place = fields.Char(string="Puerto / ciudad de destino")
    dae_number = fields.Char(string="N.º DAE", tracking=True,
                             help="Declaración Aduanera de Exportación.")
    invoice_number = fields.Char(string="Factura de exportación / venta", tracking=True)
    container = fields.Char(string="Contenedor")
    boxes = fields.Integer(string="Cajas / masters")
    line_ids = fields.One2many("shrimp.export.line", "export_id", string="Lotes")
    qty = fields.Float(string="Cantidad", compute="_compute_qty", store=True)
    uom_id = fields.Many2one("shrimp.uom", string="Unidad", compute="_compute_qty", store=True)
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", default=lambda self: self.env.company.currency_id)
    price_unit = fields.Monetary(string="Precio unitario", currency_field="currency_id")
    amount_total = fields.Monetary(string="Valor", currency_field="currency_id",
                                   compute="_compute_qty", store=True)
    confidential = fields.Boolean(
        string="Comprador confidencial", default=True,
        help="En la trazabilidad pública solo se publica el país de destino.")
    attachment_ids = fields.Many2many(
        "ir.attachment", "shrimp_export_attachment_rel", "export_id", "attachment_id",
        string="Documentos")
    notes = fields.Text(string="Observaciones")
    state = fields.Selection(
        [("draft", "Borrador"), ("registered", "Registrada"), ("cancelled", "Anulada")],
        string="Estado", default="draft", required=True, index=True, tracking=True)
    move_ids = fields.One2many("shrimp.stock.move", "export_id", string="Movimientos")

    @api.depends("line_ids.qty", "line_ids.lot_id", "price_unit")
    def _compute_qty(self):
        for rec in self:
            rec.qty = sum(rec.line_ids.mapped("qty"))
            rec.uom_id = rec.line_ids[:1].lot_id.uom_id
            rec.amount_total = rec.qty * (rec.price_unit or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nueva")) == _("Nueva"):
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code("shrimp.export") or _("Nueva")
        return super().create(vals_list)

    @api.constrains("line_ids", "partner_id")
    def _check_lineas(self):
        for rec in self:
            for line in rec.line_ids:
                if line.lot_id.owner_id != rec.partner_id:
                    raise ValidationError(_("Solo se despacha producto de lotes propios."))

    # ------------------------------------------------------------------
    def action_register(self):
        """Saca el producto de los lotes (movimiento «export») y registra."""
        for rec in self:
            if rec.state != "draft":
                raise ValidationError(_("Solo se registra una salida en borrador."))
            if not rec.line_ids:
                raise ValidationError(_("Indica de qué lote sale el producto."))
            for line in rec.line_ids:
                if float_compare(line.qty, 0.0, precision_digits=6) <= 0:
                    raise ValidationError(_("La cantidad de cada lote debe ser mayor a 0."))
                motivo = _("Salida %(ref)s%(dest)s") % {
                    "ref": rec.name,
                    "dest": (" → %s" % rec.destination_country_id.name) if rec.destination_country_id else ""}
                move = line.lot_id.sudo()._shrimp_internal_move(
                    "export", line.qty, motivo, export_id=rec.id,
                    date=(fields.Datetime.now() if rec.date == fields.Date.context_today(rec)
                          else self.env["shrimp.transaction"].shrimp_noon_utc(rec.date, rec.partner_id)))
                line.move_id = move.id
            rec.state = "registered"
            rec.message_post(body=_("Salida registrada: %s.") % rec.qty_label())
        return True

    def action_cancel(self, reason=None, actor=None):
        """Anula una salida: devuelve lo descontado al lote."""
        for rec in self:
            if actor and self.env.user.share and actor != rec.partner_id:
                raise AccessError(_("La salida la anula quien la registró."))
            if rec.state == "registered":
                for line in rec.line_ids.filtered("move_id"):
                    line.lot_id.sudo()._shrimp_internal_move(
                        "adjustment", line.move_id.qty,
                        _("Anulación de la salida %s") % rec.name, direction="in",
                        export_id=rec.id)
            rec.state = "cancelled"
            if reason:
                rec.message_post(body=_("Salida anulada: %s") % reason)
        return True

    @api.model
    def shrimp_register(self, partner, vals, lines):
        """Crea y registra una salida en nombre de `partner` (portal/API).

        `lines`: lista de (lote, cantidad). Valida que los lotes sean suyos y
        tengan saldo; lanza ValidationError/AccessError si no.
        """
        if not lines:
            raise ValidationError(_("Indica de qué lote sale el producto."))
        line_cmds = []
        for lot, qty in lines:
            lot = lot.sudo()
            if not lot or lot.owner_id != partner:
                raise AccessError(_("Ese lote no es tuyo."))
            motivo = lot._shrimp_export_block_reason(partner)
            if motivo:
                raise ValidationError(motivo)
            qty = float(qty or 0.0)
            if float_compare(qty, 0.0, precision_digits=6) <= 0:
                raise ValidationError(_("La cantidad debe ser mayor a 0."))
            if float_compare(qty, lot.available_qty, precision_digits=6) == 1:
                raise ValidationError(_(
                    "El lote solo tiene %s disponibles.") % self.env["shrimp.transaction"].shrimp_fmt_qty(
                        lot.available_qty, lot.uom_id))
            line_cmds.append((0, 0, {"lot_id": lot.id, "qty": qty}))
        permitidos = {"date", "destination_buyer", "destination_country_id", "destination_place",
                      "dae_number", "invoice_number", "container", "boxes", "price_unit",
                      "currency_id", "confidential", "notes", "attachment_ids"}
        datos = {k: v for k, v in (vals or {}).items() if k in permitidos}
        datos.update({"partner_id": partner.id, "line_ids": line_cmds})
        exp = self.sudo().create(datos)
        exp.action_register()
        return exp

    # ------------------------------------------------------------------
    # Presentación
    # ------------------------------------------------------------------
    def qty_label(self):
        self.ensure_one()
        return self.env["shrimp.transaction"].shrimp_fmt_qty(self.qty, self.uom_id)

    def chain_label(self):
        """Texto del último eslabón de la cadena (sin datos comerciales)."""
        self.ensure_one()
        if self.destination_country_id:
            return _("Exportación a %s") % self.destination_country_id.name
        return _("Venta fuera de la plataforma")

    def buyer_label(self, viewer=None):
        """Comprador de destino, salvo que sea confidencial y quien mira no
        sea el que despacha."""
        self.ensure_one()
        if self.confidential and (not viewer or viewer != self.partner_id):
            return _("Confidencial")
        return self.destination_buyer or "—"

    def public_dict(self):
        """Lo que se publica en la trazabilidad pública: fecha, país, cantidad,
        presentación. Sin precio, sin documentos; el comprador solo si NO es
        confidencial."""
        self.ensure_one()
        return {
            "date": self.date.isoformat() if self.date else None,
            "country": self.destination_country_id.name or None,
            "buyer": None if self.confidential else (self.destination_buyer or None),
            "qty": round(self.qty, 2),
            "uom": self.uom_id.name or None,
            "boxes": self.boxes or None,
        }


class ShrimpExportLine(models.Model):
    _name = "shrimp.export.line"
    _description = "Lote de una salida / exportación"

    export_id = fields.Many2one("shrimp.export", required=True, ondelete="cascade", index=True)
    lot_id = fields.Many2one("shrimp.stock.lot", string="Lote", required=True, index=True,
                             ondelete="restrict")
    product_id = fields.Many2one(related="lot_id.product_id", store=True, string="Producto")
    qty = fields.Float(string="Cantidad", required=True)
    move_id = fields.Many2one("shrimp.stock.move", string="Movimiento", readonly=True,
                              ondelete="set null")
