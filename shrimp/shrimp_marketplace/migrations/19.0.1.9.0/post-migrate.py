"""19.0.1.9.0 — compras al mismo nivel de la cadena.

Se agregan los tipos de transacción semillero_to_semillero,
laboratorio_to_laboratorio y camaronera_to_camaronera (el tipo sale ahora de
la pareja de roles, no solo del vendedor). Los tipos existentes siguen
siendo válidos; solo se corrigen las compras viejas que ya eran de mismo
nivel y quedaron con el tipo genérico: sin shrimp_packer el camarón adulto
lo compraba «cualquiera», así que una camaronera pudo comprarle a otra con
el tipo camaronera_to_buyer.

Idempotente: solo toca filas con vendedor y comprador del mismo rol que
todavía tienen el tipo de la cadena.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE shrimp_transaction
           SET transaction_type = CASE seller_role
                   WHEN 'semillero' THEN 'semillero_to_semillero'
                   WHEN 'laboratorio' THEN 'laboratorio_to_laboratorio'
                   WHEN 'camaronera' THEN 'camaronera_to_camaronera' END
         WHERE seller_role IS NOT NULL
           AND seller_role = buyer_role
           AND (seller_role, transaction_type) IN (
                   ('semillero', 'semillero_to_laboratorio'),
                   ('laboratorio', 'laboratorio_to_camaronera'),
                   ('camaronera', 'camaronera_to_buyer'))
    """)
