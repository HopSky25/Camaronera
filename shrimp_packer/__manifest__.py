{
    "name": "Camaronera — Empacadora y listas de precios",
    "version": "19.0.1.0.0",
    "summary": "Rol de empacadora, su perfil y las listas de precios que publica",
    "description": """
Reproduce la lista de precios que las empacadoras reparten cada semana a las
camaroneras: matriz de talla por presentación y calidad, con la cola abierta en
directa y sobrante, bonificaciones aparte, ventana de despacho, condiciones de
calidad y forma de pago.

Es informativa: no fija el precio de ninguna compra. Sirve para que el productor
sepa a cuánto le pagan antes de ofrecer, que es como funciona el mercado.

Va en un módulo propio y no dentro de shrimp_marketplace para no chocar con el
desarrollo que se hace en paralelo sobre ese módulo.
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Industries",
    "depends": ["shrimp_marketplace", "shrimp_user_registry", "shrimp_verification", "website"],
    "data": [
        "security/ir.model.access.csv",
        "security/shrimp_packer_rules.xml",
        "data/size_grade_extra.xml",
        "views/shrimp_price_list_views.xml",
        "views/price_list_templates.xml",
        "views/res_partner_views.xml",
        "views/registry_form_packer.xml",
        "views/packer_profile_templates.xml",
        "views/reportes_inherit.xml",
        "views/navbar_inherit.xml",
        "views/productos_inherit.xml",
        "views/aceptacion_inherit.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "shrimp_packer/static/src/css/packer.css",
        ],
    },
    "installable": True,
}
