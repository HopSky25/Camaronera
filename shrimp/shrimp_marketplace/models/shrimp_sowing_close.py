"""La cosecha cierra la siembra (decisión D-49, parte marketplace).

Registrar la cosecha de una piscina es crear el lote que sale de ella: el
producto con «Piscina de origen» y sus «Siembras de origen» (las propone
_shrimp_autofill_origin_allocations, también para la cosecha que registra la
camaronera desde una reserva de cosecha en shrimp_packer). En ese momento las
siembras vigentes («Asignado») de las que procede pasan a «Cerrada
(cosechada)». Antes quedaban «Asignado» para siempre (117 en Pruebas).

El cierre no mueve stock: «Asignado» y «Cerrada» consumen el lote igual.
"""
from odoo import api, models


class ShrimpProductSowingClose(models.Model):
    _inherit = "shrimp.product"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._shrimp_close_origin_sowings()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "origin_allocation_ids" in vals:
            self._shrimp_close_origin_sowings()
        return res

    def _shrimp_close_origin_sowings(self):
        abiertas = self.sudo().mapped("origin_allocation_ids").filtered(
            lambda a: a.state == "allocated")
        if abiertas:
            abiertas.with_context(shrimp_alloc_internal=True).write({"state": "released"})
        return abiertas

    @api.model
    def _shrimp_close_harvested_sowings_migrate(self):
        """Migración: cierra las siembras vigentes que ya son origen de una
        cosecha. Devuelve cuántas cerró."""
        productos = self.sudo().with_context(active_test=False).search(
            [("origin_allocation_ids", "!=", False)])
        return len(productos._shrimp_close_origin_sowings())
