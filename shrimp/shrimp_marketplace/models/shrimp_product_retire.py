"""Dar de baja (archivar), eliminar y reactivar un producto sin dejar stock
ni reservas sin dueño (hueco #9 / CR-11 de la auditoría de procesos).

Antes, «Dar de baja» y «Eliminar» eran un write(active/state) sin ninguna
guarda, por cuatro canales (portal, API v1, API antigua, back-office):

* se archivaba o eliminaba un producto con compras en curso, verificaciones o
  compromisos de cosecha vivos (la venta en tránsito apuntaba a un producto
  cancelado e inactivo);
* las solicitudes de chequeo abiertas seguían reservando cantidad de un
  producto que ya nadie podía aprobar («producto no activo»);
* el stock del producto eliminado seguía «disponible» en sus lotes (21
  productos con 2,38 M de unidades en el limbo en Pruebas);
* el portal reactivaba un producto ELIMINADO (cancel + activo: 6 «zombis").

Reglas (las mismas en todos los canales, porque viven en write()):

1. Archivar o eliminar se BLOQUEA si el producto tiene operaciones abiertas:
   compras no cerradas (incluye las que están en verificación), compromisos de
   cosecha vivos o reserva de cosecha vigente, órdenes de maquila abiertas…
   Es la misma lista que bloquea el traspaso de perfil
   (`_shrimp_retire_blockers`, ampliable por módulo).
2. Las solicitudes de chequeo abiertas (flujo legado) NO bloquean: se cierran
   como «Canceladas» con aviso a las dos partes y liberan su reserva.
3. Eliminar da de BAJA el stock propio del vendedor con un movimiento
   «Baja (producto eliminado)» por lote: el saldo queda trazado y los lotes
   pasan a consumidos. Archivar conserva el stock (se puede reactivar).
4. Un producto eliminado no se reactiva (ni desde el portal ni por API);
   uno archivado sí.

El contexto `shrimp_retire_force` salta las guardas (migraciones y scripts de
plataforma); no lo usa ningún controlador.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

_TX_CLOSED = ("done", "cancel")
WRITE_OFF_MOVE = "write_off"


class ShrimpStockMoveRetire(models.Model):
    _inherit = "shrimp.stock.move"

    move_type = fields.Selection(
        selection_add=[(WRITE_OFF_MOVE, "Baja (producto eliminado)")],
        ondelete={WRITE_OFF_MOVE: "set default"})


class ShrimpProductRetire(models.Model):
    _inherit = "shrimp.product"

    # ------------------------------------------------------------------
    # Qué impide retirar el producto
    # ------------------------------------------------------------------
    def _shrimp_retire_blockers(self):
        """Operaciones abiertas que impiden archivar o eliminar el producto
        (lista de textos). Los módulos con más procesos sobre el producto la
        amplían (reservas de cosecha, maquila…). Las solicitudes de chequeo
        NO están: se cierran solas al retirar el producto."""
        self.ensure_one()
        product = self.sudo()
        motivos = []
        abiertas = product.env["shrimp.transaction"].sudo().search_count([
            ("product_id", "=", product.id), ("state", "not in", _TX_CLOSED)])
        if abiertas:
            motivos.append(_("%s compra(s) en curso (sin cerrar o en verificación)") % abiertas)
        if "shrimp.harvest.commitment" in self.env:
            compromisos = self.env["shrimp.harvest.commitment"].sudo().search_count([
                ("product_id", "=", product.id),
                ("state", "in", ("sent", "accepted", "to_confirm"))])
            if compromisos:
                motivos.append(_("%s compromiso(s) de cosecha vigente(s)") % compromisos)
        return motivos

    def _shrimp_retire_problem(self, accion):
        """Texto del bloqueo o cadena vacía. `accion`: «dar de baja» / «eliminar»."""
        self.ensure_one()
        bloqueos = self._shrimp_retire_blockers()
        if not bloqueos:
            return ""
        return _("No se puede %(accion)s «%(nombre)s»: tiene %(que)s. Ciérralas primero "
                 "(o espera a que terminen); así ninguna compra ni reserva queda sin producto.") % {
            "accion": accion, "nombre": self.name or "", "que": "; ".join(bloqueos)}

    def shrimp_can_reactivate(self):
        """Solo se reactiva un producto ARCHIVADO, nunca uno eliminado."""
        self.ensure_one()
        return not self.active and self.state != "cancel"

    # ------------------------------------------------------------------
    # write(): guardas para todos los canales
    # ------------------------------------------------------------------
    def write(self, vals):
        if self.env.context.get("shrimp_retire_force"):
            return super().write(vals)
        eliminar = self.browse()
        archivar = self.browse()
        if vals.get("state") == "cancel":
            eliminar = self.filtered(lambda p: p.state != "cancel")
        if "active" in vals and not vals["active"]:
            archivar = self.filtered(lambda p: p.active) - eliminar
        if vals.get("active") and vals.get("state") in (None, "cancel"):
            zombis = self.filtered(lambda p: not p.active and p.state == "cancel")
            if zombis:
                raise UserError(_(
                    "«%s» fue eliminado y no se puede reactivar. Solo se reactivan los "
                    "productos archivados (dados de baja); publica uno nuevo si "
                    "necesitas volver a venderlo.") % (zombis[0].name or ""))
        for rec in eliminar:
            problema = rec._shrimp_retire_problem(_("eliminar"))
            if problema:
                raise UserError(problema)
        for rec in archivar:
            problema = rec._shrimp_retire_problem(_("dar de baja"))
            if problema:
                raise UserError(problema)
        retirados = eliminar | archivar
        if retirados:
            retirados._shrimp_close_check_requests()
        res = super().write(vals)
        if eliminar:
            eliminar._shrimp_write_off_stock()
        return res

    def _shrimp_close_check_requests(self):
        """Cierra las solicitudes de chequeo abiertas de los productos que se
        retiran (las cancela con aviso a comprador y vendedor)."""
        Check = self.env["shrimp.check.request"].sudo()
        abiertas = Check.search([
            ("product_id", "in", self.ids),
            ("state", "in", ("requested", "under_review"))])
        for chk in abiertas:
            chk._shrimp_close_open("cancelled", _(
                "La solicitud %(ref)s se canceló porque el vendedor retiró el producto "
                "«%(prod)s» del marketplace. La cantidad reservada se libera y no se "
                "ejecutó ninguna compra.") % {
                    "ref": chk.name or "", "prod": chk.product_id.name or ""})
        return abiertas

    def _shrimp_write_off_stock(self, reason=None):
        """Da de baja el stock propio del vendedor (producto eliminado): un
        movimiento «Baja» por lote, con saldo antes/después."""
        Lot = self.env["shrimp.stock.lot"].sudo()
        moves = self.env["shrimp.stock.move"]
        for rec in self:
            lots = Lot.search([
                ("product_id", "=", rec.id),
                ("owner_id", "=", rec.seller_partner_id.id),
                ("state", "=", "available"),
                ("available_qty", ">", 0)], order="id asc")
            for lot in lots:
                if float_compare(lot.available_qty, 0.0, precision_digits=6) <= 0:
                    continue
                moves |= lot._shrimp_internal_move(
                    WRITE_OFF_MOVE, lot.available_qty,
                    reason or _("Baja de stock: producto eliminado por el vendedor"))
        return moves
