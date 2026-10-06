from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class ShrimpLotAllocation(models.Model):
    _name = "shrimp.lot.allocation"
    _inherit = "shrimp.uuid.mixin"
    _description = "Asignación de lotes a piscinas"
    _order = "allocation_date desc, id desc"

    stock_lot_id = fields.Many2one(
        "shrimp.stock.lot",
        string="Lote",
        required=True,
        ondelete="cascade",
        index=True,
    )

    pond_id = fields.Many2one(
        "shrimp.partner.pond",
        string="Piscina",
        required=True,
        ondelete="cascade",
        index=True,
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        related="pond_id.partner_id",
        store=True,
        readonly=True,
    )

    product_id = fields.Many2one(
        "shrimp.product",
        string="Producto",
        related="stock_lot_id.product_id",
        store=True,
        readonly=True,
    )

    allocated_qty = fields.Float(
        string="Cantidad asignada",
        required=True,
    )

    allocation_date = fields.Date(
        string="Fecha de asignación",
        default=fields.Date.context_today,
        required=True,
    )

    notes = fields.Text(string="Observaciones")

    state = fields.Selection([
        ("draft", "Borrador"),
        ("allocated", "Asignado"),
        ("released", "Liberado"),
        ("cancelled", "Cancelado"),
    ], string="Estado", default="allocated", required=True, index=True)

    @api.constrains("allocated_qty")
    def _check_allocated_qty(self):
        for rec in self:
            if rec.allocated_qty <= 0:
                raise ValidationError(_("La cantidad asignada debe ser mayor a 0."))

    @api.constrains("stock_lot_id", "pond_id")
    def _check_same_partner(self):
        for rec in self:
            if rec.stock_lot_id and rec.pond_id:
                if rec.stock_lot_id.owner_id != rec.pond_id.partner_id:
                    raise ValidationError(_("El lote y la piscina deben pertenecer al mismo partner."))

    # ------------------------------------------------------------------
    # La siembra CONSUME el lote
    # ------------------------------------------------------------------
    # Sembrar larva en una piscina de engorde la gasta: esa larva ya no está
    # en el inventario de la camaronera. Antes la asignación solo apuntaba la
    # piscina y el lote seguía mostrando las 60.000 millares como disponibles,
    # y cada asignación se validaba por separado contra ese disponible intacto
    # (se podía «sembrar» el mismo lote dos veces). Ahora la siembra deja un
    # movimiento de consumo (tipo «sowing») y descuenta el lote.
    #
    # Excepción: el lote que el dueño VENDE (un laboratorio que coloca sus
    # nauplios en sus tanques de larvicultura). Ahí la asignación es solo la
    # ubicación del producto, que se sigue vendiendo desde el mismo lote.
    _CONSUMING_STATES = ("allocated", "released")

    sowing_move_id = fields.Many2one(
        "shrimp.stock.move", string="Movimiento de siembra", readonly=True, copy=False,
        ondelete="set null", help="Consumo del lote que dejó esta siembra.")

    def _shrimp_consumes_lot(self):
        """True si sembrar gasta el lote (no es stock que el dueño revende)."""
        self.ensure_one()
        lot = self.stock_lot_id.sudo()
        return bool(lot) and lot.owner_id != lot.product_id.seller_partner_id

    def _shrimp_consume(self):
        """Descuenta del lote lo sembrado (idempotente)."""
        for rec in self:
            if rec.sowing_move_id or rec.state not in self._CONSUMING_STATES:
                continue
            if not rec._shrimp_consumes_lot():
                continue
            lot = rec.stock_lot_id.sudo()
            if float_compare(rec.allocated_qty, lot.available_qty, precision_digits=6) == 1:
                raise ValidationError(_(
                    "No puedes sembrar %(qty)s: al lote solo le quedan %(disp)s "
                    "(ya descontadas las siembras anteriores).") % {
                        "qty": rec.allocated_qty, "disp": lot.available_qty})
            move = lot._shrimp_internal_move(
                "sowing", rec.allocated_qty,
                _("Siembra en %s") % (rec.pond_id.display_name or ""),
                allocation_id=rec.id,
                date=self.env["shrimp.transaction"].shrimp_noon_utc(rec.allocation_date, rec.partner_id)
                if rec.allocation_date else None)
            rec.sudo().with_context(shrimp_alloc_internal=True).write({"sowing_move_id": move.id})

    def _shrimp_return(self, reason):
        """Devuelve al lote lo que la siembra había descontado."""
        for rec in self:
            if not rec.sowing_move_id:
                continue
            lote = rec.sowing_move_id.lot_id or rec.stock_lot_id
            lote.sudo()._shrimp_internal_move(
                "adjustment", rec.sowing_move_id.qty, reason, direction="in",
                allocation_id=rec.id)
            rec.sudo().with_context(shrimp_alloc_internal=True).write({"sowing_move_id": False})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._shrimp_consume()
        return records

    def write(self, vals):
        if self.env.context.get("shrimp_alloc_internal"):
            return super().write(vals)
        clave = lambda r: (r.stock_lot_id.id, r.allocated_qty, r.state in self._CONSUMING_STATES)
        antes = {r.id: clave(r) for r in self}
        res = super().write(vals)
        for rec in self:
            if antes.get(rec.id) == clave(rec):
                continue
            # Cambió el lote, la cantidad o si consume: se deshace el consumo
            # anterior y se aplica el nuevo, para que el lote siempre cuadre.
            if rec.sowing_move_id:
                rec._shrimp_return(_("Siembra modificada o cancelada"))
            rec._shrimp_consume()
        return res

    def unlink(self):
        self.filtered("sowing_move_id")._shrimp_return(_("Siembra eliminada"))
        return super().unlink()

    @api.constrains("allocated_qty", "stock_lot_id")
    def _check_not_exceed_lot_qty(self):
        # Las siembras que consumen el lote se validan al descontar (contra el
        # disponible que dejaron las anteriores). Las demás (tanques del
        # laboratorio) no pueden superar lo que tiene el lote.
        for rec in self:
            if rec.sowing_move_id or not rec.stock_lot_id or rec._shrimp_consumes_lot():
                continue
            if float_compare(rec.allocated_qty, rec.stock_lot_id.available_qty,
                             precision_digits=6) == 1:
                raise ValidationError(_("La cantidad asignada no puede superar la cantidad disponible del lote."))

    # ------------------------------------------------------------------
    # Portal: qué se puede sembrar y qué siembra se puede tocar
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_can_sow(self, partner):
        """True si la cuenta tiene piscinas de engorde que sembrar (la
        capacidad «manage_ponds» de la matriz: la camaronera)."""
        return bool(partner) and partner.sudo()._shrimp_can_any("manage_ponds")

    @api.model
    def _shrimp_lot_is_sowable(self, lot):
        """Lote que su dueño puede sembrar desde el portal: semilla de engorde
        (larva, postlarva o juvenil; no camarón adulto), con saldo, que la
        siembra consume (no es stock que el dueño revende) y que no está en un
        perfil sin piscinas de engorde (p. ej. el de laboratorio de una cuenta
        con varios perfiles)."""
        lot = lot.sudo()
        if not lot or lot.state != "available" or float_compare(
                lot.available_qty or 0.0, 0.0, precision_digits=6) <= 0:
            return False
        prod = lot.product_id
        if not prod or not prod._shrimp_is_growout_seed():
            return False
        if "held_role" in lot._fields and lot.held_role and not self.env[
                "res.partner"]._shrimp_type_can(lot.held_role, "manage_ponds"):
            return False
        return lot.owner_id != prod.seller_partner_id

    @api.model
    def _shrimp_sowable_lots(self, partner):
        lots = self.env["shrimp.stock.lot"].sudo().search([
            ("owner_id", "=", partner.id), ("state", "=", "available"),
            ("available_qty", ">", 0)], order="id desc")
        return lots.filtered(self._shrimp_lot_is_sowable)

    def _shrimp_harvests(self):
        """Productos (cosechas) que declaran esta siembra como origen."""
        self.ensure_one()
        return self.env["shrimp.product"].sudo().with_context(active_test=False).search(
            [("origin_allocation_ids", "in", self.ids)])

    def _shrimp_portal_edit_block(self):
        """None si el dueño puede editar o cancelar la siembra desde el portal;
        si no, el motivo (texto para el usuario)."""
        self.ensure_one()
        if self.state != "allocated":
            return _("Solo se modifica una siembra vigente.")
        if self._shrimp_harvests():
            return _("Esta siembra ya es el origen de una cosecha publicada: "
                     "modificarla rompería su trazabilidad.")
        return None

    def display_qty(self):
        self.ensure_one()
        return self.env["shrimp.transaction"].shrimp_fmt_qty(self.allocated_qty, self.stock_lot_id.uom_id)
