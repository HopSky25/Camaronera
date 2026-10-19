# -*- coding: utf-8 -*-
"""19.0.1.3.0: permisos propios del módulo (Ecuador SRI: NO / Usuario / Administrador).

Antes el acceso venía de los grupos de Contabilidad. Para que nadie pierda lo que ya
hacía al actualizar: el Administrador de Contabilidad pasa a Administrador de Ecuador SRI
y quien factura pasa a Usuario. Desde aquí se ajusta en la ficha de cada usuario.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    manager = env.ref('l10n_ec_sri_community.group_sri_manager')
    user = env.ref('l10n_ec_sri_community.group_sri_user')
    accountants = env.ref('account.group_account_manager').all_user_ids.filtered(lambda u: not u.share)
    invoicers = env.ref('account.group_account_invoice').all_user_ids.filtered(lambda u: not u.share) - accountants
    manager.write({'user_ids': [(4, u.id) for u in accountants]})
    user.write({'user_ids': [(4, u.id) for u in invoicers]})
