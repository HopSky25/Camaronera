{
    "name": "CamaronMarket - Diseño del backend",
    "summary": "Lleva la identidad visual del portal (colores, tipografía Inter, tarjetas) a las pantallas internas: contabilidad, ajustes y backoffice.",
    "version": "19.0.1.0.0",
    "category": "Hidden/Tools",
    "license": "LGPL-3",
    "author": "CamaronMarket",
    "depends": ["web"],
    "assets": {
        "web._assets_primary_variables": [
            "shrimp_tema_backend/static/src/scss/variables.scss",
        ],
        "web.assets_backend": [
            "shrimp_tema_backend/static/src/scss/backend.scss",
            "shrimp_tema_backend/static/src/js/graficos_tablero.js",
        ],
    },
    "installable": True,
    "auto_install": False,
}
