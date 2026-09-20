# -*- coding: utf-8 -*-
"""Las larvas se cotizan por MILLARES, no por libras.

Los 150 lotes de larva de la demo se sembraron con uom_libra porque el XML
ponía esa unidad para todo. Un nauplio pesa del orden de microgramos: "Nauplio
Vannamei — 3.300 Libras" es una cifra que a cualquiera del sector le chirría al
instante, y es de las primeras que se ven al abrir el catálogo.

El XML ya quedó corregido, pero los registros de demo llevan noupdate="1": el
xml_id se creó con esa marca y cambiar el fichero no los actualiza. Sin esta
migración, quien tome los cambios sigue viendo nauplios en libras.

Se corrige por SQL a propósito: uom_id está en _LOCKED_AFTER_PURCHASE, y con
razón —no se le cambia la unidad a un lote que alguien ya compró—, pero esto es
dato de demo sembrado por XML sin pasar por esa regla.

El precio NO se toca. Lo que estaba mal era la etiqueta de la unidad, no el
número: el lote vale lo que vale. Cambiar ambos a la vez dejaría el histórico
de compras sin explicación.
"""


def migrate(cr, version):
    if not version:
        # Instalación en limpio: el XML ya siembra la unidad correcta.
        return

    # Engorde y juvenil se quedan en libras: el camarón adulto se pesa así, y
    # los partes de planta también.
    cr.execute("""
        UPDATE shrimp_product p
           SET uom_id = (SELECT id FROM shrimp_uom WHERE code = 'millar' LIMIT 1)
          FROM shrimp_stage s
         WHERE s.id = p.stage_id
           AND s.code NOT IN ('ENGORDE', 'JUVENIL')
           AND p.uom_id <> (SELECT id FROM shrimp_uom WHERE code = 'millar' LIMIT 1)
    """)
    print("shrimp_marketplace 1.3.4: %s lote(s) de larva pasados a millares"
          % cr.rowcount)
