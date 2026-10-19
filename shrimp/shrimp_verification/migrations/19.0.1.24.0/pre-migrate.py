"""Decisiones de la auditoría de procesos (19.0.1.24.0).

1) D-25 (segunda opinión): la compra puede tener una segunda verificación,
   así que el UNIQUE(transaction_id) pasa a ser un índice único PARCIAL
   (solo las verificaciones no superadas). Se borra aquí el constraint viejo;
   el -u crea el índice nuevo (shrimp_verification__uniq_current_verification_per_tx).
2) D-21 (respuesta de la verificadora): columna nueva verifier_response. Las
   órdenes de plataforma que ya existían quedan «accepted» (no se les abre un
   plazo de respuesta retroactivo); las declaradas, vacías.
3) D-24 (liquidación): los veredictos que ya existían sin liquidación quedan
   «legacy» (Histórico — no se liquida, decisión del usuario); los que ya
   tenían factura de proveedor, «billed»; el resto, «none».
Columnas nuevas creadas aquí para fijar el valor de las filas existentes sin
pasar por el ORM.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("ALTER TABLE shrimp_verification "
               "DROP CONSTRAINT IF EXISTS shrimp_verification_uniq_verification_per_tx")
    cr.execute("ALTER TABLE shrimp_verification ADD COLUMN IF NOT EXISTS verifier_response varchar")
    cr.execute("""UPDATE shrimp_verification SET verifier_response = 'accepted'
                   WHERE verification_mode = 'platform' AND verifier_response IS NULL""")
    _logger.info("D-21: %s verificaciones existentes quedan con la orden aceptada", cr.rowcount)
    cr.execute("ALTER TABLE shrimp_verification ADD COLUMN IF NOT EXISTS payout_state varchar")
    # Decisión del usuario: los veredictos históricos sin liquidar (261 en
    # Pruebas) quedan «Histórico — no se liquida»; el resto, «none».
    cr.execute("""UPDATE shrimp_verification SET payout_state = 'legacy'
                   WHERE payout_state IS NULL AND verification_mode = 'platform'
                     AND state IN ('approved', 'approved_obs', 'rejected')
                     AND vendor_bill_id IS NULL""")
    _logger.info("D-24: %s veredictos históricos quedan «no se liquidan»", cr.rowcount)
    cr.execute("""UPDATE shrimp_verification SET payout_state = 'billed'
                   WHERE payout_state IS NULL AND vendor_bill_id IS NOT NULL""")
    cr.execute("UPDATE shrimp_verification SET payout_state = 'none' WHERE payout_state IS NULL")
    cr.execute("ALTER TABLE shrimp_verification ADD COLUMN IF NOT EXISTS superseded boolean")
