"""19.0.1.8.0 — GlobalG.A.P. deja de aplicar a «Todos» los perfiles.

El certificado semilla shrimp_certificate_globalgap (noupdate) se creó con
role='all', así que el registro lo ofrecía a laboratorios, empacadoras,
verificadores... Es un certificado de la finca: pasa a 'camaronera'.

Solo se toca el registro semilla (por su xmlid) y solo si sigue en 'all' (si
la administración ya lo cambió a mano, se respeta). Las líneas de certificado
que ya tienen los socios no se tocan. Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE shrimp_certificate c
           SET role = 'camaronera'
          FROM ir_model_data d
         WHERE d.module = 'shrimp_user_registry'
           AND d.name = 'shrimp_certificate_globalgap'
           AND d.model = 'shrimp.certificate'
           AND d.res_id = c.id
           AND c.role = 'all'
    """)
    if cr.rowcount:
        _logger.info("GlobalG.A.P.: el certificado del catálogo pasa de 'Todos' a 'Camaronera'.")
