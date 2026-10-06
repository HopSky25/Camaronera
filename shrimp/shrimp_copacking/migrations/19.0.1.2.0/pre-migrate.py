"""19.0.1.2.0 (pre) — la presentación empacada deja de ser texto libre.

shrimp.copack.order.packed_presentation era un Char ("cola directa", "entero
IQF", "VA"...) y pasa a ser la selección única de presentaciones (entero,
cola, valor agregado). El texto original se conserva en
packed_presentation_note y el valor se traduce cuando se puede reconocer; lo
que no se reconoce queda vacío (el texto sigue en la nota). Idempotente.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""SELECT 1 FROM information_schema.tables
                   WHERE table_name = 'shrimp_copack_order'""")
    if not cr.fetchone():
        return
    cr.execute("""ALTER TABLE shrimp_copack_order
                  ADD COLUMN IF NOT EXISTS packed_presentation_note varchar""")
    cr.execute("""
        UPDATE shrimp_copack_order
           SET packed_presentation_note = packed_presentation
         WHERE packed_presentation IS NOT NULL AND packed_presentation <> ''
           AND packed_presentation NOT IN ('entero', 'cola', 'valor_agregado')
           AND packed_presentation_note IS NULL
    """)
    cr.execute("""
        UPDATE shrimp_copack_order
           SET packed_presentation = CASE
               WHEN lower(packed_presentation) ~ '(valor|agregad|pelad|desvenad|cocid|butterfly|\\mva\\M)'
                    THEN 'valor_agregado'
               WHEN lower(packed_presentation) ~ 'cola' THEN 'cola'
               WHEN lower(packed_presentation) ~ '(enter|cuerpo|hoso|head)' THEN 'entero'
               ELSE NULL END
         WHERE packed_presentation IS NOT NULL
           AND packed_presentation NOT IN ('entero', 'cola', 'valor_agregado')
    """)
