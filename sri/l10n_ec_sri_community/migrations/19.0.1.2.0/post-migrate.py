# -*- coding: utf-8 -*-
"""19.0.1.2.0: corrige los códigos de IVA que puso el hook de instalación anterior.

Clasificaba por tarifa, así que dejaba como "IVA 0 %" el no objeto, el exento y hasta
retenciones del 0 %, y ponía el 5 % con el código 8 (que es "IVA diferenciado"; el 5 %
es el código 5, tabla 17 de la ficha técnica). Solo se tocan los valores que coinciden
exactamente con lo que dejó ese hook; lo configurado a mano no se cambia.
"""
import logging
from odoo import api, SUPERUSER_ID
from odoo.addons.l10n_ec_sri_community.hooks import IVA_POR_GRUPO

_logger = logging.getLogger(__name__)
OLD_HOOK = {0.0: ('2', '0', 'zero'), 5.0: ('2', '8', 'taxable')}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    taxes = env['account.tax'].with_context(active_test=False).search([('ec_sri_tax_code', '=', '2')])
    fixed = cleared = 0
    for tax in taxes:
        old = OLD_HOOK.get(round(tax.amount, 2))
        if not old or (tax.ec_sri_tax_code, tax.ec_sri_percentage_code, tax.ec_sri_ats_bucket) != old:
            continue
        group_type = tax.tax_group_id.l10n_ec_type
        if group_type in IVA_POR_GRUPO:
            code, bucket = IVA_POR_GRUPO[group_type]
            if (code, bucket) != old[1:]:
                tax.write({'ec_sri_percentage_code': code, 'ec_sri_ats_bucket': bucket})
                fixed += 1
        elif group_type:
            # Retenciones, ICE, etc.: no son IVA del XML de venta o compra.
            tax.write({'ec_sri_tax_code': False, 'ec_sri_percentage_code': False, 'ec_sri_ats_bucket': False})
            cleared += 1
    _logger.info('SRI 19.0.1.1.0: %s impuesto(s) de IVA corregido(s), %s impuesto(s) no IVA desmarcado(s).', fixed, cleared)
