"""Un lote con empaque (maquila) abierto no se archiva ni se elimina.

Amplía `shrimp.product._shrimp_retire_blockers` (shrimp_marketplace,
auditoría de procesos hueco #9): la solicitud publicada/adjudicada o la orden
en curso apuntan al lote; retirarlo dejaría el empaque sin producto."""
from odoo import _, models


class ShrimpProductRetireCopack(models.Model):
    _inherit = "shrimp.product"

    def _shrimp_retire_blockers(self):
        motivos = super()._shrimp_retire_blockers()
        product = self.sudo()
        ordenes = self.env["shrimp.copack.order"].sudo().search_count([
            ("product_id", "=", product.id),
            ("state", "in", ("confirmed", "received", "packed"))])
        if ordenes:
            motivos.append(_("%s orden(es) de empaque en curso") % ordenes)
        solicitudes = self.env["shrimp.copack.request"].sudo().search_count([
            ("product_id", "=", product.id),
            ("state", "in", ("published", "assigned"))])
        if solicitudes:
            motivos.append(_("%s solicitud(es) de empaque abierta(s)") % solicitudes)
        return motivos
