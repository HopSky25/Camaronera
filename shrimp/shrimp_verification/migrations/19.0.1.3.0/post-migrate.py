"""19.0.1.3.0 — perfil común, cuenta bancaria estándar y cobros del honorario.

1. ver_razon_social / ver_representante / ver_telefono / ver_ubicacion /
   ver_capacidad_lotes_dia pasan al perfil común (los nombres viejos quedan
   como alias una versión).
2. La identificación fiscal del verificador (ver_ruc, la de "Facturación")
   pasa a `vat` cuando `vat` está vacío o es solo la copia de vat_or_id.
3. La cuenta bancaria (ver_bank_*) pasa a res.partner.bank, el modelo estándar
   que usan Contabilidad y los pagos.
4. Las verificaciones ya facturadas con la lógica anterior (pedido/factura
   guardados en la propia verificación) reciben su cobro (shrimp.charge) para
   que la factura siga visible desde la verificación.

Idempotente: cada paso solo actúa sobre lo que falta.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.shrimp_user_registry.migration_utils import columnas, copiar_perfil


def migrate(cr, version):
    if not version:
        return
    copiar_perfil(cr, {
        "shrimp_razon_social": ["ver_razon_social"],
        "shrimp_representante": ["ver_representante"],
        "shrimp_telefono": ["ver_telefono"],
        "shrimp_ubicacion": ["ver_ubicacion"],
    }, tipo="verificador", unidad_capacidad="lots_day",
        columna_capacidad="ver_capacidad_lotes_dia")

    cols = columnas(cr, "res_partner")
    if "ver_ruc" in cols:
        cr.execute(r"""
            UPDATE res_partner
               SET vat = upper(regexp_replace(ver_ruc, '[^A-Za-z0-9]', '', 'g'))
             WHERE shrimp_user_type = 'verificador'
               AND COALESCE(regexp_replace(ver_ruc, '[^A-Za-z0-9]', '', 'g'), '') <> ''
               AND (vat IS NULL OR vat = ''
                    OR vat = upper(regexp_replace(COALESCE(vat_or_id, ''), '[^A-Za-z0-9]', '', 'g')))
        """)

    env = api.Environment(cr, SUPERUSER_ID, {})
    if "ver_bank_account_number" in cols:
        cr.execute("""
            SELECT id, ver_bank_name, ver_bank_account_type, ver_bank_account_number,
                   ver_bank_holder, ver_bank_holder_id
              FROM res_partner
             WHERE COALESCE(ver_bank_account_number, '') <> ''
        """)
        for pid, banco, tipo, numero, titular, titular_id in cr.fetchall():
            socio = env["res.partner"].browse(pid)
            if socio.bank_ids.filtered(lambda b: b.acc_number == numero.strip()):
                continue
            socio._shrimp_set_bank_account(
                bank_name=banco, account_type=tipo, number=numero,
                holder=titular, holder_id=titular_id)

    vcols = columnas(cr, "shrimp_verification")
    if "invoice_id" in vcols:
        cr.execute("""
            SELECT v.id FROM shrimp_verification v
             WHERE v.invoice_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM shrimp_charge c WHERE c.verification_id = v.id)
        """)
        ids = [r[0] for r in cr.fetchall()]
        Charge = env["shrimp.charge"].with_context(shrimp_charge_no_invoice=True)
        for ver in env["shrimp.verification"].browse(ids):
            cr.execute("SELECT invoice_id, sale_order_id FROM shrimp_verification WHERE id = %s",
                       (ver.id,))
            factura_id, pedido_id = cr.fetchone()
            factura = env["account.move"].browse(factura_id).exists()
            pagador = ver.fee_payer_partner_id or factura.partner_id or ver.buyer_partner_id
            cobro = Charge._register_charge({
                "charge_type": "verification_fee",
                "verification_id": ver.id,
                "transaction_id": ver.transaction_id.id,
                "payer_partner_id": pagador.id,
                "buyer_partner_id": ver.buyer_partner_id.id,
                "seller_partner_id": ver.seller_partner_id.id,
                "amount": ver.fee or factura.amount_untaxed,
                "invoice_qty": 1.0,
                "origin": ver.name,
                "description": "Verificación en campo – %s" % (ver.name or ""),
            })
            cobro.write({
                "state": "invoiced" if factura.state == "posted" else "error",
                "invoice_id": factura.id or False,
                "sale_order_id": pedido_id or False,
            })
