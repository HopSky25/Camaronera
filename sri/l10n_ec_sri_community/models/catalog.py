# -*- coding: utf-8 -*-
from odoo import api, fields, models


class EcSriCatalog(models.Model):
    """Catálogos oficiales del SRI (tablas de la ficha técnica del esquema
    offline). Son datos de referencia de solo lectura: se precargan por data
    y el usuario únicamente los consulta. Cada registro pertenece a una de las
    tablas de la ficha (comprobantes, ambiente, tarifas de IVA/ICE,
    retenciones, formas de pago, países, etc.)."""

    _name = 'ec.sri.catalog'
    _description = 'Catálogo SRI (tablas de la ficha técnica)'
    _order = 'table_code, sequence, code'
    _rec_name = 'display_name'

    # Solo catálogos SIN equivalente nativo en Odoo. Los que sí lo tienen se
    # consumen de la tabla nativa (comprobante -> l10n_latam.document.type,
    # ambiente -> Selection en la compañía, forma de pago -> l10n_ec.sri.payment,
    # identificación -> l10n_latam.identification.type).
    TABLES = [
        ('emision', 'Tabla 2 - Tipo de emisión'),
        ('impuesto', 'Tabla 16 - Códigos de impuesto'),
        ('iva', 'Tabla 17 - Tarifa del IVA'),
        ('ice', 'Tabla 18 - Tarifa del ICE'),
        ('impuesto_retener', 'Tabla 19 - Impuesto a retener'),
        ('ret_iva', 'Tabla 20 - Retención del IVA'),
        ('pais', 'Tabla 25 - Países'),
        ('reembolso', 'Tabla 26 - Tipo proveedor de reembolso'),
    ]

    table_code = fields.Selection(
        TABLES, string='Tabla', required=True, index=True)
    code = fields.Char(string='Código', required=True, index=True)
    name = fields.Char(string='Descripción', required=True)
    value = fields.Char(
        string='Valor / Tarifa',
        help='Dato adicional de la tabla: porcentaje, tarifa u observación.')
    sequence = fields.Integer(string='Secuencia', default=10)
    active = fields.Boolean(string='Activo', default=True)

    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = '[%s] %s' % (rec.code or '', rec.name or '')
