{
    'name': "CamaronMarket — Marketplace",

    'summary': "Productos de naupliero y transacciones laboratorio/camaronera",

    'description': """
Marketplace de camarón con trazabilidad: publicación de productos de naupliero,
transacciones naupliero→laboratorio y laboratorio→camaronera, lotes, certificados
y portal público.
    """,

    'author': "Carlos Carballo",

    'category': 'Sales/Marketplace',
    'version': '19.0.1.40.0',

    # any module necessary for this one to work correctly
    'depends': ["base", "website", "portal", "sale_management", "account", "shrimp_user_registry"],

    "assets": {
        "web.assets_frontend": [
            # Bloqueo de scroll compartido para modales, cajones y overlays
            # (window.ShrimpScrollLock; 19.0.1.29.0). Antes que los demás.
            "shrimp_marketplace/static/src/css/shrimp_scroll_lock.css",
            "shrimp_marketplace/static/src/js/shrimp_scroll_lock.js",
            "shrimp_marketplace/static/src/css/shrimp_marketplace.css",
            # Fase 1 larva/nauplio: Mi panel, filtros de larva, ficha de calidad.
            "shrimp_marketplace/static/src/css/shrimp_panel.css",
            "shrimp_marketplace/static/src/js/shrimp_landing.js",
            # Cajón de filtros reutilizable (todas las páginas con filtros).
            "shrimp_marketplace/static/src/js/shrimp_filter_drawer.js",
            # Publicar / editar producto (vista previa en vivo, fotos,
            # certificados en modal, checklist).
            "shrimp_marketplace/static/src/css/shrimp_product_form.css",
            "shrimp_marketplace/static/src/js/shrimp_product_form.js",
            # Ficha del producto (galería, compra fija, certificados en modal,
            # barra de acción móvil).
            "shrimp_marketplace/static/src/css/shrimp_product_detail.css",
            # Página de compra (pasos + resumen fijo + barra móvil).
            "shrimp_marketplace/static/src/css/shrimp_buy.css",
            "shrimp_marketplace/static/src/js/shrimp_buy.js",
            # «¡Compra realizada!» a todo el ancho (banda + detalle + resumen fijo).
            "shrimp_marketplace/static/src/css/shrimp_thanks.css",
            # Mi cuenta: navegación lateral, tarjetas plegables e
            # «Instalaciones y piscinas» con modales (19.0.1.28.0).
            "shrimp_marketplace/static/src/css/shrimp_my_account.css",
            "shrimp_marketplace/static/src/js/shrimp_my_account.js",
            # «Estado real del lote»: bloque de la ficha y modal de observación.
            "shrimp_marketplace/static/src/css/shrimp_product_evolution.css",
            "shrimp_marketplace/static/src/js/shrimp_product_evolution.js",
            # Modo oscuro: la capa generada desde las hojas claras
            # (scripts/generar_modo_oscuro.py), después los tokens, los
            # componentes de Odoo y los retoques a mano, y el botón luna/sol.
            "shrimp_marketplace/static/src/css/shrimp_dark_auto.css",
            "shrimp_marketplace/static/src/css/shrimp_dark.css",
            "shrimp_marketplace/static/src/js/shrimp_dark_mode.js",
        ],
    },

    # always loaded
    "data": [
        "security/shrimp_groups.xml",
        "security/ir.model.access.csv",
        "security/shrimp_rules.xml",
        "security/shrimp_larva_access.xml",

        "data/sequence.xml",
        "data/ir_cron.xml",
        # Caducidad de publicaciones (D-31) y vencimiento de certificados (D-13).
        "data/decisiones_cron.xml",
        "data/shrimp_uom_data.xml",
        "data/shrimp_size_grade_data.xml",
        "data/shrimp_species_data.xml",
        "data/shrimp_stage_data.xml",
        "data/shrimp_genetics_line_data.xml",
        "data/config_parameter_data.xml",
        # Borradores legales de entregas, retrasos y strikes (una vez).
        "data/delay_policy_data.xml",
        # Borradores legales: publicaciones, sanidad y certificados (D-30/31/13).
        "data/decision_policy_data.xml",

        'views/shrimp_master_data_views.xml',
        'views/portal_my_home_inherit.xml',
        "views/shrimp_transaction_views.xml",
        # Recepción por el comprador e incidencias (ficha interna).
        "views/shrimp_transaction_reception_views.xml",
        # Retrasos de entrega (pestaña de la compra) y strikes / suspensiones
        # (ficha del socio), sin menús nuevos.
        "views/shrimp_delay_views.xml",
        "views/shrimp_product_views.xml",
        "views/shrimp_stock_lot_views.xml",
        "views/shrimp_check_request.xml",
        "views/menus.xml",
        # Solicitudes LOPDP y purgas de retención (hueco #7)
        "views/lopdp_menus.xml",
        "views/shrimp_user_cert_approval_views.xml",

        "views/components_template.xml",
        "views/website_menu.xml",
        "data/menu_config.xml",
        "data/brand_config.xml",
        "views/marca_website.xml",
        "views/navbar_dropdown.xml",
        # Modo oscuro: data-bs-theme en <html> y botón luna/sol en la cabecera.
        "views/dark_mode_templates.xml",
        "views/website_settings_views.xml",
        "views/landing_template.xml",
        # Barra · etiquetas · cajón de filtros reutilizables (t-call).
        "views/filter_drawer_templates.xml",
        "views/marketplace_public_template.xml",
        # «¡Compra realizada!» (buy_thanks), rediseño 2026-10.
        "views/buy_thanks_template.xml",
        "views/product_portal_template.xml",
        "views/transaction_portal_template.xml",
        "views/account_portal_template.xml",
        # Mi cuenta · sección «Instalaciones y piscinas» (19.0.1.28.0).
        "views/my_account_facilities.xml",
        # Recepción por el comprador: página de la compra, «Mis compras».
        "views/reception_templates.xml",
        # Retraso de entrega: aviso, negociación de una ronda y cancelación
        # sin costo (página de la compra, Ventas, Mis compras, perfil).
        "views/delay_templates.xml",
        # Cancelación de la compra y «No entregada» (página de la compra).
        "views/cancel_templates.xml",
        # Paso «Instalaciones» del asistente de alta (19.0.1.22.0).
        "views/signup_facilities.xml",

        "views/shrimp_traceability_pdf.xml",
        "views/shrimp_purchase_receipt.xml",
        "views/mail_template.xml",
        "views/shrimp_reports.xml",
        "views/shrimp_api_key_views.xml",
        "views/shrimp_uom_views.xml",
        # Después de las unidades de medida: Ajustes enlaza a su acción.
        "views/res_config_settings_views.xml",
        # Ajustes › Entregas y recepción: retrasos de entrega y strikes.
        "views/delay_settings_views.xml",
        # Cancelación general (comprador, vendedor, mutuo acuerdo, plataforma)
        # y desenlaces de la incidencia (19.0.1.34.0).
        "views/cancel_views.xml",
        # Ajustes › Protección de datos: plazos LOPDP y retención (hueco #7)
        "views/lopdp_settings_views.xml",
        "views/shrimp_size_grade_views.xml",
        "views/shrimp_review_views.xml",
        "views/shrimp_product_evolution_views.xml",
        # Salidas / exportaciones (último eslabón de la trazabilidad),
        # tipos de movimiento y siembras de origen del producto.
        "views/shrimp_export_views.xml",
        "views/export_portal_templates.xml",
        # Siembra desde el portal (formulario y botón «Sembrar»).
        "views/sowing_portal_templates.xml",
        # Historial de decisiones y «deshacer mi decisión» de las firmas de
        # dos partes (bloques de portal, correo y asistente del gestor).
        "views/signoff_templates.xml",
        # Fase 1 larva/nauplio (por herencia, sin reescribir las plantillas base):
        # Mi panel, filtros de larva del catálogo, ficha de calidad y landing.
        "views/dashboard_templates.xml",
        "views/larva_marketplace_templates.xml",
        "views/quality_templates.xml",
        "views/landing_larva_templates.xml",
        # Mover producto propio entre los perfiles de la misma cuenta
        # (transferencia interna): back-office y portal, por herencia.
        "views/profile_transfer_views.xml",
        "views/profile_transfer_templates.xml",

        # «Estado real del lote»: observaciones del vendedor (ficha, Mis
        # productos y Ajustes), por herencia.
        "views/product_evolution_templates.xml",
        "views/shrimp_evolution_settings_views.xml",

        # Reglas de la compra (auditoría de procesos §10–11): factura del
        # vendedor con plazo y bloqueo (D-43), Ajustes y borradores legales.
        "views/purchase_rules_views.xml",
        "data/purchase_rules_data.xml",
        # Decisiones de la auditoría (19.0.1.38.0): PCR positivo (D-30),
        # caducidad de publicaciones (D-31) y vigencia de certificados (D-13):
        # back-office y portal, por herencia.
        "views/decisiones_views.xml",
        "views/decisiones_templates.xml",

        # Va el ÚLTIMO a propósito: sobrescribe los grupos de menús que crean
        # los archivos anteriores (reportes, tallas, certificados de usuario).
        "views/shrimp_menu_groups.xml",

    ],
    # Datos de ejemplo realistas, divididos por modelo/relación. Usan
    # <data noupdate="1">: se crean una sola vez y no se reprocesan en -u.
    #
    # Van en 'demo' y NO en 'data'. Estaban en 'data', que Odoo carga en TODA
    # instalación aunque se pase --without-demo: 1.822 registros de ejemplo
    # entrarían en producción. Lo peor no son los lotes de mentira, son las
    # 67 reseñas: shrimp_rating_avg está almacenado y sale de ahí, así que la
    # reputación pública de los vendedores —lo que un comprador mira para
    # decidir— venía fabricada. En shrimp_user_registry ya estaba bien puesto,
    # lo que confirma que aquí era un descuido.
    "demo": [
        "demo/demo_01_attachments.xml",
        "demo/demo_02_partners.xml",
        "demo/demo_03_facilities.xml",
        "demo/demo_04_ponds.xml",
        "demo/demo_05_user_certificates.xml",
        "demo/demo_06_products.xml",
        "demo/demo_07_product_certificates.xml",
        "demo/demo_08_reviews.xml",
        "demo/demo_10_transactions.xml",
        "demo/demo_11_evolution.xml",
        # Demo masiva generada por scripts/demo_masivo/generar.py (no editar a
        # mano): cadena completa con movimientos, lotes, siembras y cobros.
        "demo/demo_12_masivo_instalaciones.xml",
        "demo/demo_13_masivo_productos.xml",
        "demo/demo_14_masivo_transacciones.xml",
        "demo/demo_15_masivo_accesos.xml",
        "demo/demo_16_masivo_calidad.xml",
        # Compras al mismo nivel (semillero/laboratorio), mismo generador.
        "demo/demo_17_masivo_mismo_nivel.xml",
        # «Estado real del lote»: observaciones con parámetros de agua.
        "demo/demo_18_masivo_observaciones.xml",
        # Recepción: decide qué compras de la demo la llevan (plazo desde hoy).
        "demo/demo_99_reception.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "post_init_hook": "post_init_hook",
    "application": True,
    "license": "LGPL-3",
}

