"""Rediseño del portal de la verificadora + equipo de campo (19.0.1.22.0).

1) Las plantillas verifier_detail y purchase_pending cambiaron de estructura:
   ya no tienen .sf-banner ni .row.g-4, que eran las anclas de dos vistas
   heredadas de dispatch_templates.xml. Al actualizar, Odoo valida las
   herencias VIEJAS (aún en la base) contra el padre nuevo antes de recargar
   dispatch_templates.xml, y el -u se cae. Se borran aquí esas dos herencias:
   el mismo -u las vuelve a crear con las anclas nuevas.

2) Equipo de campo: el técnico de siempre (technician_partner_id) pasa a ser
   el «técnico responsable». No hay datos que mover: el campo se conserva con
   su columna; los técnicos de apoyo van en una tabla de relación nueva
   (shrimp_verification_support_tech_rel), vacía al empezar.
"""
import logging

_logger = logging.getLogger(__name__)

HERENCIAS = ("verifier_detail_dispatch", "purchase_pending_dispatch")


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'shrimp_verification' AND model = 'ir.ui.view' AND name IN %s
    """, (HERENCIAS,))
    ids = [r[0] for r in cr.fetchall()]
    if ids:
        # Hijas de esas herencias (copias por sitio web), por si las hubiera.
        cr.execute("DELETE FROM ir_ui_view WHERE inherit_id IN %s", (tuple(ids),))
        cr.execute("DELETE FROM ir_ui_view WHERE id IN %s", (tuple(ids),))
        cr.execute("""
            DELETE FROM ir_model_data
             WHERE module = 'shrimp_verification' AND model = 'ir.ui.view' AND name IN %s
        """, (HERENCIAS,))
    _logger.info("shrimp_verification 19.0.1.22.0: %s herencias de despacho recreadas; "
                 "technician_partner_id = técnico responsable (sin cambios de datos).", len(ids))
