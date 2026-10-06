"""19.0.1.10.0 (pre) — barra nueva: se borran las vistas heredadas viejas.

Las entradas de la barra antigua se inyectaban con xpath sobre
shrimp_marketplace.submenu_mi_panel_dropdown, anclados en nodos que la
plantilla nueva ya no tiene (el desplegable «shrimp-dd-products», el enlace
al calendario, el de listas de precios). Ahora se declaran en
website._shrimp_nav_entries() y esas vistas se quitaron de sus módulos.

Hay que borrarlas ANTES de cargar la plantilla nueva: al actualizarla, Odoo
valida la vista combinada con las heredadas que siguen en la base, y las
viejas (de shrimp_verification, shrimp_packer y shrimp_copacking, que se
actualizan después) harían fallar el -u con «cannot be located in parent
view». En una instalación nueva no existen y esto no hace nada.

Idempotente: solo borra lo que encuentra (y sus vistas hijas, si alguien les
hubiera colgado alguna).
"""
import logging

_logger = logging.getLogger(__name__)

OBSOLETAS = [
    ("shrimp_verification", "submenu_mi_panel_verificador"),
    ("shrimp_packer", "navbar_listas_de_precios"),
    ("shrimp_packer", "navbar_reservas"),
    ("shrimp_copacking", "navbar_servicio_empaque"),
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
    _logger.info("Barra nueva: %s vistas heredadas obsoletas eliminadas (%s)", len(todas), todas)
