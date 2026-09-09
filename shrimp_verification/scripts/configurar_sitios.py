"""Configura las dos plataformas: camaronera y verificadores.

Esto NO va en los datos del módulo a propósito: los sitios web se crean a mano
desde la interfaz y sus ids cambian de una instalación a otra, así que forzarlos
desde XML pisaría la configuración del cliente.

Uso:

    cd /home/ccarballo/odoo19
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d odoo19 \
        < custom_addons/Camaronera/shrimp_verification/scripts/configurar_sitios.py

Ajusta DOMINIO_CAMARONERA y DOMINIO_VERIFICADORES antes de ejecutarlo en
producción. Es idempotente: se puede correr las veces que haga falta.
"""

# --- Ajusta esto según el entorno -------------------------------------------
# El sitio se identifica por su dominio, no por su nombre: el nombre es de
# marca y cambia (era "Verificadores", hoy es "Trazul Verificadores"), mientras
# que el dominio es lo que de verdad lo distingue para Odoo.
DOMINIO_CAMARONERA = "http://localhost:8069"
DOMINIO_VERIFICADORES = "http://verificadores.localhost:8069"
# ----------------------------------------------------------------------------


W = env["website"].sudo()
M = env["website.menu"].sudo()

verif = W.search([("domain", "like", DOMINIO_VERIFICADORES)], limit=1)
if not verif:
    # Reserva: por nombre, para la primera corrida en una base donde el sitio
    # existe pero todavía no tiene dominio asignado.
    verif = W.search([("name", "ilike", "verificador")], limit=1)
if not verif:
    raise SystemExit(
        "No existe el sitio de verificadores (dominio %s). Créalo en "
        "Sitio web › Configuración › Sitios web y vuelve a ejecutar este script."
        % DOMINIO_VERIFICADORES)

principal = W.search([("id", "!=", verif.id)], order="id", limit=1)
if not principal:
    raise SystemExit("No se encontró el sitio principal de la camaronera.")

# 1) Dominios. Sin ellos Odoo no puede distinguir los sitios y sirve siempre el
#    primero, así que la separación no existiría.
principal.domain = DOMINIO_CAMARONERA
verif.domain = DOMINIO_VERIFICADORES

# 2) Marcar cuál es el de verificadores. Se usa un campo y no el id ni el nombre
#    para que el sitio se pueda renombrar o recrear sin romper el enrutado.
W.search([]).write({"shrimp_is_verifier_site": False})
verif.shrimp_is_verifier_site = True

# 3) Menú propio: el que Odoo copia al crear el sitio es el del marketplace y no
#    le sirve a un verificador.
raiz = M.search([("website_id", "=", verif.id), ("parent_id", "=", False)], limit=1)
if not raiz:
    raise SystemExit("El sitio '%s' no tiene menú raíz." % verif.name)

# El árbol lo define el modelo (_SHRIMP_MENU_VERIFICADOR en models/website.py),
# no este script. Aquí había una copia PLANA y desactualizada de la lista, y al
# ejecutarse borraba el árbol bueno y lo sustituía por seis enlaces sueltos: se
# perdían "Mi equipo", "Mis reportes" y "Perfil y cuenta bancaria", y los
# desplegables con estilo dejaban de coincidir porque dependen de que los
# nombres 'Verificaciones' y 'Mi empresa' existan CON hijos. Dos sitios
# distintos definiendo el mismo menú es una fuente de regresiones garantizada,
# así que el script solo pide reconstruirlo.
M.search([("website_id", "=", verif.id), ("parent_id", "!=", False)]).unlink()
W._shrimp_build_verifier_menu(verif)

# ---------------------------------------------------------------------------
# Realinear los menús del sitio de la camaronera
# ---------------------------------------------------------------------------
# La barra superior del marketplace convierte tres menús —"Productos",
# "Operaciones" y "Mi cuenta"— en desplegables, y los reconoce por el NOMBRE
# literal del menú (shrimp_marketplace/views/navbar_dropdown.xml).
#
# Los website.menu de cada sitio son copias que Odoo saca al crearlo, así que
# no siguen cambiando el registro del módulo. Cuando "Mi panel" se dividió en
# los tres menús actuales, la copia del sitio 1 se quedó con el nombre viejo
# apuntando a /my: el desplegable "Productos" dejó de coincidir y no se
# renderizaba PARA NINGÚN ROL. Con él desaparecieron de la barra las seis
# entradas que cuelgan de ahí —Publicar producto, Mis productos, Marketplace,
# Mi inventario, Instalaciones y piscinas, Solicitudes de chequeo—, o sea
# todo el trabajo del vendedor. Solo se llegaba escribiendo la URL.
#
# Se realinea cada copia con el registro del módulo por posición en la barra.
MENU_CAMARONERA = {
    "/marketplace": "Marketplace",
    "/marketplace/products": "Productos",
    "/marketplace/compras": "Operaciones",
    "/marketplace/mi-cuenta": "Mi cuenta",
}
# El nombre viejo y la URL a la que quedó apuntando, para reconocer la copia
# desalineada sin depender del id.
RENOMBRADOS = {"Mi panel": ("/my", "/marketplace/products")}

for sitio in W.search([]):
    if sitio.shrimp_is_verifier_site:
        continue
    for menu in M.search([("website_id", "=", sitio.id), ("parent_id", "!=", False)]):
        esperado = MENU_CAMARONERA.get(menu.url)
        if esperado and menu.name != esperado:
            print("  menú [%s] '%s' -> '%s'" % (sitio.id, menu.name, esperado))
            menu.name = esperado
            continue
        arreglo = RENOMBRADOS.get(menu.name)
        if arreglo and menu.url == arreglo[0]:
            destino = arreglo[1]
            print("  menú [%s] '%s' (%s) -> '%s' (%s)" % (
                sitio.id, menu.name, menu.url, MENU_CAMARONERA[destino], destino))
            menu.write({"name": MENU_CAMARONERA[destino], "url": destino})

# ---------------------------------------------------------------------------
# Sanear la personalizacion de tema del sitio
# ---------------------------------------------------------------------------
# Al crear un sitio, Odoo deja preparada la osamenta de personalizacion del
# tema: dos .scss de override (user_values.scss y user_theme_color_palette.scss)
# apuntados por registros ir.asset con directiva "replace". Si el tema no
# termina de instalarse, esos overrides no se pueden resolver, y UN SOLO
# archivo irresoluble tumba el bundle CSS completo del sitio: la pagina sale
# sin maquetar, con todo pegado al margen izquierdo, y Odoo no lo reporta como
# error en el log, solo escribe el aviso dentro del propio bundle.
#
# Como no personalizamos colores desde el editor —la identidad de Trazul vive
# en verification.css— lo correcto es no tener esos overrides.
for sitio in W.search([]):
    if sitio.theme_id and sitio.theme_id.state != "installed":
        print("  quitando tema sin instalar de [%s]: %s" % (sitio.id, sitio.theme_id.name))
        sitio.theme_id = False
    Assets = env["website.assets"].with_context(website_id=sitio.id).sudo()
    for url in ("/website/static/src/scss/options/user_values.scss",
                "/website/static/src/scss/options/colors/user_theme_color_palette.scss"):
        Assets.reset_asset(url, "web.assets_frontend")

# Los bundles ya compilados guardan el error en cache: hay que botarlos.
env["ir.attachment"].sudo().search([("url", "like", "/web/assets/%")]).unlink()
env.registry.clear_cache("assets")

env.cr.commit()

print("Configuración aplicada:")
for w in W.search([], order="id"):
    print("  [%s] %-18s %-42s verificadores=%s" % (
        w.id, w.name, w.domain or "(sin dominio)", w.shrimp_is_verifier_site))
print("  menú del sitio de verificadores:")
for m in M.search([("website_id", "=", verif.id), ("parent_id", "=", raiz.id)],
                  order="sequence"):
    print("    %-18s %s" % (m.name, m.url))
    for h in M.search([("parent_id", "=", m.id)], order="sequence"):
        print("      - %-16s %s" % (h.name, h.url))
