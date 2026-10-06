"""19.0.1.2.0 (post) — empaque enlazado a la compra, perfil común y secuencia.

1. pack_razon_social / pack_representante / pack_telefono / pack_ubicacion /
   pack_capacidad_lb_semana pasan al perfil común (alias una versión).
2. Las órdenes y solicitudes ya existentes se enlazan a su COMPRA cuando no
   hay ambigüedad: el cliente es el comprador de exactamente una compra
   confirmada/recibida de ese producto. Las de lote propio (la camaronera que
   empaca lo suyo) quedan sin compra, como corresponde.
3. La secuencia de solicitudes usaba el prefijo "SEM-", el mismo que el
   semillero: pasa a "SOL-EMP-" para las referencias NUEVAS (las emitidas no
   se tocan).

Idempotente.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.shrimp_user_registry.migration_utils import copiar_perfil


def migrate(cr, version):
    if not version:
        return
    copiar_perfil(cr, {
        "shrimp_razon_social": ["pack_razon_social"],
        "shrimp_representante": ["pack_representante"],
        "shrimp_telefono": ["pack_telefono"],
        "shrimp_ubicacion": ["pack_ubicacion"],
    }, tipo="maquilador", unidad_capacidad="lb_week",
        columna_capacidad="pack_capacidad_lb_semana")

    for tabla in ("shrimp_copack_order", "shrimp_copack_request"):
        cr.execute("""
            UPDATE %(t)s o
               SET transaction_id = sub.tx_id
              FROM (
                    SELECT o2.id AS oid, min(t.id) AS tx_id, count(t.id) AS n
                      FROM %(t)s o2
                      JOIN shrimp_transaction t
                        ON t.product_id = o2.product_id
                       AND t.buyer_partner_id = o2.client_partner_id
                       AND t.state IN ('confirmed', 'done')
                     WHERE o2.transaction_id IS NULL AND o2.product_id IS NOT NULL
                     GROUP BY o2.id
                   ) sub
             WHERE sub.oid = o.id AND sub.n = 1
        """ % {"t": tabla})

    env = api.Environment(cr, SUPERUSER_ID, {})
    seq = env.ref("shrimp_copacking.seq_copack_request", raise_if_not_found=False)
    if seq and seq.prefix == "SEM-":
        seq.prefix = "SOL-EMP-"
