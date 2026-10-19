"""19.0.1.30.0 — «Estado real del lote»: origen de cada fila de evolución.

Las filas que ya existían no tenían origen (la columna nace con el valor por
defecto, «observación»). Se clasifican por cómo se crearon:

* «Actualización automática del producto.» (la foto de shrimp.product.write)
  -> «Cambio en la publicación»;
* las enlazadas desde una ficha de calidad (shrimp_product_quality.
  evolution_id) o con la nota «Muestreo del …» -> «Ficha de calidad»;
* «Muestreo registrado por el vendedor.» y todo lo demás (mediciones a mano,
  demo, API) -> «Observación del vendedor».

Es SQL directo: el ORM no dejaría reescribir el origen ni tocar observaciones
fuera de su plazo de corrección.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE shrimp_product_evolution SET origin = 'observation'
         WHERE origin IS NULL OR origin NOT IN ('observation', 'publication', 'quality')
    """)
    cr.execute("""
        UPDATE shrimp_product_evolution SET origin = 'publication'
         WHERE note = 'Actualización automática del producto.'
    """)
    n_pub = cr.rowcount
    cr.execute("""
        UPDATE shrimp_product_evolution e SET origin = 'quality'
         WHERE e.origin <> 'publication'
           AND (e.id IN (SELECT q.evolution_id FROM shrimp_product_quality q
                          WHERE q.evolution_id IS NOT NULL)
                OR e.note LIKE 'Muestreo del %%')
    """)
    n_q = cr.rowcount
    cr.execute("SELECT count(*) FROM shrimp_product_evolution WHERE origin = 'observation'")
    n_obs = cr.fetchone()[0]
    _logger.info("Evolución: %s cambios en la publicación, %s fichas de calidad, %s observaciones",
                 n_pub, n_q, n_obs)
