"""19.0.1.1.0 — endurecimiento de seguridad.

- res.partner.shrimp_account_state: los contactos que ya existían quedan
  "approved" (el valor por defecto de la columna ya lo hace al crearla; esto
  cubre cualquier NULL residual). Idempotente.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE res_partner
           SET shrimp_account_state = 'approved'
         WHERE shrimp_account_state IS NULL
    """)
