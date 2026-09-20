"""Migra el antiguo campo Selection `uom` (texto: libra/millar/unidad) al nuevo
Many2one `uom_id` que apunta a shrimp.uom. La columna `uom` original queda
huérfana en la BD y se puede eliminar; aquí solo la leemos para mapear.
"""
import logging

_logger = logging.getLogger(__name__)

# (tabla, columna_texto_vieja, columna_fk_nueva)
_TABLES = [
    ("shrimp_product", "uom", "uom_id"),
    ("shrimp_stock_lot", "uom", "uom_id"),
    ("shrimp_charge", "uom", "uom_id"),
    ("shrimp_check_request", "uom", "uom_id"),
]


def migrate(cr, version):
    if not version:
        return

    # Mapa code -> id de shrimp.uom
    cr.execute("SELECT id, code FROM shrimp_uom")
    code_to_id = {code: rid for (rid, code) in cr.fetchall()}
    if not code_to_id:
        _logger.warning("shrimp.uom vacío; se omite migración de unidades.")
        return

    for table, old_col, new_col in _TABLES:
        # ¿existe todavía la columna vieja?
        cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=%s AND column_name=%s",
            (table, old_col),
        )
        if not cr.fetchone():
            continue

        for code, uom_id in code_to_id.items():
            cr.execute(
                f"UPDATE {table} SET {new_col}=%s "
                f"WHERE {old_col}=%s AND ({new_col} IS NULL)",
                (uom_id, code),
            )
        _logger.info("Migradas unidades de medida en %s.", table)
