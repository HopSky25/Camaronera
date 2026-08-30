{
    'name': "shrimp_user_registry",

    'summary': "Registro web de Semillero/Laboratorio/Camaronera con adjuntos",

    'description': """
Long description of module's purpose
    """,

    'author': "Shrimp User Registry",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Website',
    'version': '1.0.0',
    "installable": True,
    'application': True,
    'license': 'LGPL-3',
    # any module necessary for this one to work correctly
    'depends': ["base", "website", "auth_signup", "portal", "contacts", "mail",],

    # always loaded
    "data": [
        "security/ir.model.access.csv",
        
        "data/shrimp_certificate_data.xml",

        "views/templates.xml",
        "views/partner_views.xml",
        "views/auth_inherit.xml",
        "views/shrimp_certificate_views.xml",
        "views/menus.xml",
    ],
    
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}

