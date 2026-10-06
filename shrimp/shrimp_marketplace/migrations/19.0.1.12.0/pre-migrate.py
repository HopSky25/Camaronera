"""19.0.1.12.0 (pre) — filtros en cajón: se borra la herencia vieja de larva.

La vista heredada shrimp_marketplace.marketplace_list_larva guardada en la
base se anclaba en el formulario de orden («input[@name='q']»), que la
plantilla nueva del marketplace (barra + cajón de filtros) ya no tiene. Odoo
valida esa herencia vieja al cargar la plantilla nueva, antes de actualizarla,
y el -u fallaba con «no se puede localizar en la vista principal».

Se borra antes de cargar los datos; el mismo -u la vuelve a crear desde
views/larva_marketplace_templates.xml con sus anclas nuevas. Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)

OBSOLETAS = [
    ("shrimp_marketplace", "marketplace_list_larva"),
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
    _logger.info("Filtros en cajón: %s vista(s) heredada(s) obsoleta(s) eliminada(s) (%s)", len(todas), todas)
