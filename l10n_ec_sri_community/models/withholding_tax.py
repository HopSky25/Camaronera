# -*- coding: utf-8 -*-
"""Catálogo de códigos de retención SRI (Tabla 19 renta / Tabla 20-21 IVA /
ISD), portado desde la suite odoo_saas_ecuador (l10n_ec.withholding.tax) a
nuestra convención. Datos de referencia de solo lectura; sirve para elegir
el código en las líneas de retención de un comprobante 07 sin teclearlo."""

from odoo import fields, models


class EcSriWithholdingTax(models.Model):
    _name = 'ec.sri.withholding.tax'
    _description = 'Código de retención SRI'
    _order = 'type, code'
    _rec_name = 'display_name'

    code = fields.Char(string='Código', required=True, index=True,
                       help='Código SRI (ej. 303, 332B, 343A).')
    name = fields.Char(string='Descripción', required=True, translate=True)
    type = fields.Selection(
        [('renta', 'Renta (Tabla 19)'), ('iva', 'IVA (Tabla 20/21)'), ('isd', 'ISD')],
        string='Tipo', required=True, index=True)
    percentage = fields.Float(string='Porcentaje %', digits=(12, 2), required=True)
    active = fields.Boolean(default=True)

    display_name = fields.Char(compute='_compute_display_name')

    _code_type_unique = models.Constraint('UNIQUE(code, type)',
                                          'El código de retención debe ser único por tipo.')

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = '[%s] %s' % (rec.code or '', rec.name or '')
