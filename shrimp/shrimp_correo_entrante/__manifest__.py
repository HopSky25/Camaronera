{
    "name": "CamaronMarket · Correo",
    "version": "19.0.1.3.1",
    "summary": "Bandeja de correo de los buzones @camaronmarket.com dentro de Odoo, al estilo de Outlook",
    "description": """
Las direcciones @camaronmarket.com las recibe Cloudflare Email Routing. Un Email
Worker reenvía cada correo al Gmail de la empresa y además lo entrega por HTTPS
a /correo/entrante, protegido con una clave (parámetro shrimp_correo.token).

Con cada correo que llega:

1. Odoo lo procesa con su mecanismo normal (message_process): si es respuesta a
   un correo enviado desde un registro (oportunidad, contacto...), queda en la
   conversación de ese registro.
2. Además se guarda en la bandeja del buzón al que iba dirigido (info@,
   facturacion@...), con sus adjuntos e imágenes.

Desde la app «Correo» cada usuario lee los buzones que tiene asignados,
responde, reenvía y redacta correos nuevos que salen con la dirección del
buzón (servidor saliente con from_filter camaronmarket.com). Un correo se
convierte en oportunidad del CRM o se vincula a un contacto con un botón.

La bandeja está en vivo: cuando llega un correo, quien ve ese buzón recibe
un aviso «Nuevo correo» y la lista y los contadores se actualizan solos
(bus de Odoo; en producción con workers hace falta el proxy de /websocket).

Asignar usuarios a los buzones: Correo › Configuración › Buzones, o desde un
shell con env["shrimp.buzon"]._shrimp_asignar_usuarios_por_login({...}).
""",
    "author": "CamaronMarket",
    "license": "LGPL-3",
    "category": "Productivity/Discuss",
    "depends": ["mail", "contacts", "crm"],
    "data": [
        "security/shrimp_correo_groups.xml",
        "security/ir.model.access.csv",
        "security/shrimp_correo_rules.xml",
        "data/shrimp_buzon_data.xml",
        "views/shrimp_correo_views.xml",
        "views/shrimp_buzon_views.xml",
        "views/menus.xml",
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "shrimp_correo_entrante/static/src/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
