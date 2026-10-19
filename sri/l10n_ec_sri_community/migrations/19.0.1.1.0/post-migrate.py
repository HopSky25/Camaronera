# -*- coding: utf-8 -*-
"""19.0.1.1.0: las facturas y notas se numeran desde el punto de emisión.

- Enlaza cada línea del punto con su tipo de documento nativo de Odoo.
- Hasta ahora el número lo ponía el diario, así que la secuencia del punto para
  01/03/04/05 nunca avanzó. Se lleva al número siguiente al último ya usado con ese
  establecimiento y punto, para no repetir un número ante el SRI.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['ec.sri.point.line'].with_context(active_test=False).search([])
    # Recalcular por el ORM (no por write(), que bloquea cambiar el documento de una línea).
    env.add_to_compute(lines._fields['l10n_latam_document_type_id'], lines)
    env.flush_all()
    lines._ec_sri_skip_used()
