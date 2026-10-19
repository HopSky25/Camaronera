"""19.0.1.7.0 — el perfil «Semillero» se muestra como «Naupliero» (solo etiqueta).

La clave técnica no cambia ('semillero' en selecciones, campos, xmlids, rutas,
parámetros y API). Las etiquetas que viven en el código (selecciones, vistas,
plantillas) se refrescan solas con el -u; aquí solo se corrigen los textos que
quedaron GUARDADOS en la base y que el -u no vuelve a escribir (registros
noupdate o creados a mano), y solo cuando el texto es EXACTAMENTE la etiqueta
vieja del perfil:

1. Menús del sitio (website.menu) llamados «Semillero(s)…» → «Naupliero(s)…».
2. Nombre del documento legal del anexo del perfil (si una instalación vieja lo
   tenía como «Anexo de perfil: Semillero»). NO se toca el contenido de las
   versiones publicadas (inmutables; ya dicen «Naupliero») ni las aceptaciones
   (son la foto de lo que el usuario aceptó).
3. Nota de los socios de la demo masiva («Socio de demostración (semillero)…»),
   que es noupdate.
4. Copia es_EC vieja del resumen/descripción de los módulos shrimp_*.

Idempotente.
"""
import logging

_logger = logging.getLogger(__name__)

# Texto exacto viejo -> texto nuevo. Solo nombres del perfil, nada más.
MENUS = {
    "Semillero": "Naupliero",
    "Semilleros": "Nauplieros",
    "Semilleros destacados": "Nauplieros destacados",
    "Laboratorios / Semilleros destacados": "Laboratorios / Nauplieros destacados",
}
NOTA_DEMO_VIEJA = "Socio de demostración (semillero). Expediente completo en la plataforma."
NOTA_DEMO_NUEVA = "Socio de demostración (naupliero). Expediente completo en la plataforma."


def _tabla(cr, nombre):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (nombre,))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    total = {}

    # 1. website.menu: name es jsonb traducible; se cambia idioma por idioma
    #    solo el valor que coincide exacto.
    if _tabla(cr, "website_menu"):
        n = 0
        for viejo, nuevo in MENUS.items():
            cr.execute("""
                UPDATE website_menu m
                   SET name = (SELECT jsonb_object_agg(k, CASE WHEN v = to_jsonb(%s::text)
                                                               THEN to_jsonb(%s::text) ELSE v END)
                                 FROM jsonb_each(m.name) AS e(k, v))
                 WHERE EXISTS (SELECT 1 FROM jsonb_each_text(m.name) AS e(k, v) WHERE v = %s)
            """, (viejo, nuevo, viejo))
            n += cr.rowcount
        total["website.menu"] = n

    # 2. Nombre del anexo legal del perfil (no el contenido de las versiones).
    if _tabla(cr, "shrimp_legal_document"):
        cr.execute("""
            UPDATE shrimp_legal_document
               SET name = 'Anexo de perfil: Naupliero'
             WHERE code = 'annex_semillero' AND name = 'Anexo de perfil: Semillero'
        """)
        total["shrimp.legal.document"] = cr.rowcount

    # 3. Nota de los socios de la demo (noupdate). Se guarda como HTML.
    cr.execute("""
        UPDATE res_partner
           SET comment = replace(comment, %s, %s)
         WHERE comment IN (%s, %s)
    """, (NOTA_DEMO_VIEJA, NOTA_DEMO_NUEVA, NOTA_DEMO_VIEJA, "<p>%s</p>" % NOTA_DEMO_VIEJA))
    total["res.partner (nota demo)"] = cr.rowcount

    # 4. Resumen/descripción de los módulos shrimp_* en Aplicaciones: el -u
    #    reescribe el texto en en_US pero conserva la copia vieja en es_EC
    #    (decía «Semillero»). Se quita esa copia para que se lea la nueva.
    n = 0
    for campo in ("summary", "description"):
        cr.execute("""
            UPDATE ir_module_module m
               SET {c} = (SELECT jsonb_object_agg(k, v) FROM jsonb_each(m.{c}) AS e(k, v)
                           WHERE k = 'en_US' OR v::text NOT ILIKE '%%semiller%%')
             WHERE m.name LIKE 'shrimp%%'
               AND m.{c} ? 'en_US'
               AND m.{c}->>'en_US' NOT ILIKE '%%semiller%%'
               AND EXISTS (SELECT 1 FROM jsonb_each_text(m.{c}) AS e(k, v)
                            WHERE k <> 'en_US' AND v ILIKE '%%semiller%%')
        """.format(c=campo))
        n += cr.rowcount
    total["ir.module.module (textos)"] = n

    _logger.info("shrimp_user_registry 19.0.1.7.0: etiqueta Semillero -> Naupliero en datos guardados: %s", total)
