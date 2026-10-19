"""19.0.1.24.0 — backoffice en tres aplicaciones (pre).

Los <menuitem groups="..."> de Odoo AÑADEN grupos al menú en cada
actualización, nunca quitan los que ya tenía. Este módulo cambia los grupos de
sus menús (ahora los de cada app: CamaronMarket / Verificador / Copacking), así
que antes de cargar los datos se vacían los grupos de SUS menús; la carga los
vuelve a poner, ya los nuevos. Sin esto un menú quedaría con el grupo viejo y
el nuevo a la vez, y lo seguiría viendo quien no debe.

Solo toca menús con xmlid de este módulo. Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        DELETE FROM ir_ui_menu_group_rel
         WHERE menu_id IN (SELECT res_id FROM ir_model_data
                            WHERE module = %s AND model = 'ir.ui.menu')
    """, ("shrimp_marketplace",))
    _logger.info("shrimp_marketplace 19.0.1.24.0: grupos vaciados en %s fila(s) de menús (se recargan con el módulo).",
                 cr.rowcount)
