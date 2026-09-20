# -*- coding: utf-8 -*-
"""Establecimientos SRI = sucursales nativas de Odoo (res.company branches).

No se crea una tabla nueva: se aprovecha la jerarquía nativa de compañías
(parent_id/child_ids = "Branches") y el hecho de que cada pos.config ya
pertenece a una compañía. Así, un establecimiento (sucursal) muestra los
puntos de venta asignados y, por cada uno, su punto de emisión SRI."""

from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ec_sri_establishment_code = fields.Char(
        string='Código establecimiento SRI', size=3,
        help='Número de establecimiento registrado en el RUC (ej. 001 para la '
             'matriz, 002 para la sucursal).')
    pos_config_ids = fields.One2many(
        'pos.config', 'company_id', string='Puntos de venta')
    ec_sri_pos_count = fields.Integer(
        string='N° de POS', compute='_compute_ec_sri_pos_count')

    @api.depends('pos_config_ids')
    def _compute_ec_sri_pos_count(self):
        for company in self:
            company.ec_sri_pos_count = len(company.pos_config_ids)
