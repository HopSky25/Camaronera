"""19.0.1.14.0 (pre) — rediseño del formulario de publicar/editar producto.

La plantilla product_new_form se reescribió (tarjetas, vista previa en vivo,
chips y grupos de entrada): el campo de precio ya no es un input suelto sino
parte de un grupo «USD $ … / unidad». La herencia de shrimp_packer guardada en
la base («Publicar lote - precio sugerido») se anclaba en
«//input[@name='price']» con position before/after; Odoo la valida al cargar
la plantilla nueva, antes de que shrimp_packer se actualice, y o bien
fallaría o metería la lista de precios DENTRO del grupo del precio.

Se borra antes de cargar los datos; el mismo -u (que actualiza también los
módulos que dependen de shrimp_marketplace) la vuelve a crear desde
shrimp_packer/views/publicar_inherit.xml con sus anclas nuevas
(#pfPriceListSlot y #pfPriceSugSlot). Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)

OBSOLETAS = [
    ("shrimp_packer", "publicar_precio_sugerido"),
]


def migrate(cr, version):
    if not version:
        return
    ids = []
    for module, name in OBSOLETAS:
        cr.execute("SELECT res_id FROM ir_model_data WHERE module = %s AND name = %s AND model = 'ir.ui.view'",
                   (module, name))
        ids += [r[0] for r in cr.fetchall()]
    if not ids:
        return
    cr.execute("""
        WITH RECURSIVE arbol(id) AS (
            SELECT id FROM ir_ui_view WHERE id = ANY(%s)
            UNION
            SELECT v.id FROM ir_ui_view v JOIN arbol a ON v.inherit_id = a.id
        )
        SELECT id FROM arbol
    """, (ids,))
    todas = [r[0] for r in cr.fetchall()]
    cr.execute("DELETE FROM ir_model_data WHERE model = 'ir.ui.view' AND res_id = ANY(%s)", (todas,))
    # Hijas primero (inherit_id es ondelete restrict/cascade según versión).
    cr.execute("DELETE FROM ir_ui_view WHERE id = ANY(%s) AND id <> ALL(%s)", (todas, ids))
    cr.execute("DELETE FROM ir_ui_view WHERE id = ANY(%s)", (ids,))
    _logger.info("Formulario de producto: %s vista(s) heredada(s) obsoleta(s) eliminada(s) (%s)",
                 len(todas), todas)
