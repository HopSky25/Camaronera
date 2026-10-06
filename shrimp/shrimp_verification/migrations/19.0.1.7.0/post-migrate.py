"""19.0.1.7.0 — comprobación posterior del modo de verificación.

Si alguna verificación quedó sin modo (no debería: el pre-migrate y el valor
por defecto lo cubren), se marca como 'platform', que es como se hicieron
todas las anteriores a esta versión. Idempotente.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE shrimp_verification SET verification_mode = 'platform' "
               "WHERE verification_mode IS NULL")
    cr.execute("""
        UPDATE shrimp_transaction t
           SET verification_mode = v.verification_mode
          FROM shrimp_verification v
         WHERE v.transaction_id = t.id
           AND (t.verification_mode IS NULL OR t.verification_mode != v.verification_mode)
    """)
