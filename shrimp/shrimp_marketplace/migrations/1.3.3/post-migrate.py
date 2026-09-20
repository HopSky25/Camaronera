# -*- coding: utf-8 -*-
"""Lleva la referencia de las transacciones de 6 a 10 dígitos.

El padding de la secuencia está en data/sequence.xml y ese registro NO lleva
noupdate, así que un -u ya deja ir_sequence en 10. Pero eso solo afecta a las
referencias que se emitan a partir de ahora: las que ya existen se guardaron
como texto en shrimp_transaction.name y siguen con 6 dígitos.

Convivir con las dos formas es el verdadero problema, no la estética: TXN-000111
y TXN-0000000112 no ordenan igual, ni por SQL ni en el buscador del portal
(name ilike), y el usuario que copia una referencia de un PDF viejo y la pega en
el filtro no encuentra nada en cuanto los formatos se mezclan. Por eso se
reescriben todas a lo mismo.

Se toca solo shrimp_transaction.name y su copia en sale_order.client_order_ref.
Los números legales —account_move.name de la factura— son de otra secuencia y no
se tocan: la referencia de la transacción es un identificador interno del
marketplace, no un documento con validez fiscal.
"""
import re

# Debe coincidir con data/sequence.xml. Si mañana se sube a 12, se cambia allí
# y se añade otra migración: aquí no se adivina el valor.
PREFIJO = "TXN-"
PADDING = 10


def migrate(cr, version):
    if not version:
        # Instalación en limpio: sequence.xml ya crea la secuencia con el
        # padding bueno y no hay referencias previas que reescribir.
        return

    # 1) La secuencia. En esta base el -u ya la deja en 10, pero una base donde
    #    alguien marcó el registro como noupdate (o lo editó a mano desde
    #    Ajustes) se quedaría en 6 sin avisar, y entonces la migración de abajo
    #    normalizaría el pasado mientras el futuro sigue saliendo corto.
    cr.execute("""
        UPDATE ir_sequence
           SET padding = %s
         WHERE code = 'shrimp.transaction'
           AND padding < %s
    """, (PADDING, PADDING))
    secuencias = cr.rowcount

    # 2) Las referencias ya emitidas. Solo las que tienen la forma exacta
    #    TXN-<dígitos> y se quedan cortas; cualquier nombre escrito a mano
    #    ("Nuevo", una referencia importada) se deja intacto.
    patron = re.compile(r"^%s(\d+)$" % re.escape(PREFIJO))
    cr.execute("""
        SELECT id, name
          FROM shrimp_transaction
         WHERE name LIKE %s
    """, (PREFIJO + "%",))

    renombrados = []
    for tx_id, nombre in cr.fetchall():
        m = patron.match(nombre or "")
        if not m or len(m.group(1)) >= PADDING:
            continue
        renombrados.append((PREFIJO + m.group(1).zfill(PADDING), nombre, tx_id))

    for nuevo, viejo, tx_id in renombrados:
        cr.execute("UPDATE shrimp_transaction SET name = %s WHERE id = %s",
                   (nuevo, tx_id))
        # El pedido de venta guarda la referencia como texto libre; si no se
        # actualiza, el enlace entre la factura y la transacción se pierde para
        # cualquiera que lo busque por ese campo.
        cr.execute("""
            UPDATE sale_order
               SET client_order_ref = %s
             WHERE client_order_ref = %s
        """, (nuevo, viejo))

    print("shrimp_marketplace 1.3.3: %s secuencia(s) a %s dígitos, "
          "%s referencia(s) reescritas" % (secuencias, PADDING, len(renombrados)))
