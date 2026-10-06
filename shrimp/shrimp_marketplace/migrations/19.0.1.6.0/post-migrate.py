"""19.0.1.6.0 — varios perfiles por cuenta.

Las transacciones guardan ahora con qué PERFIL actuó cada parte
(seller_role, buyer_role). En las existentes:
- seller_role sale del tipo de transacción (semillero_to_laboratorio ->
  semillero, etc.), que es exactamente el rol con el que se vendió;
- buyer_role, del tipo del comprador (o de su empresa si es un contacto hijo),
  que antes de esta versión era su único rol.
Idempotente: solo rellena lo vacío.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE shrimp_transaction
           SET seller_role = CASE transaction_type
                   WHEN 'semillero_to_laboratorio' THEN 'semillero'
                   WHEN 'laboratorio_to_camaronera' THEN 'laboratorio'
                   WHEN 'camaronera_to_buyer' THEN 'camaronera' END
         WHERE seller_role IS NULL
    """)
    cr.execute("""
        UPDATE shrimp_transaction t
           SET buyer_role = COALESCE(b.shrimp_user_type, bp.shrimp_user_type)
          FROM res_partner b
          LEFT JOIN res_partner bp ON bp.id = b.parent_id
         WHERE b.id = t.buyer_partner_id
           AND t.buyer_role IS NULL
           AND COALESCE(b.shrimp_user_type, bp.shrimp_user_type) IS NOT NULL
    """)
