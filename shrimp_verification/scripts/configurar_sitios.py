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

MENU_VERIFICADOR = [
    ("Inicio", "/", 10),
    ("Mi bandeja", "/verificador/bandeja", 20),
    ("En campo", "/verificador/bandeja?state=in_field", 30),
    ("Por dictaminar", "/verificador/bandeja?state=done", 40),
    ("Ya verificadas", "/verificador/bandeja?state=approved", 50),
    ("Mi acreditación", "/marketplace/mis-certificados", 60),
]

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

M.search([("website_id", "=", verif.id), ("parent_id", "!=", False)]).unlink()
for nombre, url, secuencia in MENU_VERIFICADOR:
    M.create({"name": nombre, "url": url, "parent_id": raiz.id,
              "sequence": secuencia, "website_id": verif.id})

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
for m in M.search([("website_id", "=", verif.id), ("parent_id", "!=", False)],
                  order="sequence"):
    print("    %-18s %s" % (m.name, m.url))
