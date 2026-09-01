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
NOMBRE_SITIO_VERIFICADORES = "Verificadores"
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

verif = W.search([("name", "=", NOMBRE_SITIO_VERIFICADORES)], limit=1)
if not verif:
    raise SystemExit(
        "No existe el sitio '%s'. Créalo en Sitio web › Configuración › Sitios web "
        "y vuelve a ejecutar este script." % NOMBRE_SITIO_VERIFICADORES)

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

env.cr.commit()

print("Configuración aplicada:")
for w in W.search([], order="id"):
    print("  [%s] %-18s %-42s verificadores=%s" % (
        w.id, w.name, w.domain or "(sin dominio)", w.shrimp_is_verifier_site))
print("  menú del sitio de verificadores:")
for m in M.search([("website_id", "=", verif.id), ("parent_id", "!=", False)],
                  order="sequence"):
    print("    %-18s %s" % (m.name, m.url))
