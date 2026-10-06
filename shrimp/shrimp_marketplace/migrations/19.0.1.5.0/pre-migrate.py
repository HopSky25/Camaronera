"""19.0.1.5.0 (pre) — coherencia entre módulos.

1. website.shrimp_platform: cada sitio dice qué plataforma sirve. Sustituye a
   los booleanos shrimp_is_verifier_site (shrimp_verification) y
   shrimp_is_copacker_site (shrimp_copacking), que quedan como alias. Se crea
   y se rellena AQUÍ, antes de cargar el módulo, porque lo primero que hace la
   carga (y la de verificación y empaque) es buscar los sitios por plataforma:
   si la columna naciera con "main" para todos, la autoconfiguración de
   verificadores creería que no hay sitio de verificadores y elegiría otro.
2. Piezas que se mudan de shrimp_verification a este módulo (el mixin de
   avisos y la marca global de los sitios): se cambia el dueño de su xmlid
   para que la carga de este módulo ACTUALICE esos registros en vez de crear
   copias (dos vistas heredadas reemplazando el mismo nodo tumbarían la
   validación de la vista).

Idempotente.
"""

MOVIDOS_DESDE_VERIFICACION = (
    "model_shrimp_notify_mixin",
    "header_telefono_empresa",
    "header_correo_empresa",
    "header_contacto_de_la_empresa",
    "sin_promocion_de_odoo",
    "copyright_de_la_empresa",
)


def _columna(cr, tabla, columna):
    cr.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""", (tabla, columna))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    # 1) Plataforma de cada sitio.
    cr.execute("ALTER TABLE website ADD COLUMN IF NOT EXISTS shrimp_platform varchar")
    if _columna(cr, "website", "shrimp_is_verifier_site"):
        cr.execute("""UPDATE website SET shrimp_platform = 'verifier'
                       WHERE shrimp_is_verifier_site IS TRUE AND shrimp_platform IS NULL""")
    if _columna(cr, "website", "shrimp_is_copacker_site"):
        cr.execute("""UPDATE website SET shrimp_platform = 'copacker'
                       WHERE shrimp_is_copacker_site IS TRUE AND shrimp_platform IS NULL""")
    cr.execute("UPDATE website SET shrimp_platform = 'main' WHERE shrimp_platform IS NULL")

    # 2) xmlids que se mudan de módulo. Si ya existe el nuevo (segunda
    #    ejecución), el viejo se borra para no dejar dos dueños.
    for nombre in MOVIDOS_DESDE_VERIFICACION:
        cr.execute("""SELECT id FROM ir_model_data
                       WHERE module = 'shrimp_marketplace' AND name = %s""", (nombre,))
        if cr.fetchone():
            cr.execute("""DELETE FROM ir_model_data
                           WHERE module = 'shrimp_verification' AND name = %s""", (nombre,))
            continue
        cr.execute("""UPDATE ir_model_data SET module = 'shrimp_marketplace'
                       WHERE module = 'shrimp_verification' AND name = %s""", (nombre,))
    # Las claves (key) de las plantillas QWeb movidas se actualizan solas al
    # cargar el XML; las t-call antiguas ya apuntan a la clave nueva.
    cr.execute("""UPDATE ir_ui_view SET key = replace(key, 'shrimp_verification.', 'shrimp_marketplace.')
                   WHERE key IN %s""", (tuple("shrimp_verification.%s" % n
                                               for n in MOVIDOS_DESDE_VERIFICACION),))
