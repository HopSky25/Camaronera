"""Libera las plantillas de correo para que el módulo pueda actualizarlas.

Nacieron dentro de un bloque ``noupdate="1"``, y Odoo guarda esa marca en
``ir_model_data`` al crear el registro: cambiar el XML después no la revierte,
así que las mejoras al texto de los correos nunca llegarían a una base ya
instalada. Esto la limpia una sola vez.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_model_data
           SET noupdate = false
         WHERE module = 'shrimp_verification'
           AND model = 'mail.template'
    """)
