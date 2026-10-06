"""19.0.1.1.0 — endurecimiento de seguridad.

shrimp.copack.acceptance y shrimp.copack.tariff.line pasan a tener uuid_ref;
el mixin lo rellena en _auto_init. Aquí se repite el relleno (idempotente)
por si la columna se hubiera creado sin pasar por el ORM.
"""


def migrate(cr, version):
    if not version:
        return
    for tabla in ("shrimp_copack_acceptance", "shrimp_copack_tariff_line"):
        cr.execute("SELECT to_regclass(%s)", (tabla,))
        if cr.fetchone()[0]:
            cr.execute('UPDATE "%s" SET uuid_ref = gen_random_uuid()::text '
                       'WHERE uuid_ref IS NULL' % tabla)
