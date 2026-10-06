{
    'name': "shrimp_user_registry",

    'summary': "Registro web de Semillero/Laboratorio/Camaronera con adjuntos",

    'description': """
Registro web de los socios de la plataforma CamaronMarket
==========================================================

Alta propia (con certificados y aprobación de los roles operativos), perfil
de empresa común a todos los roles, identificación fiscal estándar (vat),
matriz de capacidades por rol (res.partner._shrimp_can), varios perfiles
por cuenta con aprobación por perfil (shrimp.partner.role; el perfil activo
es shrimp_user_type) y código de
referencia uuid para no exponer ids en URLs y API.
    """,

    'author': "Carlos Carballo",

    'category': 'Website',
    'version': '19.0.1.4.0',
    "installable": True,
    'application': True,
    'license': 'LGPL-3',
    # any module necessary for this one to work correctly
    'depends': ["base", "website", "auth_signup", "portal", "contacts", "mail",],

    "assets": {
        "web.assets_frontend": [
            # Estilos base de las pantallas de registro (s-eyebrow, sf-banner,
            # shrimp-page...): antes dependían de la hoja del marketplace.
            "shrimp_user_registry/static/src/css/shrimp_base.css",
        ],
    },

    # always loaded
    "data": [
        "security/ir.model.access.csv",
        "security/shrimp_partner_role_rules.xml",
        
        "data/shrimp_certificate_data.xml",

        "views/templates.xml",
        "views/partner_views.xml",
        "views/partner_role_views.xml",
        "views/auth_inherit.xml",
        "views/shrimp_certificate_views.xml",
        "views/menus.xml",
    ],
    
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
        # Demo masiva generada por
        # shrimp_marketplace/scripts/demo_masivo/generar.py (no editar a mano).
        'demo/demo_masivo_01_catalogo.xml',
        'demo/demo_masivo_02_socios.xml',
        'demo/demo_masivo_03_certificados.xml',
    ],
}

