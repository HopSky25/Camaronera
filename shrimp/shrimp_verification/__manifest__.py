{
    "name": "CamaronMarket Verificadores — Verificación de camarón",
    "summary": "Verificación obligatoria en campo antes de concluir la compra de camarón adulto",
    "description": """
Introduce la figura del VERIFICADOR: un tercero acreditado que inspecciona el
camarón en campo antes de que la compra se concrete.

Flujo:
  1. El comprador compra camarón adulto y elige un verificador registrado.
  2. La transacción queda en 'Pendiente de verificación' y RESERVA el stock
     (no lo consume todavía).
  3. Al verificador le llega la orden a su bandeja, va a campo y registra los
     cinco análisis: peso, cuerpo o cola, metabisulfito, clasificación y sabor.
  4. Al aprobar, el comprador recibe la alerta y concluye la compra: ahí sí se
     consumen los lotes y se genera la trazabilidad.

Calcula automáticamente rendimientos, sobrepeso y porcentajes por clase, y
genera el parte en el mismo formato de texto que el equipo ya usa por WhatsApp.
    """,
    "author": "Carlos Carballo",
    "category": "Sales/Marketplace",
    "version": "19.0.1.25.0",
    "license": "LGPL-3",
    "depends": ["shrimp_marketplace"],
    "pre_init_hook": "pre_init_hook",
    "data": [
        "security/ir.model.access.csv",
        "security/shrimp_verification_rules.xml",
        "security/shrimp_dispatch_rules.xml",
        # Matriz de verificación (vendedor → comprador) y sus reglas iniciales.
        "data/verification_rule_data.xml",
        "data/sequence.xml",
        "data/mail_template.xml",
        "data/mail_template_dispatch.xml",
        # Equipo de campo: aviso al técnico de apoyo.
        "data/mail_template_team.xml",
        "data/ir_cron.xml",
        "data/verifier_certificate_data.xml",
        "data/taste_criterion_data.xml",
        "data/tech_role_data.xml",
        "views/res_config_settings_views.xml",
        "views/verifier_approval_views.xml",
        "views/shrimp_verification_views.xml",
        # Equipo de campo (responsable + apoyo) en el backoffice.
        "views/team_views.xml",
        # Plazos de la verificadora, reasignar y cancelar (19.0.1.21.0).
        "views/deadline_views.xml",
        # Decisiones de la auditoría (19.0.1.24.0): respuesta de la verificadora,
        # producto no presentado, contraoferta, liquidación y segunda opinión.
        "views/decisions_views.xml",
        "views/declared_views.xml",
        "views/shrimp_dispatch_views.xml",
        # registry_form_inherit.xml retirado en 19.0.1.12.0: el registro común es
        # ahora un asistente y en la plataforma de verificadores /register lleva
        # a /register/verifier.
        "data/legal_annex_data.xml",
        # Borradores legales: cancelación y cierre de compras (19.0.1.21.0).
        "data/purchase_policy_data.xml",
        # Cron de acreditaciones (D-23) y borradores legales de las decisiones.
        "data/decisions_data.xml",
        # 19.0.1.17.0: ya no es un formulario propio sino el bloque «Verificadora»
        # del asistente común (/register/verifier lo abre con el perfil marcado).
        "views/registry_form_verifier.xml",
        "views/verifier_website.xml",
        "views/verifier_profile_templates.xml",
        # Antes de portal_templates.xml: al actualizar una BD existente, Odoo valida
        # la vista padre con esta herencia aun en su version vieja (anchor trazabilidad/pdf).
        "views/purchases_list_inherit.xml",
        "views/portal_templates.xml",
        # Aviso de verificación en el formulario de publicar producto.
        "views/product_form_inherit.xml",
        "views/verifier_full_detail.xml",
        "views/acceptance_templates.xml",
        # Verificación declarada por las partes (formulario y avisos).
        "views/declared_templates.xml",
        "views/technicians_templates.xml",
        # Después de portal_templates.xml y de las listas del marketplace:
        # hereda de todas ellas.
        "views/dispatch_templates.xml",
        "views/traceability_inherit.xml",
        # Verificación vencida: cancelar sin costo o cambiar de verificadora.
        "views/deadline_templates.xml",
        "views/decisions_templates.xml",
        "views/traceability_pdf_inherit.xml",
        "views/portal_home_inherit.xml",
        "views/navbar_dropdown_inherit.xml",
        "views/menus.xml",
        "views/shrimp_verification_rule_views.xml",
        # Columna «Confirmación de recepción» de la matriz.
        "views/reception_rule_views.xml",
        "views/shrimp_taste_criterion_views.xml",
        "views/shrimp_tech_role_views.xml",
        "data/site_config.xml",
        "data/brand_config.xml",
    ],
    # Van en 'demo' y no en 'data': son escenarios de ejemplo (empresas
    # verificadoras, sus técnicos, 80 verificaciones con sus aceptaciones y
    # calificaciones) y no deben entrar nunca en una base de producción.
    "demo": [
        "demo/demo_01_verifier_partners.xml",
        "demo/demo_02_verifier_technicians.xml",
        "demo/demo_03_verifier_accreditations.xml",
        "demo/demo_04_verifications_adult.xml",
        "demo/demo_05_verifications_larvae.xml",
        "demo/demo_06_acceptances.xml",
        "demo/demo_07_verifier_reviews.xml",
        # Demo masiva generada por
        # shrimp_marketplace/scripts/demo_masivo/generar.py (no editar a mano).
        "demo/demo_08_masivo_verificadores.xml",
        "demo/demo_09_masivo_verificaciones_larva.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "shrimp_verification/static/src/css/verification.css",
            # Bloque «Verificación» de la página de compra (rediseño 2026-10).
            "shrimp_verification/static/src/js/verification_buy.js",
            # Modo oscuro (generada + retoques a mano con la paleta teal).
            "shrimp_verification/static/src/css/verification_dark_auto.css",
            "shrimp_verification/static/src/css/verification_dark.css",
        ],
    },
    "installable": True,
}
