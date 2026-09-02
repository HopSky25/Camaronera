{
    "name": "Verificación de Camarón",
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
    "author": "Camaronera",
    "category": "Sales/Marketplace",
    "version": "19.0.1.1.0",
    "license": "LGPL-3",
    "depends": ["shrimp_marketplace"],
    "data": [
        "security/ir.model.access.csv",
        "security/shrimp_verification_rules.xml",
        "data/sequence.xml",
        "data/mail_template.xml",
        "data/verifier_certificate_data.xml",
        "views/res_config_settings_views.xml",
        "views/verifier_approval_views.xml",
        "views/shrimp_verification_views.xml",
        "views/registry_form_inherit.xml",
        "views/website_settings_views.xml",
        "views/verifier_website.xml",
        "views/portal_templates.xml",
        "views/technicians_templates.xml",
        "views/purchases_list_inherit.xml",
        "views/traceability_inherit.xml",
        "views/traceability_pdf_inherit.xml",
        "views/portal_home_inherit.xml",
        "views/navbar_dropdown_inherit.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "shrimp_verification/static/src/css/verification.css",
        ],
    },
    "application": True,
    "installable": True,
}
