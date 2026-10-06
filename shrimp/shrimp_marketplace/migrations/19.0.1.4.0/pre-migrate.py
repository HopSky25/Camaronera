"""19.0.1.4.0 — certificados de producto con revisión (pre).

Se agrega shrimp.product.certificate.line.status (pendiente/aprobado/
rechazado), con valor por defecto "pending" para lo NUEVO. Si se dejara al
ORM crear la columna, rellenaría con "pending" también los certificados que
YA se mostraban en el marketplace y desaparecerían de golpe de las fichas
públicas. Decisión: lo existente queda "approved" (estaba publicado y nadie
lo había objetado; el administrador puede rechazarlo desde la nueva cola) y
solo lo que se suba a partir de ahora pasa por revisión.

Se crea la columna aquí, antes que el ORM, para poder distinguir. Idempotente.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT 1 FROM information_schema.tables
         WHERE table_name = 'shrimp_product_certificate_line'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        ALTER TABLE shrimp_product_certificate_line
        ADD COLUMN IF NOT EXISTS status varchar
    """)
    cr.execute("""
        UPDATE shrimp_product_certificate_line
           SET status = 'approved'
         WHERE status IS NULL
    """)
