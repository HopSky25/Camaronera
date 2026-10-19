{
    "name": "Camaronera — Servicio de empaque (co-packing)",
    "version": "19.0.1.17.0",
    "summary": "Maquiladores que empacan camaron ajeno, y el acta que cuadra las libras",
    "description": """
Hay plantas que no compran camaron: lo empacan para otros. La camaronera o la
empacadora que no se abastece les manda su producto ya listo para empacar, con
sus propios insumos, y paga por libra empacada.

Lo que resuelve este modulo no es el rendimiento —no hay merma que conciliar,
el camaron llega limpio— sino dos cosas:

1. El cuadre. Entraron 40.000 libras, tienen que salir 40.000 empacadas. El
   acta la firman las dos partes y deja constancia de la diferencia si la hay.

2. La trazabilidad. Hoy, cuando un lote se va a empacar fuera, la cadena se
   corta: el certificado no puede decir donde se empaco ni bajo que codigo de
   establecimiento. Con el maquilador dentro de la plataforma, el paso queda
   registrado y sale impreso en el certificado del lote.

Va en modulo aparte, como verificacion, porque es un rol distinto con su propio
sitio y sus propias bandejas.
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Industries",
    # shrimp_verification ya no figura: este módulo no usa nada suyo (llega
    # igual, a través de shrimp_packer). Sí se usan el marketplace, el
    # registro y la empacadora (grupos de socios, menú de listas de precios).
    "depends": ["shrimp_marketplace", "shrimp_user_registry", "shrimp_packer", "website"],
    "pre_init_hook": "pre_init_hook",
    "data": [
        "data/sequences.xml",
        # Plazos del acta y vencimiento de solicitudes y ofertas (D-14, D-47).
        "data/ir_cron.xml",
        "security/ir.model.access.csv",
        "security/shrimp_copacking_rules.xml",
        "views/copack_client_templates.xml",
        "views/copack_request_templates.xml",
        "views/copack_order_templates.xml",
        "views/copack_self_templates.xml",
        "views/copacker_templates.xml",
        "views/copacker_tariff_templates.xml",
        "views/registry_form_maquilador.xml",
        "views/res_partner_views.xml",
        "views/traceability_pdf_inherit.xml",
        "views/traceability_screen_inherit.xml",
        "views/navbar_inherit.xml",
        "views/menus.xml",
        "views/copack_landing_templates.xml",
        # Ajustes › CamaronMarket › Empaque (co-packing).
        "views/res_config_settings_views.xml",
        # Ficha interna de la orden: mediación de disputas y cancelaciones (D-14, D-34).
        "views/copack_backend_views.xml",
        "data/site_config.xml",
        "data/brand_config.xml",
        # Borradores de los anexos legales con las reglas de maquila.
        "data/legal_drafts_data.xml",
    ],
    # Datos de ejemplo: generados por
    # shrimp_marketplace/scripts/demo_masivo/generar.py (no editar a mano).
    "demo": [
        "demo/demo_01_maquiladores.xml",
        "demo/demo_02_tarifas.xml",
        "demo/demo_03_solicitudes_ordenes.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "shrimp_copacking/static/src/css/copacking_landing.css",
            # Portal (maquilador y cliente): tarjetas, línea de tiempo, píldoras.
            "shrimp_copacking/static/src/css/copacking_portal.css",
            # Modo oscuro (generada + retoques a mano con la paleta coral).
            "shrimp_copacking/static/src/css/copacking_dark_auto.css",
            "shrimp_copacking/static/src/css/copacking_dark.css",
        ],
    },
    "installable": True,
}
