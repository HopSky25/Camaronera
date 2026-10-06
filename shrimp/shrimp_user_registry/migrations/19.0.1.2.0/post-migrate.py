"""19.0.1.2.0 — perfil de empresa común e identificación estándar.

1. Los datos que cada rol guardaba en sus propios campos (lab_*, farm_*) pasan
   al perfil común (shrimp_razon_social, shrimp_representante,
   shrimp_telefono, shrimp_ubicacion, capacidad + unidad). Las columnas viejas
   quedan en la base (Odoo no las borra) y los nombres viejos siguen
   funcionando como alias durante una versión. Los módulos de cada rol copian
   lo suyo en su propia migración (emp_*, ver_*, pack_*).
2. `vat` (el campo estándar que usa la facturación electrónica) estaba vacío
   en todos los socios: se rellena con vat_or_id normalizado.

Idempotente: solo rellena lo que está vacío.
"""


from odoo.addons.shrimp_user_registry.migration_utils import copiar_perfil


def migrate(cr, version):
    if not version:
        return
    copiar_perfil(cr, {
        "shrimp_razon_social": ["lab_razon_social"],
        "shrimp_ubicacion": ["lab_ubicacion"],
    }, tipo="laboratorio")
    copiar_perfil(cr, {
        "shrimp_razon_social": ["farm_razon_social"],
        "shrimp_representante": ["farm_representante"],
        "shrimp_telefono": ["farm_telefono"],
        "shrimp_ubicacion": ["farm_ubicacion"],
    }, tipo="camaronera", unidad_capacidad="ton_year", columna_capacidad="farm_capacidad")

    # Identificación estándar desde vat_or_id (mayúsculas, sin separadores).
    cr.execute(r"""
        UPDATE res_partner
           SET vat = upper(regexp_replace(vat_or_id, '[^A-Za-z0-9]', '', 'g'))
         WHERE shrimp_user_type IS NOT NULL
           AND (vat IS NULL OR vat = '')
           AND COALESCE(regexp_replace(vat_or_id, '[^A-Za-z0-9]', '', 'g'), '') <> ''
    """)
