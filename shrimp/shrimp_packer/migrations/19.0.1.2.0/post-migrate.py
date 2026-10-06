"""19.0.1.2.0 — perfil común de la empacadora.

emp_razon_social / emp_representante / emp_telefono / emp_planta_ubicacion /
emp_capacidad_lb_dia pasan al perfil común (shrimp_user_registry). Los
nombres viejos quedan como alias una versión. Idempotente.
"""
from odoo.addons.shrimp_user_registry.migration_utils import copiar_perfil


def migrate(cr, version):
    if not version:
        return
    copiar_perfil(cr, {
        "shrimp_razon_social": ["emp_razon_social"],
        "shrimp_representante": ["emp_representante"],
        "shrimp_telefono": ["emp_telefono"],
        "shrimp_ubicacion": ["emp_planta_ubicacion"],
    }, tipo="empacadora", unidad_capacidad="lb_day", columna_capacidad="emp_capacidad_lb_dia")
