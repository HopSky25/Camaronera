# -*- coding: utf-8 -*-
"""Mover producto propio entre los perfiles de la MISMA cuenta.

Una cuenta laboratorio + camaronera que compró larva como laboratorio tiene
que poder manejarla como camaronera (sembrarla en sus piscinas o volver a
publicarla con ese perfil). No es una venta: no hay precio, comisión, factura
ni verificación. Es una TRANSFERENCIA INTERNA, trazable:

* Lote / inventario (``shrimp.stock.lot.action_transfer_profile``): se
  descuenta del lote origen con un movimiento interno ``profile_transfer``
  (el mismo mecanismo que la siembra o la producción declarada) y nace un lote
  HIJO para el perfil destino cuyo movimiento de origen es esa transferencia.
  Así la trazabilidad sigue sola la cadena (``parent_move_id``) aguas arriba y
  aguas abajo. Admite cantidades parciales. El lote hijo cuelga de un producto
  propio de la cuenta con ``seller_role`` = perfil destino (se reutiliza el
  que ya exista para el mismo producto de origen, o se crea en borrador).
* Producto publicado (``shrimp.product.action_change_profile``): el producto
  entero pasa a venderse con otro perfil. Solo si la cuenta tiene ese perfil
  APROBADO, ese perfil vende ese estadío y no hay operaciones abiertas sobre el
  producto. Cada lote propio con stock se traspasa igual que arriba (lote hijo
  con el movimiento de transferencia), así la cadena queda documentada.

Reglas de perfil (``_shrimp_profile_*``):

* El destino tiene que ser un perfil de la cuenta en estado «aprobado» (un
  perfil pendiente o rechazado no recibe nada).
* El destino tiene que poder publicar (matriz ``sell_products``): el lote
  queda en un producto propio de ese perfil.
* El destino tiene que poder TENER ese estadío: lo vende (``_SELLS_TIPOS``) o
  se lo compra a quien lo vende (matriz ``buy_from_<vendedor natural>``). Una
  camaronera tiene larva y camarón, no nauplio.
* Cambiar el perfil de un producto exige además que el destino VENDA ese
  estadío.
"""
from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare

from .shrimp_larva import SHRIMP_TIPOS, SHRIMP_TIPO_ROL

MOVE_TYPE = "profile_transfer"

# Qué estadíos (tipo de producto) VENDE cada perfil. El laboratorio también
# revende el nauplio que compra (lo larvicultura y lo publica como suyo).
_SELLS_TIPOS = {
    "semillero": ("nauplio",),
    "laboratorio": ("nauplio", "larva"),
    "camaronera": ("camaron",),
}
# Perfiles cuyo stock propio está a la venta (no se "consume" al ubicarlo en
# un tanque). Es la misma regla que shrimp.transaction._buyer_can_republish:
# el resto (camaronera) gasta la larva al sembrarla.
_REPUBLISH_ROLES = ("semillero", "laboratorio")
# Estados de la transacción que ya no bloquean (cerrada o anulada).
_TX_CLOSED_STATES = ("done", "cancel")


def _role_selection(env):
    return list(env["res.partner"]._fields["shrimp_user_type"].selection)


class ShrimpProfileTransfer(models.Model):
    _name = "shrimp.profile.transfer"
    _inherit = ["shrimp.uuid.mixin"]
    _description = "Transferencia interna entre perfiles de la misma cuenta"
    _order = "date desc, id desc"

    kind = fields.Selection(
        [("lot", "Lote / inventario"), ("product", "Producto completo")],
        string="Qué se movió", required=True, default="lot", index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Cuenta", required=True, index=True, ondelete="cascade")
    from_role = fields.Selection(
        selection="_selection_shrimp_role", string="Desde el perfil", required=True)
    to_role = fields.Selection(
        selection="_selection_shrimp_role", string="Al perfil", required=True)
    qty = fields.Float(string="Cantidad")
    uom_id = fields.Many2one("shrimp.uom", string="Unidad")
    product_id = fields.Many2one(
        "shrimp.product", string="Producto de origen", index=True, ondelete="set null")
    target_product_id = fields.Many2one(
        "shrimp.product", string="Producto del perfil destino", index=True, ondelete="set null")
    lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Lote de origen", index=True, ondelete="set null")
    new_lot_ids = fields.Many2many(
        "shrimp.stock.lot", "shrimp_profile_transfer_new_lot_rel", "transfer_id", "lot_id",
        string="Lotes del perfil destino")
    move_ids = fields.Many2many(
        "shrimp.stock.move", "shrimp_profile_transfer_move_rel", "transfer_id", "move_id",
        string="Movimientos")
    user_id = fields.Many2one(
        "res.users", string="Registrado por", default=lambda self: self.env.uid, index=True)
    reason = fields.Text(string="Motivo")
    date = fields.Datetime(string="Fecha", required=True, default=fields.Datetime.now, index=True)

    @api.model
    def _selection_shrimp_role(self):
        return _role_selection(self.env)

    @api.depends("from_role", "to_role", "partner_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.label()

    def label(self):
        """'Transferencia interna: Laboratorio → Camaronera (misma empresa)'."""
        self.ensure_one()
        return self.env["shrimp.stock.lot"]._shrimp_profile_label(self.from_role, self.to_role)

    def role_label(self, role):
        return self.env["res.partner"]._shrimp_type_label(role) if role else ""

    def fmt_qty(self):
        self.ensure_one()
        return self.env["shrimp.transaction"].shrimp_fmt_qty(self.qty, self.uom_id)

    @api.model
    def _shrimp_for_partner(self, partner, limit=100):
        if not partner:
            return self.browse()
        return self.sudo().search([("partner_id", "=", partner.id)], limit=limit)


class ShrimpStockMoveProfile(models.Model):
    _inherit = "shrimp.stock.move"

    move_type = fields.Selection(
        selection_add=[(MOVE_TYPE, "Transferencia interna entre perfiles")],
        ondelete={MOVE_TYPE: "set default"})
    from_role = fields.Selection(
        selection="_selection_shrimp_role", string="Desde el perfil",
        help="Transferencia interna: perfil de la cuenta del que sale.")
    to_role = fields.Selection(
        selection="_selection_shrimp_role", string="Al perfil",
        help="Transferencia interna: perfil de la cuenta que recibe.")

    @api.model
    def _selection_shrimp_role(self):
        return _role_selection(self.env)

    def is_profile_transfer(self):
        self.ensure_one()
        return self.move_type == MOVE_TYPE


class ShrimpStockLotProfile(models.Model):
    _name = "shrimp.stock.lot"
    _inherit = ["shrimp.stock.lot", "mail.thread"]

    held_role = fields.Selection(
        selection="_selection_shrimp_role", string="Perfil que lo tiene", index=True,
        copy=False,
        help="Con qué perfil de la cuenta se maneja este lote: el del producto si es "
             "stock propio, o el perfil con el que se compró. Cambia con «Mover a "
             "otro perfil» (transferencia interna).")
    parent_lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Lote padre", index=True, ondelete="set null", copy=False,
        help="Lote del que salió este por una transferencia interna entre perfiles.")
    child_lot_ids = fields.One2many("shrimp.stock.lot", "parent_lot_id", string="Lotes hijos")
    profile_transfer_ids = fields.One2many(
        "shrimp.profile.transfer", "lot_id", string="Transferencias entre perfiles")

    @api.model
    def _selection_shrimp_role(self):
        return _role_selection(self.env)

    # ------------------------------------------------------------------
    # Perfil que tiene el lote
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_guess_held_role(self, product, owner, origin_move):
        """Perfil de la cuenta que tiene un lote nuevo (o antiguo, al migrar)."""
        if not owner:
            return False
        if product and owner == product.seller_partner_id:
            return product._shrimp_seller_role() or False
        if origin_move:
            if origin_move.move_type == MOVE_TYPE and origin_move.to_role:
                return origin_move.to_role
            tx = origin_move.transaction_id
            if tx and tx.buyer_partner_id == owner and tx.buyer_role:
                return tx.buyer_role
        return owner._shrimp_effective_type() or False

    @api.model_create_multi
    def create(self, vals_list):
        Product = self.env["shrimp.product"].sudo()
        Partner = self.env["res.partner"].sudo()
        Move = self.env["shrimp.stock.move"].sudo()
        for vals in vals_list:
            if not vals.get("held_role"):
                vals["held_role"] = self._shrimp_guess_held_role(
                    Product.browse(vals.get("product_id")) if vals.get("product_id") else Product,
                    Partner.browse(vals.get("owner_id")) if vals.get("owner_id") else Partner,
                    Move.browse(vals.get("origin_move_id")) if vals.get("origin_move_id") else Move)
        # Sin mensajes automáticos de creación: los lotes nacen a cientos con
        # cada compra; el chatter es para las transferencias.
        return super(ShrimpStockLotProfile, self.with_context(
            mail_create_nolog=True, mail_create_nosubscribe=True, tracking_disable=True,
        )).create(vals_list)

    @api.model
    def _shrimp_backfill_held_role(self):
        """Rellena held_role de los lotes que no lo tienen (migración)."""
        lots = self.sudo().with_context(active_test=False).search([("held_role", "=", False)])
        for lot in lots:
            role = self._shrimp_guess_held_role(lot.product_id, lot.owner_id, lot.origin_move_id)
            if role:
                lot.with_context(tracking_disable=True).write({"held_role": role})
        return len(lots)

    def _shrimp_held_role(self):
        """Perfil que tiene el lote AHORA. En stock propio manda el rol del
        producto (es lo que se vende); en lo comprado, el held_role."""
        self.ensure_one()
        lot = self.sudo()
        if lot.owner_id and lot.owner_id == lot.product_id.seller_partner_id:
            return lot.product_id._shrimp_seller_role() or lot.held_role
        return lot.held_role or self._shrimp_guess_held_role(
            lot.product_id, lot.owner_id, lot.origin_move_id)

    def held_role_label(self):
        self.ensure_one()
        return self.env["res.partner"]._shrimp_type_label(self._shrimp_held_role())

    @api.model
    def _shrimp_profile_label(self, from_role, to_role):
        label = self.env["res.partner"]._shrimp_type_label
        return _("Transferencia interna: %(a)s → %(b)s (misma empresa)") % {
            "a": label(from_role), "b": label(to_role)}

    # ------------------------------------------------------------------
    # Reglas
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_check_transfer_actor(self, account, actor=None):
        """Solo la propia cuenta (o un usuario interno) mueve su producto."""
        user = self.env.user
        if not user.share:
            return True
        actor = actor or user.partner_id
        holder = actor._shrimp_role_holder() if actor else actor
        if not actor or (actor != account and holder != account):
            raise AccessError(_("Solo la cuenta dueña puede mover su producto entre sus perfiles."))
        return True

    def _shrimp_profile_target_problem(self, to_role):
        """'' si el lote puede pasar al perfil `to_role` de su cuenta; si no, el porqué."""
        self.ensure_one()
        lot = self.sudo()
        if not to_role:
            return _("Elige el perfil al que quieres mover el lote.")
        from_role = lot._shrimp_held_role()
        problema = lot.product_id._shrimp_profile_role_problem(lot.owner_id, to_role, from_role)
        if problema:
            return problema
        tipo = lot.product_id._shrimp_tipo()
        if not lot.product_id._shrimp_role_can_hold(to_role, tipo):
            return lot.product_id._shrimp_tipo_problem(to_role, tipo, _("manejar"))
        return ""

    def _shrimp_profile_transfer_targets(self):
        """[(código, etiqueta)] de los perfiles a los que se puede mover el lote."""
        self.ensure_one()
        lot = self.sudo()
        if lot.state != "available" or float_compare(lot.available_qty, 0.0, precision_digits=6) <= 0:
            return []
        label = self.env["res.partner"]._shrimp_type_label
        return [(r, label(r)) for r in lot.owner_id._shrimp_roles()
                if not lot._shrimp_profile_target_problem(r)]

    def _shrimp_profile_transferable_qty(self):
        """Lo que se puede mover: el disponible del lote y, si es stock propio a
        la venta, sin lo reservado (chequeos y compras verificadas abiertas)."""
        self.ensure_one()
        lot = self.sudo()
        qty = lot.available_qty if lot.state == "available" else 0.0
        product = lot.product_id
        if lot.owner_id == product.seller_partner_id:
            # El disponible del producto ya descuenta las reservas (chequeos,
            # compras verificadas abiertas): no se mueve lo comprometido.
            qty = min(qty, product.available_qty or 0.0)
        return max(0.0, qty)

    # ------------------------------------------------------------------
    # Transferencia (lote)
    # ------------------------------------------------------------------
    def action_transfer_profile(self, to_role, qty=None, reason=None, actor=None):
        """Mueve `qty` (por defecto todo lo disponible) de este lote al perfil
        `to_role` de la misma cuenta. Devuelve la shrimp.profile.transfer."""
        self.ensure_one()
        lot = self.sudo()
        account = lot.owner_id
        self._shrimp_check_transfer_actor(account, actor)
        if lot.state != "available" or float_compare(lot.available_qty, 0.0, precision_digits=6) <= 0:
            raise ValidationError(_("El lote no tiene stock disponible para mover."))
        problema = lot._shrimp_profile_target_problem(to_role)
        if problema:
            raise ValidationError(problema)
        try:
            sin_cantidad = qty is None or qty is False or (isinstance(qty, str) and not qty.strip())
            qty = float(lot.available_qty if sin_cantidad else qty)
        except (TypeError, ValueError):
            raise ValidationError(_("La cantidad no es válida."))
        if float_compare(qty, 0.0, precision_digits=6) <= 0:
            raise ValidationError(_("La cantidad a mover debe ser mayor a 0."))
        maximo = lot._shrimp_profile_transferable_qty()
        if float_compare(qty, maximo, precision_digits=6) == 1:
            raise ValidationError(_(
                "Solo puedes mover hasta %(max)s de este lote (lo demás está reservado "
                "o no existe).") % {"max": self.env["shrimp.transaction"].shrimp_fmt_qty(
                    maximo, lot.uom_id)})
        from_role = lot._shrimp_held_role()
        transfer = lot._shrimp_do_profile_transfer(
            to_role, qty, from_role, reason=reason, kind="lot")
        source = lot.product_id
        if source.seller_partner_id == account and source.state in ("published", "sold"):
            source._update_state_from_stock()
        return transfer

    def _shrimp_do_profile_transfer(self, to_role, qty, from_role, reason=None, kind="lot",
                                    target_product=None, transfer=None):
        """Núcleo común: movimiento interno + lote hijo (+ registro y chatter)."""
        self.ensure_one()
        lot = self.sudo()
        account = lot.owner_id
        texto = self._shrimp_profile_label(from_role, to_role)
        move = lot._shrimp_internal_move(
            MOVE_TYPE, qty, texto, direction="out", from_role=from_role, to_role=to_role)
        target = target_product or lot.product_id._shrimp_profile_target_product(
            account, to_role, lot)
        child = self.env["shrimp.stock.lot"].sudo().create({
            "product_id": target.id,
            "owner_id": account.id,
            "origin_move_id": move.id,
            "initial_qty": qty,
            "available_qty": qty,
            "uom_id": lot.uom_id.id or target.uom_id.id,
            "state": "available",
            "held_role": to_role,
            "parent_lot_id": lot.id,
        })
        vals_transfer = {
            "kind": kind,
            "partner_id": account.id,
            "from_role": from_role,
            "to_role": to_role,
            "qty": qty,
            "uom_id": lot.uom_id.id or False,
            "product_id": lot.product_id.id,
            "target_product_id": target.id,
            "lot_id": lot.id,
            "reason": (reason or "").strip() or False,
            "new_lot_ids": [(4, child.id)],
            "move_ids": [(4, move.id)],
        }
        if transfer:
            transfer.sudo().write({
                "qty": transfer.qty + qty,
                "new_lot_ids": [(4, child.id)],
                "move_ids": [(4, move.id)],
            })
        else:
            transfer = self.env["shrimp.profile.transfer"].sudo().create(vals_transfer)
        target._compute_available_qty()
        if target.state == "sold":
            target._update_state_from_stock()
        if kind == "lot":
            transfer._shrimp_post_chatter(child)
        return transfer

    def action_view_profile_transfers(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Transferencias entre perfiles"),
            "res_model": "shrimp.profile.transfer",
            "view_mode": "list,form",
            "domain": ["|", ("lot_id", "=", self.id), ("new_lot_ids", "in", self.ids)],
        }


class ShrimpProfileTransferChatter(models.Model):
    _inherit = "shrimp.profile.transfer"

    def _shrimp_post_chatter(self, child_lots=None):
        """Deja constancia en el chatter del lote y de los productos."""
        for rec in self.sudo():
            fmt = rec.fmt_qty()
            cuerpo = _("%(label)s · %(qty)s · por %(user)s.") % {
                "label": rec.label(), "qty": fmt, "user": rec.user_id.name or "—"}
            if rec.reason:
                cuerpo += " " + _("Motivo: %s") % rec.reason
            if rec.lot_id:
                rec.lot_id.message_post(body=cuerpo)
            for child in (child_lots or rec.new_lot_ids):
                child.message_post(body=_("Lote creado por %s") % cuerpo)
            productos = (rec.product_id | rec.target_product_id).filtered(
                lambda p: p.seller_partner_id == rec.partner_id)
            for prod in productos:
                prod.message_post(body=cuerpo)


class ShrimpProductProfile(models.Model):
    _inherit = "shrimp.product"

    profile_origin_product_id = fields.Many2one(
        "shrimp.product", string="Producto de origen (transferencia interna)",
        index=True, ondelete="set null", copy=False,
        help="Producto del que nació este al mover stock entre perfiles de la misma "
             "cuenta. Se reutiliza en las siguientes transferencias al mismo perfil.")
    profile_transfer_ids = fields.One2many(
        "shrimp.profile.transfer", "product_id", string="Transferencias entre perfiles (origen)")

    # ------------------------------------------------------------------
    # Qué perfil puede tener / vender qué estadío
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_role_sells_tipo(self, role, tipo):
        Partner = self.env["res.partner"]
        if not Partner._shrimp_type_can(role, "sell_products"):
            return False
        return tipo in _SELLS_TIPOS.get(role, ())

    @api.model
    def _shrimp_role_can_hold(self, role, tipo):
        """True si el perfil `role` puede tener stock del tipo `tipo`
        (nauplio / larva / camaron): lo vende, o se lo compra a quien lo
        vende según la matriz de capacidades."""
        if self._shrimp_role_sells_tipo(role, tipo):
            return True
        vendedor = SHRIMP_TIPO_ROL.get(tipo)
        return bool(vendedor) and self.env["res.partner"]._shrimp_type_can(
            role, "buy_from_%s" % vendedor)

    @api.model
    def _shrimp_tipo_problem(self, role, tipo, verbo):
        label = self.env["res.partner"]._shrimp_type_label
        tipos = dict(SHRIMP_TIPOS)
        return _("Un perfil «%(r)s» no puede %(v)s %(t)s.") % {
            "r": label(role), "v": verbo, "t": (tipos.get(tipo) or tipo or "").lower()}

    @api.model
    def _shrimp_profile_role_problem(self, account, to_role, from_role):
        """Reglas comunes del perfil destino (lote y producto)."""
        Partner = self.env["res.partner"]
        label = Partner._shrimp_type_label
        if not account:
            return _("El stock no tiene dueño.")
        if to_role == from_role:
            return _("Ya está en tu perfil «%s».") % label(to_role)
        estado = account._shrimp_role_state(to_role)
        if estado != "approved":
            if estado == "pending":
                return _("Tu perfil «%s» todavía está pendiente de aprobación.") % label(to_role)
            return _("Tu cuenta no tiene aprobado el perfil «%s».") % label(to_role)
        roles_producto = [v for v, _l in self._fields["seller_role"].selection]
        if to_role not in roles_producto or not Partner._shrimp_type_can(to_role, "sell_products"):
            return _("El perfil «%s» no maneja inventario propio en el marketplace.") % label(to_role)
        return ""

    # ------------------------------------------------------------------
    # Producto propio del perfil destino
    # ------------------------------------------------------------------
    def _shrimp_profile_target_product(self, account, to_role, lot=None):
        """Producto de `account` con seller_role = `to_role` en el que queda el
        stock movido: el que ya nació de este mismo producto (o el producto
        raíz si es propio y de ese perfil), o uno nuevo en borrador."""
        self.ensure_one()
        source = self.sudo()
        root = source.profile_origin_product_id or source
        Product = self.env["shrimp.product"].sudo().with_context(active_test=False)
        existente = Product.search([
            ("seller_partner_id", "=", account.id),
            ("seller_role", "=", to_role),
            ("active", "=", True),
            ("state", "!=", "cancel"),
            "|", ("id", "=", root.id), ("profile_origin_product_id", "=", root.id),
        ], order="id asc", limit=1)
        if existente:
            return existente
        propio = source.seller_partner_id == account
        qty = lot.available_qty if lot else (source.available_qty or source.initial_qty or 1.0)
        vals = {
            "name": source.name,
            "seller_partner_id": account.id,
            "seller_role": to_role,
            "species_id": source.species_id.id or False,
            "stage_id": source.stage_id.id or False,
            "genetics_line_id": source.genetics_line_id.id or False,
            "avg_size_mg": source.avg_size_mg,
            "survival_rate": source.survival_rate,
            "health_status": source.health_status,
            "initial_qty": qty if qty > 0 else 1.0,
            "uom_id": source.uom_id.id or False,
            "presentation": source.presentation or False,
            "size_grade_id": source.size_grade_id.id or False,
            # Sin precio de transferencia: el precio de venta lo fija el dueño
            # al publicar (se propone el del producto de origen, como en una
            # compra).
            "price": source.price,
            "location": source.location,
            "batch_code": source.batch_code,
            "production_date": source.production_date,
            "expected_delivery_date": source.expected_delivery_date,
            "origin_facility_id": source.origin_facility_id.id if propio else False,
            "state": "draft",
            "active": True,
            "profile_origin_product_id": root.id,
            "photo_attachment_ids": [(6, 0, source.photo_attachment_ids.ids)],
        }
        # Se crea como sistema: el perfil ACTIVO del usuario del portal puede
        # no ser uno que publique, y aquí el vendedor es su propia cuenta con
        # un perfil aprobado que sí vende (lo comprueba _check_seller_type).
        nuevo = self.env["shrimp.product"].with_user(SUPERUSER_ID).with_context(
            skip_initial_lot=True, shrimp_system_flow=True).create(vals)
        for cert in source.certificate_line_ids:
            self.env["shrimp.product.certificate.line"].sudo().with_context(
                shrimp_keep_cert_status=True).create({
                    "status": cert.status,
                    "product_id": nuevo.id,
                    "source_user_certificate_line_id": cert.source_user_certificate_line_id.id or False,
                    "certificate_id": cert.certificate_id.id,
                    "number": cert.number,
                    "issue_date": cert.issue_date,
                    "expiry_date": cert.expiry_date,
                    "attachment_id": cert.attachment_id.id,
                    "active": cert.active,
                })
        return nuevo.sudo()

    # ------------------------------------------------------------------
    # Cambiar el perfil de un producto entero
    # ------------------------------------------------------------------
    def _shrimp_profile_change_blockers(self):
        """Operaciones abiertas que impiden cambiar el perfil del producto
        (lista de textos). Los módulos con más procesos sobre el producto la
        amplían."""
        self.ensure_one()
        product = self.sudo()
        motivos = []
        abiertas = product.env["shrimp.transaction"].sudo().search_count([
            ("product_id", "=", product.id), ("state", "not in", _TX_CLOSED_STATES)])
        if abiertas:
            motivos.append(_("tiene %s compra(s) abierta(s) (sin recibir o en verificación)") % abiertas)
        chequeos = product.check_request_ids.filtered(
            lambda c: c.state in ("requested", "under_review"))
        if chequeos:
            motivos.append(_("tiene %s solicitud(es) de chequeo activa(s)") % len(chequeos))
        if "shrimp.harvest.commitment" in self.env:
            compromisos = self.env["shrimp.harvest.commitment"].sudo().search_count([
                ("product_id", "=", product.id),
                ("state", "in", ("sent", "accepted", "to_confirm"))])
            if compromisos:
                motivos.append(_("tiene %s compromiso(s) de cosecha vigente(s)") % compromisos)
        return motivos

    def _shrimp_profile_change_problem(self, to_role):
        self.ensure_one()
        product = self.sudo()
        if not to_role:
            return _("Elige el perfil al que quieres mover el producto.")
        if not product.active or product.state == "cancel":
            return _("El producto está dado de baja.")
        from_role = product._shrimp_seller_role()
        problema = self._shrimp_profile_role_problem(product.seller_partner_id, to_role, from_role)
        if problema:
            return problema
        tipo = product._shrimp_tipo()
        if not self._shrimp_role_sells_tipo(to_role, tipo):
            return self._shrimp_tipo_problem(to_role, tipo, _("vender"))
        bloqueos = product._shrimp_profile_change_blockers()
        if bloqueos:
            return _("No se puede cambiar de perfil mientras el producto %s. "
                     "Ciérralas primero (o mueve solo una cantidad a otro perfil).") % "; ".join(bloqueos)
        return ""

    def _shrimp_profile_change_targets(self):
        """[(código, etiqueta)] de los perfiles a los que puede pasar el producto."""
        self.ensure_one()
        product = self.sudo()
        label = self.env["res.partner"]._shrimp_type_label
        return [(r, label(r)) for r in product.seller_partner_id._shrimp_roles()
                if not product._shrimp_profile_change_problem(r)]

    def _shrimp_own_available_lots(self):
        self.ensure_one()
        return self.env["shrimp.stock.lot"].sudo().search([
            ("product_id", "=", self.id), ("owner_id", "=", self.seller_partner_id.id),
            ("state", "=", "available"), ("available_qty", ">", 0)], order="id asc")

    def _shrimp_profile_qty_targets(self):
        """Perfiles a los que se puede mover UNA CANTIDAD del stock propio."""
        self.ensure_one()
        lots = self._shrimp_own_available_lots()
        if not lots:
            return []
        return lots[0]._shrimp_profile_transfer_targets()

    def shrimp_profile_move_targets(self):
        """Para el portal: perfiles a los que se puede mover algo (producto
        completo o una cantidad). Vacío = no se pinta el botón."""
        self.ensure_one()
        vistos = {}
        for code, lbl in self._shrimp_profile_change_targets() + self._shrimp_profile_qty_targets():
            vistos.setdefault(code, lbl)
        return list(vistos.items())

    def action_change_profile(self, to_role, reason=None, actor=None):
        """El producto entero pasa a venderse con el perfil `to_role` de la
        misma cuenta. Devuelve la shrimp.profile.transfer."""
        self.ensure_one()
        product = self.sudo()
        account = product.seller_partner_id
        self.env["shrimp.stock.lot"]._shrimp_check_transfer_actor(account, actor)
        problema = product._shrimp_profile_change_problem(to_role)
        if problema:
            raise ValidationError(problema)
        from_role = product._shrimp_seller_role()
        lots = product._shrimp_own_available_lots()
        product.with_context(shrimp_profile_transfer=True, shrimp_system_flow=True).write(
            {"seller_role": to_role})
        transfer = self.env["shrimp.profile.transfer"].sudo().create({
            "kind": "product",
            "partner_id": account.id,
            "from_role": from_role,
            "to_role": to_role,
            "qty": 0.0,
            "uom_id": product.uom_id.id or False,
            "product_id": product.id,
            "target_product_id": product.id,
            "reason": (reason or "").strip() or False,
        })
        # Cada lote propio con stock pasa al perfil nuevo como lote hijo: la
        # trazabilidad enseña el paso interno igual que en un lote movido.
        for lot in lots:
            lot._shrimp_do_profile_transfer(
                to_role, lot.available_qty, from_role, reason=reason, kind="product",
                target_product=product, transfer=transfer)
        transfer._shrimp_post_chatter()
        return transfer

    def action_transfer_qty_to_profile(self, to_role, qty, reason=None, actor=None):
        """Mueve UNA CANTIDAD del stock propio de este producto (de los lotes
        más antiguos) al perfil `to_role`. Devuelve la shrimp.profile.transfer."""
        self.ensure_one()
        product = self.sudo()
        account = product.seller_partner_id
        self.env["shrimp.stock.lot"]._shrimp_check_transfer_actor(account, actor)
        lots = product._shrimp_own_available_lots()
        if not lots:
            raise ValidationError(_("Este producto no tiene stock propio para mover."))
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            raise ValidationError(_("La cantidad no es válida."))
        if float_compare(qty, 0.0, precision_digits=6) <= 0:
            raise ValidationError(_("La cantidad a mover debe ser mayor a 0."))
        problema = lots[0]._shrimp_profile_target_problem(to_role)
        if problema:
            raise ValidationError(problema)
        maximo = min(sum(lots.mapped("available_qty")), product.available_qty or 0.0)
        if float_compare(qty, maximo, precision_digits=6) == 1:
            raise ValidationError(_(
                "Solo puedes mover hasta %s (lo demás está reservado).") % (
                self.env["shrimp.transaction"].shrimp_fmt_qty(maximo, product.uom_id)))
        from_role = product._shrimp_seller_role()
        target = product._shrimp_profile_target_product(account, to_role, lots[0])
        transfer = None
        falta = qty
        for lot in lots:
            if float_compare(falta, 0.0, precision_digits=6) <= 0:
                break
            take = min(lot.available_qty, falta)
            transfer = lot._shrimp_do_profile_transfer(
                to_role, take, from_role, reason=reason, kind="lot",
                target_product=target, transfer=transfer)
            falta -= take
        if product.state in ("published", "sold"):
            product._update_state_from_stock()
        return transfer

    def action_view_profile_transfers(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Transferencias entre perfiles"),
            "res_model": "shrimp.profile.transfer",
            "view_mode": "list,form",
            "domain": ["|", ("product_id", "=", self.id), ("target_product_id", "=", self.id)],
        }


class ShrimpLotAllocationProfile(models.Model):
    _inherit = "shrimp.lot.allocation"

    def _shrimp_consumes_lot(self):
        """Además de lo de siempre: el stock que una cuenta movió a un perfil
        que NO revende (camaronera) se gasta al sembrarlo, aunque viva en un
        producto propio de la cuenta."""
        if super()._shrimp_consumes_lot():
            return True
        lot = self.stock_lot_id.sudo()
        product = lot.product_id
        return bool(lot) and bool(product.profile_origin_product_id) \
            and lot.owner_id == product.seller_partner_id \
            and lot._shrimp_held_role() not in _REPUBLISH_ROLES


class ShrimpTransactionProfile(models.Model):
    _inherit = "shrimp.transaction"

    def _shrimp_chain_parties(self, data=None):
        """La transferencia interna añade a la cadena el MISMO socio con el
        perfil que recibe, justo detrás del perfil del que sale
        (Laboratorio → Camaronera de la misma empresa)."""
        self.ensure_one()
        data = data or self.get_full_traceability_data()
        seq = super()._shrimp_chain_parties(data)
        for m in data["moves"].filtered(lambda mv: mv.move_type == MOVE_TYPE):
            socio, desde, hacia = m.source_partner_id, m.from_role, m.to_role
            if not socio or not desde or not hacia:
                continue
            claves = [(p.id, r) for p, r in seq]
            a, b = (socio.id, desde), (socio.id, hacia)
            if a in claves:
                i = max(idx for idx, k in enumerate(claves) if k == a)
                if i + 1 < len(seq) and claves[i + 1] == b:
                    continue
                seq.insert(i + 1, (socio, hacia))
            elif b in claves:
                seq.insert(claves.index(b), (socio, desde))
            else:
                seq.extend([(socio, desde), (socio, hacia)])
        return seq

    def shrimp_profile_transfers(self, data=None):
        """Transferencias internas que forman parte de la trazabilidad de esta
        compra (para pantallas, PDF y la página pública). Sin precios."""
        self.ensure_one()
        data = data or self.get_full_traceability_data()
        moves = data["moves"].filtered(lambda m: m.move_type == MOVE_TYPE)
        out = []
        for m in moves:
            out.append({
                "move": m,
                "date": m.date or m.create_date,
                "company": m.source_partner_id,
                "from_role": m.from_role,
                "to_role": m.to_role,
                "qty": m.qty,
                "uom": m.product_id.uom_id,
                "label": self.env["shrimp.stock.lot"]._shrimp_profile_label(m.from_role, m.to_role),
            })
        return out
