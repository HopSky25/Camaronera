{
    'name': "shrimp_marketplace",

    'summary': "Productos de semillero y transacciones laboratorio/camaronera",

    'description': """
Marketplace de camarón con trazabilidad: publicación de productos de semillero,
transacciones semillero→laboratorio y laboratorio→camaronera, lotes, certificados
y portal público.
    """,

    'author': "Shrimp Marketplace",
    'website': "https://www.yourcompany.com",

    'category': 'Sales/Marketplace',
    'version': '1.3.1',

    # any module necessary for this one to work correctly
    'depends': ["base", "website", "portal", "sale_management", "account", "shrimp_user_registry"],

    "assets": {
        "web.assets_frontend": [
            "shrimp_marketplace/static/src/css/shrimp_marketplace.css",
            "shrimp_marketplace/static/src/js/shrimp_landing.js",
        ],
    },

    # always loaded
    "data": [
        "security/ir.model.access.csv",
        "security/shrimp_rules.xml",

        "data/sequence.xml",
        "data/shrimp_uom_data.xml",
        "data/shrimp_size_grade_data.xml",
        "data/shrimp_species_data.xml",
        "data/shrimp_stage_data.xml",
        "data/shrimp_genetics_line_data.xml",

        'views/shrimp_master_data_views.xml',
        'views/portal_my_home_inherit.xml',
        "views/shrimp_transaction_views.xml",
        "views/shrimp_product_views.xml",
        "views/shrimp_stock_lot_views.xml",
        "views/shrimp_check_request.xml",
        "views/menus.xml",
        "views/shrimp_user_cert_approval_views.xml",

        "views/components_template.xml",
        "views/website_menu.xml",
        "views/navbar_dropdown.xml",
        "views/landing_template.xml",
        "views/marketplace_public_template.xml",
        "views/product_portal_template.xml",
        "views/transaction_portal_template.xml",
        "views/account_portal_template.xml",

        "views/shrimp_traceability_pdf.xml",
        "views/mail_template.xml",
        "views/res_config_settings_views.xml",
        "views/shrimp_reports.xml",
        "views/shrimp_api_key_views.xml",
        "views/shrimp_uom_views.xml",
        "views/shrimp_size_grade_views.xml",

        # Datos de ejemplo realistas, divididos por modelo/relación. Usan
        # <data noupdate="1">: se crean una sola vez y no se reprocesan en -u.
        "demo/demo_01_attachments.xml",
        "demo/demo_02_partners.xml",
        "demo/demo_03_facilities.xml",
        "demo/demo_04_ponds.xml",
        "demo/demo_05_user_certificates.xml",
        "demo/demo_06_products.xml",
        "demo/demo_07_product_certificates.xml",
        "demo/demo_08_reviews.xml",
        "demo/demo_09_check_requests.xml",
        "demo/demo_10_transactions.xml",
        "demo/demo_11_evolution.xml",

    ],
    'demo': [],
    "post_init_hook": "post_init_hook",
    "application": True,
    "license": "LGPL-3",
}

