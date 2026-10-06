"""19.0.1.5.0 (post) — cobros de la plataforma unificados.

shrimp.charge pasa a ser el registro único de todo lo que la plataforma
factura (comisión, honorario de verificación, comisión de empaque). Los cobros
que ya existían son todos comisiones:

* tipo «commission», paga el vendedor, documento de origen = la compra;
* si ya tenían factura contabilizada quedan «Facturado»;
* si no, quedan «Error al facturar» y FUERA del reintento automático (con el
  motivo a la vista): la versión anterior dejaba el pedido de venta enlazado
  aunque la factura fallara, y reintentarlos solos al actualizar emitiría de
  golpe facturas viejas. Se revisan y se reintentan con el botón.

No se crean cobros retroactivos para compras ya cerradas sin cobro.
Idempotente.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.shrimp_marketplace.models.shrimp_charge import MAX_AUTO_ATTEMPTS


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE shrimp_charge c
           SET charge_type = COALESCE(c.charge_type, 'commission'),
               payer_partner_id = COALESCE(c.payer_partner_id, c.seller_partner_id),
               origin = COALESCE(c.origin, t.name)
          FROM shrimp_transaction t
         WHERE t.id = c.transaction_id
    """)
    # Línea de factura de la comisión: unidades vendidas x tarifa. Se fija
    # sin condición (es idempotente): la columna nueva pudo nacer con el
    # valor por defecto (1) en las filas existentes.
    cr.execute("""
        UPDATE shrimp_charge
           SET invoice_qty = COALESCE(NULLIF(qty, 0), 1),
               unit_amount = COALESCE(rate_cents, 0) / 100.0
         WHERE charge_type = 'commission' AND COALESCE(rate_cents, 0) > 0
    """)
    cr.execute("""
        UPDATE shrimp_charge
           SET charge_type = COALESCE(charge_type, 'commission'),
               payer_partner_id = COALESCE(payer_partner_id, seller_partner_id)
         WHERE charge_type IS NULL OR payer_partner_id IS NULL
    """)
    cr.execute("""
        UPDATE shrimp_charge c SET company_id = (SELECT id FROM res_company ORDER BY id LIMIT 1)
         WHERE c.company_id IS NULL
    """)
    # Estado según los documentos que ya tenía.
    cr.execute("""
        UPDATE shrimp_charge c SET state = 'invoiced'
          FROM account_move m
         WHERE m.id = c.invoice_id AND m.state = 'posted'
           AND (c.state IS NULL OR c.state = 'pending')
    """)
    cr.execute("""
        UPDATE shrimp_charge
           SET state = 'error',
               invoice_attempts = GREATEST(COALESCE(invoice_attempts, 0), %s),
               invoice_error = 'Cobro anterior a la versión 19.0.1.5.0 sin factura '
                               'contabilizada. Revise el pedido de venta enlazado (si lo hay) '
                               'y reintente la facturación desde la ficha.'
         WHERE (state IS NULL OR state = 'pending') AND invoice_id IS NULL
           AND COALESCE(invoice_attempts, 0) = 0
    """, (MAX_AUTO_ATTEMPTS,))

    # Las tres plataformas en español (si el idioma está instalado). Solo en
    # esta migración: después manda lo que configure el administrador.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["website"].search([])._shrimp_set_spanish_default()
