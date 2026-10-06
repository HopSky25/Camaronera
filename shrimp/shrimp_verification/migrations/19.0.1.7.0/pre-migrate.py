"""19.0.1.7.0 — modo de verificación (plataforma / declarada por las partes).

Todas las verificaciones existentes se hicieron con una verificadora de la
plataforma: se crea la columna `verification_mode` ya rellena con 'platform'
ANTES de que el ORM la declare obligatoria (así no hay ni un instante con
nulos ni el aviso de columna NOT NULL sin valor), y lo mismo para el campo
almacenado de la compra (shrimp_transaction.verification_mode).

Idempotente: solo rellena lo que está vacío.
"""


def _columna(cr, tabla, columna):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (tabla, columna))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    if not _columna(cr, "shrimp_verification", "verification_mode"):
        cr.execute("ALTER TABLE shrimp_verification ADD COLUMN verification_mode varchar")
    cr.execute("UPDATE shrimp_verification SET verification_mode = 'platform' "
               "WHERE verification_mode IS NULL")
    if not _columna(cr, "shrimp_transaction", "verification_mode"):
        cr.execute("ALTER TABLE shrimp_transaction ADD COLUMN verification_mode varchar")
    cr.execute("""
        UPDATE shrimp_transaction t
           SET verification_mode = v.verification_mode
          FROM shrimp_verification v
         WHERE v.transaction_id = t.id
           AND t.verification_mode IS NULL
    """)
