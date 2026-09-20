{
    "name": "Web Theme Launcher",
    "summary": "Lanzador de apps, color de tema configurable y modo oscuro por usuario",
    "description": """
Reemplaza el menú desplegable de aplicaciones por un lanzador a pantalla
completa (estilo Enterprise) con buscador y atajos de teclado (Ctrl+K abrir,
Esc cerrar). Además:

* Aplica un color corporativo configurable a la barra superior y a los botones
  y acentos del backend (Ajustes > Tema).
* Modo oscuro activable por cada usuario desde sus preferencias o el botón del
  systray.
    """,
    "author": "Cristhian",
    "website": "https://www.agricominsa.com",
    "category": "Web",
    "version": "2.0.0",
    "license": "LGPL-3",
    "depends": ["web", "base_setup"],
    "data": [
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "web_theme_launcher/static/src/css/theme_launcher.css",
            "web_theme_launcher/static/src/xml/theme_launcher.xml",
            "web_theme_launcher/static/src/js/theme_launcher.js",
        ],
        # El POS se sirve en un bundle aparte: aplicamos ahí el modo oscuro.
        "point_of_sale.assets_prod": [
            "web_theme_launcher/static/src/js/pos_theme.js",
            "web_theme_launcher/static/src/css/pos_dark.css",
            "web_theme_launcher/static/src/xml/pos_navbar.xml",
        ],
    },
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
}
