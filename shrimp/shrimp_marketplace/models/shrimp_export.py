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

import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare

from odoo.addons.shrimp_user_registry.authz import shrimp_actor, shrimp_is_trusted

_logger = logging.getLogger(__name__)

# D-35: la anulación de una salida con DAE o factura no la decide quien la
# registró: queda pendiente hasta que un administrador de la plataforma la
# apruebe.
GRUPO_APRUEBA_ANULACION = "shrimp_marketplace.group_marketplace_manager"


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
    # mail.activity.mixin: D-35, la anulación pendiente se le asigna como
    # actividad a los administradores de la plataforma.
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
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

    # D-35: anular siempre exige motivo; con DAE o factura, además, la
    # aprobación de la plataforma. Mientras tanto la salida sigue registrada
    # (el producto no vuelve al lote) y solo se marca la solicitud.
    cancel_reason = fields.Text(string="Motivo de la anulación", readonly=True, copy=False)
    cancel_state = fields.Selection(
        [("pending", "Anulación pendiente de aprobación"),
         ("approved", "Anulación aprobada"),
         ("rejected", "Anulación rechazada")],
        string="Solicitud de anulación", readonly=True, copy=False, index=True, tracking=True)
    cancel_requested_uid = fields.Many2one(
        "res.users", string="Anulación pedida por", readonly=True, copy=False)
    cancel_requested_at = fields.Datetime(string="Anulación pedida el", readonly=True, copy=False)
    cancel_decided_uid = fields.Many2one(
        "res.users", string="Anulación resuelta por", readonly=True, copy=False)
    cancel_decided_at = fields.Datetime(string="Anulación resuelta el", readonly=True, copy=False)
    cancel_decision_note = fields.Text(string="Nota de la resolución", readonly=True, copy=False)

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

    def _anulacion_requiere_aprobacion(self):
        """D-35: una salida registrada que ya tiene DAE o factura no se anula
        sin la aprobación de la plataforma: esos documentos existen fuera
        (aduana, SRI) y anularla aquí descuadraría la trazabilidad."""
        self.ensure_one()
        return self.state == "registered" and bool(
            (self.dae_number or "").strip() or (self.invoice_number or "").strip())

    @api.model
    def _es_aprobador_anulacion(self):
        return self.env.su or self.env.user.has_group(GRUPO_APRUEBA_ANULACION)

    def _aprobadores_anulacion(self):
        grupo = self.env.ref(GRUPO_APRUEBA_ANULACION, raise_if_not_found=False)
        if not grupo:
            return self.env["res.users"]
        return grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and not u._is_superuser() and u._is_internal())

    def action_cancel(self, reason=None, actor=None):
        """Anula una salida: devuelve lo descontado al lote.

        D-35: el motivo es obligatorio siempre. Si la salida ya tiene DAE o
        factura, quien la registró solo puede PEDIR la anulación: queda
        pendiente hasta que un administrador de la plataforma la aprueba
        (action_approve_cancel). Un administrador que anula desde el
        back-office no necesita pedírsela a sí mismo.
        """
        if not shrimp_is_trusted(self.env):
            # Por RPC (portal): siempre como el socio del propio usuario, que
            # tiene que ser el titular; nunca la anulación directa del gestor.
            actor = shrimp_actor(self.env)
            if not actor:
                raise AccessError(_("La salida la anula quien la registró."))
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError(_("Indica el motivo de la anulación de la salida."))
        for rec in self:
            if actor and self.env.user.share and actor != rec.partner_id:
                raise AccessError(_("La salida la anula quien la registró."))
            if rec.state == "cancelled":
                raise ValidationError(_("La salida %s ya está anulada.") % rec.name)
            if rec.cancel_state == "pending":
                raise ValidationError(_(
                    "La anulación de %s ya está pedida y espera la aprobación de "
                    "la plataforma.") % rec.name)
            directo = not actor and self._es_aprobador_anulacion()
            if rec._anulacion_requiere_aprobacion() and not directo:
                rec._pedir_aprobacion_anulacion(reason)
                continue
            rec._ejecutar_anulacion(reason)
        return True

    def _pedir_aprobacion_anulacion(self, reason):
        self.ensure_one()
        self.write({
            "cancel_state": "pending",
            "cancel_reason": reason,
            "cancel_requested_uid": self.env.user.id,
            "cancel_requested_at": fields.Datetime.now(),
            "cancel_decided_uid": False,
            "cancel_decided_at": False,
            "cancel_decision_note": False,
        })
        texto = _("Anulación pedida para la salida %(ref)s (DAE %(dae)s, factura %(fac)s). "
                  "Motivo: %(motivo)s. Queda pendiente de aprobación de la plataforma: "
                  "hasta entonces la salida sigue registrada.") % {
            "ref": self.name, "dae": self.dae_number or "—",
            "fac": self.invoice_number or "—", "motivo": reason.rstrip(". ")}
        self.message_post(body=texto)
        for usuario in self._aprobadores_anulacion()[:5]:
            try:
                self.sudo().activity_schedule(
                    "mail.mail_activity_data_todo", user_id=usuario.id,
                    summary=_("Aprobar o rechazar la anulación de %s") % self.name,
                    note=texto)
            except Exception:  # noqa: BLE001 - el aviso nunca impide pedir
                _logger.warning("No se pudo crear la actividad de anulación %s",
                                self.id, exc_info=True)
        return True

    def _ejecutar_anulacion(self, reason):
        self.ensure_one()
        if self.state == "registered":
            for line in self.line_ids.filtered("move_id"):
                line.lot_id.sudo()._shrimp_internal_move(
                    "adjustment", line.move_id.qty,
                    _("Anulación de la salida %s") % self.name, direction="in",
                    export_id=self.id)
        self.write({"state": "cancelled", "cancel_reason": reason})
        self.message_post(body=_("Salida anulada: %s") % reason)
        return True

    def _cerrar_actividades_anulacion(self, feedback):
        try:
            self.sudo().activity_feedback(["mail.mail_activity_data_todo"], feedback=feedback)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudieron cerrar las actividades de %s", self.ids, exc_info=True)

    def _avisar_titular_anulacion(self, body):
        """Deja el mensaje en la salida notificando al titular (correo)."""
        self.ensure_one()
        self.message_post(body=body, partner_ids=self.partner_id.ids,
                          message_type="comment", subtype_xmlid="mail.mt_comment")

    def action_approve_cancel(self, note=None):
        """La plataforma aprueba la anulación pedida: ahora sí vuelve el
        producto al lote y la salida queda anulada."""
        if not self._es_aprobador_anulacion():
            raise AccessError(_("Solo un administrador de la plataforma aprueba anulaciones."))
        for rec in self:
            if rec.cancel_state != "pending":
                raise ValidationError(_("La salida %s no tiene una anulación pendiente.") % rec.name)
            rec._ejecutar_anulacion(rec.cancel_reason)
            rec.write({"cancel_state": "approved", "cancel_decided_uid": self.env.user.id,
                       "cancel_decided_at": fields.Datetime.now(),
                       "cancel_decision_note": (note or "").strip() or False})
            rec._cerrar_actividades_anulacion(_("Anulación aprobada"))
            rec._avisar_titular_anulacion(_(
                "La plataforma aprobó la anulación de la salida %s: la cantidad volvió al lote.")
                % rec.name)
        return True

    def action_reject_cancel(self, note=None):
        """La plataforma rechaza la anulación: la salida sigue registrada."""
        if not self._es_aprobador_anulacion():
            raise AccessError(_("Solo un administrador de la plataforma rechaza anulaciones."))
        note = (note or "").strip()
        if not note:
            raise ValidationError(_("Indica por qué se rechaza la anulación."))
        for rec in self:
            if rec.cancel_state != "pending":
                raise ValidationError(_("La salida %s no tiene una anulación pendiente.") % rec.name)
            rec.write({"cancel_state": "rejected", "cancel_decided_uid": self.env.user.id,
                       "cancel_decided_at": fields.Datetime.now(),
                       "cancel_decision_note": note})
            rec._cerrar_actividades_anulacion(_("Anulación rechazada: %s") % note)
            rec._avisar_titular_anulacion(_(
                "La plataforma rechazó la anulación de la salida %(ref)s, que sigue "
                "registrada. Motivo: %(nota)s") % {"ref": rec.name, "nota": note})
        return True

    def action_open_cancel_wizard(self):
        """Back-office: pide el motivo (anular) o la nota (rechazar)."""
        self.ensure_one()
        modo = self.env.context.get("shrimp_export_cancel_mode") or "cancel"
        return {
            "type": "ir.actions.act_window",
            "res_model": "shrimp.export.cancel.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_export_id": self.id, "default_mode": modo},
        }

    @api.private
    @api.model
    def shrimp_register(self, partner, vals, lines):
        """Crea y registra una salida en nombre de `partner` (portal/API).

        Privado (@api.private): crea en sudo a nombre del socio que recibe; el
        portal y la API lo llaman con el socio de quien hace la petición.

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


class ShrimpExportCancelWizard(models.TransientModel):
    """D-35: motivo de la anulación (o del rechazo) desde el back-office."""

    _name = "shrimp.export.cancel.wizard"
    _description = "Anular salida / resolver anulación"

    export_id = fields.Many2one("shrimp.export", required=True, ondelete="cascade")
    mode = fields.Selection(
        [("cancel", "Anular"), ("approve", "Aprobar anulación"), ("reject", "Rechazar anulación")],
        required=True, default="cancel")
    reason = fields.Text(string="Motivo")

    def action_confirm(self):
        self.ensure_one()
        exp = self.export_id
        if self.mode == "approve":
            exp.action_approve_cancel(note=self.reason)
        elif self.mode == "reject":
            exp.action_reject_cancel(note=self.reason)
        else:
            exp.action_cancel(reason=self.reason)
        return {"type": "ir.actions.act_window_close"}
