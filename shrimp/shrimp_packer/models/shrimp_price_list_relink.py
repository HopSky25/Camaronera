"""Lote atado a una lista archivada (decisión D-49, parte empacadora).

Cuando se archiva la lista de precios de la que un lote toma su precio (al
publicar la siguiente o a mano), el lote pasa a la lista NUEVA vigente del
mismo emisor que su vendedor puede ver (la vigente antes que la próxima; la
más reciente primero) y su precio se resincroniza con ella, con las reglas
de siempre (un lote con compras no cambia de precio). Si el emisor no tiene
otra lista para ese vendedor, el lote se desata (conserva su precio y el
vendedor lo puede volver a editar). En los dos casos se avisa al vendedor.
"""
from odoo import api, models, _


class ShrimpPriceListRelink(models.Model):
    _inherit = "shrimp.price.list"

    def write(self, vals):
        archivando = self.filtered(lambda l: l.state != "archived") \
            if vals.get("state") == "archived" else self.browse()
        res = super().write(vals)
        if archivando:
            archivando._shrimp_relink_products()
        return res

    def _shrimp_replacement_for(self, seller):
        """Lista que reemplaza a esta para `seller`: publicada, del mismo
        emisor, visible para él; vigente antes que próxima."""
        self.ensure_one()
        candidatas = self.sudo().search([
            ("issuer_partner_id", "=", self.issuer_partner_id.id),
            ("state", "=", "published"), ("id", "!=", self.id),
        ], order="issue_date desc, id desc").filtered(
            lambda l: (l.is_current or l.is_upcoming) and l.visible_para(seller))
        vigentes = candidatas.filtered("is_current")
        return (vigentes or candidatas)[:1]

    def _shrimp_relink_products(self):
        """Mueve (o desata) los lotes atados a estas listas archivadas.
        Devuelve (movidos, desatados)."""
        Product = self.env["shrimp.product"].sudo().with_context(active_test=False)
        movidos = desatados = Product.browse()
        for lista in self:
            for prod in Product.search([("price_list_id", "=", lista.id)]):
                nueva = lista._shrimp_replacement_for(prod.seller_partner_id)
                prod_ctx = prod.with_context(shrimp_system_flow=True, skip_evolution_snapshot=True)
                if nueva:
                    prod_ctx.write({"price_list_id": nueva.id})
                    movidos |= prod
                    texto = _(
                        "La lista de precios «%(old)s» de %(emisor)s se archivó: el lote «%(n)s» "
                        "pasó a su lista vigente «%(new)s» y toma su precio de ella (si el lote ya "
                        "tiene compras, el precio no cambia).") % {
                            "old": lista.name, "emisor": lista.issuer_partner_id.name or "",
                            "n": prod.name, "new": nueva.name}
                else:
                    prod_ctx.write({"price_list_id": False})
                    desatados |= prod
                    texto = _(
                        "La lista de precios «%(old)s» de %(emisor)s se archivó y no hay otra lista "
                        "suya para ti: el lote «%(n)s» quedó sin lista, conserva su precio actual "
                        "y puedes volver a editarlo.") % {
                            "old": lista.name, "emisor": lista.issuer_partner_id.name or "",
                            "n": prod.name}
                if prod.active and prod.state in ("draft", "published", "sold"):
                    prod._shrimp_notify_seller(
                        _("Lista de precios del lote %s actualizada") % prod.name, texto)
        return movidos, desatados

    @api.model
    def _shrimp_relink_archived_migrate(self):
        """Migración: lotes que hoy siguen atados a una lista archivada."""
        archivadas = self.sudo().search([("state", "=", "archived")])
        ligadas = archivadas.filtered(lambda l: self.env["shrimp.product"].sudo().with_context(
            active_test=False).search_count([("price_list_id", "=", l.id)]))
        return ligadas._shrimp_relink_products()
