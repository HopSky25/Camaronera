{
    "name": "Camaronera — Empacadora y listas de precios",
    "version": "19.0.1.8.0",
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
        "security/shrimp_reserva_rules.xml",
        "security/shrimp_lot_alert_rules.xml",
        "data/size_grade_extra.xml",
        "data/mail_template.xml",
        "data/mail_template_avisos.xml",
        "data/cron_auto_publish.xml",
        "data/aguaje_data.xml",
        "data/reserva_sequences.xml",
        "data/cron_avisos_lotes.xml",
        "views/shrimp_price_list_views.xml",
        "views/price_list_templates.xml",
        "views/res_partner_views.xml",
        "views/registry_form_packer.xml",
        "views/packer_profile_templates.xml",
        "views/reportes_inherit.xml",
        "views/navbar_inherit.xml",
        "views/mi_cuenta_inherit.xml",
        "views/avisos_lotes_inherit.xml",
        "views/productos_inherit.xml",
        "views/aceptacion_inherit.xml",
        "views/oferta_templates.xml",
        "views/publicar_inherit.xml",
        "views/menus.xml",
        "views/aguaje_views.xml",
        "views/simulador_templates.xml",
        "views/proveedores_templates.xml",
        "views/mi_historial_templates.xml",
        "views/lote_historial_inherit.xml",
        "views/reserva_templates.xml",
        "views/reserva_navbar_inherit.xml",
        # Ajustes › CamaronMarket › Reservas de cosecha y proveedores.
        "views/res_config_settings_views.xml",
    ],
    "demo": [
        "demo/demo_01_partners.xml",
        "demo/demo_02_price_lists.xml",
        "demo/demo_03_listas_semana.xml",
        # Demo masiva generada por
        # shrimp_marketplace/scripts/demo_masivo/generar.py (no editar a mano).
        "demo/demo_04_masivo_empacadoras.xml",
        "demo/demo_05_masivo_listas.xml",
        "demo/demo_06_masivo_compras_adulto.xml",
        "demo/demo_07_masivo_reservas.xml",
        "demo/demo_08_masivo_avisos.xml",
        "demo/demo_09_masivo_coherencia_demo_original.xml",
        # Compras entre camaroneras (adulto revendido y juveniles), mismo generador.
        "demo/demo_10_masivo_mismo_nivel.xml",
        # Verificaciones declaradas por las partes (mismo generador).
        "demo/demo_11_masivo_verificacion_declarada.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "shrimp_packer/static/src/css/packer.css",
        ],
    },
    "installable": True,
}
