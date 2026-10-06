"""19.0.1.11.0 — mover producto propio entre los perfiles de la cuenta.

Nuevo campo shrimp.stock.lot.held_role («perfil que tiene el lote»). Se
rellena para los lotes que ya existían:

1. Stock propio (el dueño del lote es el vendedor del producto): el rol del
   producto (seller_role).
2. Lote recibido por una compra: el perfil con el que compró
   (shrimp_transaction.buyer_role de la transacción de su movimiento de origen).
3. Lo demás: el perfil activo del dueño (o el de su empresa).

Idempotente: solo toca filas con held_role vacío.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE shrimp_stock_lot l
           SET held_role = p.seller_role
          FROM shrimp_product p
         WHERE l.product_id = p.id
           AND l.owner_id = p.seller_partner_id
           AND l.held_role IS NULL
           AND p.seller_role IS NOT NULL
    """)
    cr.execute("""
        UPDATE shrimp_stock_lot l
           SET held_role = t.buyer_role
          FROM shrimp_stock_move m
          JOIN shrimp_transaction t ON t.id = m.transaction_id
         WHERE l.origin_move_id = m.id
           AND t.buyer_partner_id = l.owner_id
           AND t.buyer_role IS NOT NULL
           AND l.held_role IS NULL
    """)
    cr.execute("""
        UPDATE shrimp_stock_lot l
           SET held_role = COALESCE(rp.shrimp_user_type, parent.shrimp_user_type)
          FROM res_partner rp
          LEFT JOIN res_partner parent ON parent.id = rp.parent_id
         WHERE rp.id = l.owner_id
           AND l.held_role IS NULL
           AND COALESCE(rp.shrimp_user_type, parent.shrimp_user_type) IS NOT NULL
    """)
