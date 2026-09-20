"""Aplica la identidad de Trazul a los dos sitios y limpia los datos de demo.

Odoo instala una empresa de ejemplo con dirección en California, teléfono
+1 555-555-5556 y www.example.com, y un pie de página con textos de relleno
("Somos un equipo de personas apasionadas..."). Todo eso queda a la vista de
los camaroneros y de las verificadoras, así que hay que reemplazarlo.

Uso:
    cd /home/ccarballo/odoo19
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d odoo19 --no-http \
        < custom_addons/Camaronera/shrimp_verification/scripts/configurar_marca.py

Es idempotente: se puede correr las veces que haga falta.

OJO — los datos reales de la compañía todavía no existen (la SAS está en
constitución). Lo que va abajo es lo que se sabe hoy; cuando haya RUC,
dirección y teléfono, se cambian aquí y se vuelve a ejecutar. Se deja en
blanco a propósito en vez de dejar los datos de California: un dato falso
en una factura es peor que un dato ausente.
"""

DATOS_EMPRESA = {
    "name": "TRAZUL S.A.S.",
    "email": "info@trazul.ec",
    "website": "https://trazul.ec",
    "pais": "EC",
    # Rellenar cuando existan:
    "phone": "",          # ej. "+593 4 000 0000"
    "street": "",
    "city": "",
    "vat": "",            # RUC
}

DESCRIPCION = (
    "Trazul conecta camaroneras, laboratorios y semilleros con sus compradores, "
    "y deja constancia verificable de cada eslabón de la cadena: quién produjo el "
    "lote, quién lo movió y qué encontró el verificador en campo."
)

DESCRIPCION_VERIF = (
    "Plataforma de las empresas verificadoras acreditadas en Trazul. Aquí reciben "
    "las órdenes de inspección, registran los cinco análisis en campo y emiten su "
    "veredicto. El informe es vinculante: la compra no se cierra hasta que "
    "comprador y vendedor lo aceptan."
)

ENLACES_CAMARONERA = [
    ("Inicio", "/"),
    ("Marketplace", "/marketplace"),
    ("Mis compras", "/marketplace/compras"),
    ("Mis ventas", "/marketplace/ventas"),
    ("Mi cuenta", "/marketplace/mi-cuenta"),
]

ENLACES_VERIFICADORES = [
    ("Inicio", "/"),
    ("Mi bandeja", "/verificador/bandeja"),
    ("En campo", "/verificador/bandeja?state=in_field"),
    ("Por dictaminar", "/verificador/bandeja?state=done"),
    ("Mi acreditación", "/marketplace/mis-certificados"),
]


def _pie(titulo, descripcion, enlaces, empresa):
    """Devuelve el arch del pie de página. Se arma en Python y no como plantilla
    del módulo porque el pie es una vista de sitio web: Odoo la copia por sitio
    (copy-on-write) en cuanto se edita, y cada sitio necesita sus enlaces."""
    lis = "\n".join(
        '                                <li><a href="%s">%s</a></li>' % (url, texto)
        for texto, url in enlaces
    )
    contacto = ['<li><i class="fa fa-comment fa-fw me-2"/><a href="/contactus">Escríbenos</a></li>']
    if empresa.email:
        contacto.append(
            '<li><i class="fa fa-envelope fa-fw me-2"/>'
            '<a href="mailto:%s">%s</a></li>' % (empresa.email, empresa.email))
    if empresa.phone:
        contacto.append(
            '<li><i class="fa fa-phone fa-fw me-2"/>'
            '<a href="tel:%s"><span class="o_force_ltr">%s</span></a></li>'
            % (empresa.phone, empresa.phone))
    contacto = "\n".join('                                %s' % c for c in contacto)

    return """<data inherit_id="website.layout" name="Default" active="True">
    <xpath expr="//div[@id='footer']" position="replace">
        <div id="footer" class="oe_structure oe_structure_solo border text-break" t-ignore="true" t-if="not no_footer" style="--box-border-left-width: 0px; --box-border-right-width: 0px;">
            <section class="s_text_block pt40 pb16" data-snippet="s_text_block" data-name="Container">
                <div class="container">
                    <div class="row">
                        <div class="col-lg-3 pt24 pb24">
                            <h5>Ir a</h5>
                            <ul class="list-unstyled">
%(lis)s
                            </ul>
                        </div>
                        <div class="col-lg-5 pt24 pb24">
                            <h5>%(titulo)s</h5>
                            <p>%(descripcion)s</p>
                        </div>
                        <div class="col-lg-3 offset-lg-1 pt24 pb24">
                            <h5>Contacto</h5>
                            <ul class="list-unstyled">
%(contacto)s
                            </ul>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    </xpath>
</data>""" % {"lis": lis, "titulo": titulo, "descripcion": descripcion, "contacto": contacto}


# ---------------------------------------------------------------------------
# 1) Datos de la compañía
# ---------------------------------------------------------------------------
empresa = env.company                                    # noqa: F821
pais = env["res.country"].search(                        # noqa: F821
    [("code", "=", DATOS_EMPRESA["pais"])], limit=1)

empresa.write({
    "name": DATOS_EMPRESA["name"],
    "email": DATOS_EMPRESA["email"],
    "website": DATOS_EMPRESA["website"],
    "phone": DATOS_EMPRESA["phone"] or False,
    "street": DATOS_EMPRESA["street"] or False,
    "street2": False,
    "city": DATOS_EMPRESA["city"] or False,
    "zip": False,
    "state_id": False,
    "vat": DATOS_EMPRESA["vat"] or False,
    "country_id": pais.id if pais else False,
})
print("Compañía: %s | %s | %s" % (empresa.name, empresa.email, pais.name if pais else "sin país"))
if not DATOS_EMPRESA["phone"]:
    print("  (sin teléfono ni dirección: rellénalos en este script cuando existan)")

# La otra compañía es la de demo de Odoo. No se borra —puede tener asientos
# colgando— pero se le quita el nombre de relleno para que nadie la confunda
# con la real si aparece en un desplegable.
for otra in env["res.company"].search([("id", "!=", empresa.id)]):   # noqa: F821
    if "My" in (otra.name or "") or "Company" in (otra.name or ""):
        otra.name = "(demo Odoo — no usar)"
        print("  compañía de demo renombrada:", otra.name)

# ---------------------------------------------------------------------------
# 2) Pie de página de cada sitio
# ---------------------------------------------------------------------------
V = env["ir.ui.view"].sudo()                             # noqa: F821


def _vista_del_sitio(clave, sitio):
    """La vista de este sitio si ya existe; si no, la genérica.

    Escribir sobre la genérica con website_id en el contexto dispara el
    copy-on-write de Odoo y crea la copia. Se busca primero la propia para
    que al reejecutar el script no se dependa del orden de búsqueda.
    """
    propia = V.search([("key", "=", clave), ("website_id", "=", sitio.id)], limit=1)
    if propia:
        return propia
    return V.with_context(website_id=sitio.id).search(
        [("key", "=", clave), ("website_id", "=", False)], limit=1)
for sitio in env["website"].sudo().search([], order="id"):   # noqa: F821
    if sitio.shrimp_is_verifier_site:
        arch = _pie("Trazul Verificadores", DESCRIPCION_VERIF, ENLACES_VERIFICADORES, empresa)
    else:
        arch = _pie("Trazul", DESCRIPCION, ENLACES_CAMARONERA, empresa)
    # Escribir con website_id en el contexto dispara el copy-on-write de Odoo:
    # se crea (o se reusa) una copia de la vista propia de este sitio y la
    # genérica del módulo queda intacta.
    vista = _vista_del_sitio("website.footer_custom", sitio)
    if vista:
        vista.with_context(website_id=sitio.id).write({"arch_db": arch})
        print("Pie de página aplicado a [%s] %s" % (sitio.id, sitio.name))

# ---------------------------------------------------------------------------
# 3) Página de contacto
# ---------------------------------------------------------------------------
# Odoo la deja con "3575 Fake Buena Vista Avenue", +1 555-555-5556 e
# info@yourcompany.example.com. Es la página a la que apunta el botón
# "Contáctanos" de la cabecera, así que es de las más vistas.
import re as _re

_datos = []
if empresa.street:
    _datos.append('<li><i class="fa fa-map-marker fa-fw me-2"/>'
                  '<span class="o_force_ltr">%s%s</span></li>'
                  % (empresa.street, (", " + empresa.city) if empresa.city else ""))
if empresa.phone:
    _datos.append('<li><i class="fa fa-phone fa-fw me-2"/>'
                  '<span class="o_force_ltr">%s</span></li>' % empresa.phone)
if empresa.email:
    _datos.append('<li><i class="fa fa-1x fa-fw fa-envelope me-2"/>'
                  '<span>%s</span></li>' % empresa.email)
if not _datos:
    _datos.append('<li><span>Escríbenos con el formulario.</span></li>')

_nueva_lista = '<ul class="list-unstyled mb-0 ps-2">%s</ul>' % "".join(_datos)
_patron = _re.compile(
    r'<ul class="list-unstyled mb-0 ps-2">.*?</ul>', _re.S)

for sitio in env["website"].sudo().search([], order="id"):   # noqa: F821
    pagina = _vista_del_sitio("website.contactus", sitio)
    if not pagina:
        continue
    arch = pagina.arch_db
    nuevo, n = _patron.subn(_nueva_lista, arch, count=1)
    # El encabezado de la columna también viene de relleno ("My Company").
    for relleno in ("<h5>My Company</h5>", "<h5>Mi Empresa</h5>"):
        nuevo = nuevo.replace(relleno, "<h5>%s</h5>" % empresa.name)
    if nuevo != arch:
        pagina.with_context(website_id=sitio.id).write({"arch_db": nuevo})
        print("Contacto [%s] %s: datos de ejemplo reemplazados" % (sitio.id, sitio.name))
    else:
        print("Contacto [%s] %s: ya estaba al día" % (sitio.id, sitio.name))

# ---------------------------------------------------------------------------
# 4) Quitar la promoción de Odoo del pie
# ---------------------------------------------------------------------------
# "Con la tecnología de Odoo · Cree un sitio web gratuito" es publicidad del
# proveedor dentro de un producto que se cobra: no puede quedar.
for clave in ("website.brand_promotion", "web.brand_promotion",
              "web.brand_promotion_message"):
    vista = env.ref(clave, raise_if_not_found=False)     # noqa: F821
    if vista and vista.active:
        vista.sudo().active = False
        print("Desactivada la promoción:", clave)

# ---------------------------------------------------------------------------
# 5) Idioma por defecto
# ---------------------------------------------------------------------------
es = env["res.lang"].sudo().search(                      # noqa: F821
    [("code", "in", ("es_EC", "es_419", "es_ES"))], limit=1)
if es:
    for sitio in env["website"].sudo().search([]):        # noqa: F821
        sitio.default_lang_id = es.id
        if es not in sitio.language_ids:
            sitio.language_ids = [(4, es.id)]
    for sitio in env["website"].sudo().search([]):        # noqa: F821
        # Solo español: la interfaz está escrita en español, así que el sitio
        # en inglés mostraría los mismos textos con un selector inútil arriba.
        sitio.language_ids = [(6, 0, [es.id])]
    print("Idioma por defecto:", es.name, "(único idioma de los sitios)")

env.cr.commit()                                          # noqa: F821
print("\nListo. Refresca con Ctrl+Shift+R.")
