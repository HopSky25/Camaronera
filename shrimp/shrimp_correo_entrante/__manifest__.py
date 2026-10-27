{
    "name": "CamaronMarket · Correo entrante",
    "version": "19.0.1.0.0",
    "summary": "Recibe en Odoo las respuestas por correo que Cloudflare le pasa (Email Worker)",
    "description": """
Las direcciones @camaronmarket.com las recibe Cloudflare Email Routing. Un Email
Worker reenvía cada correo al Gmail de la empresa y además lo entrega por HTTPS
a /correo/entrante, protegido con una clave (parámetro shrimp_correo.token).

Odoo lo procesa con su mecanismo normal (message_process): si es respuesta a un
correo enviado desde Odoo, queda en la conversación de ese registro
(oportunidad, contacto...). Si no responde a nada de Odoo, se ignora: el correo
sigue estando en Gmail y no se crea nada.
""",
    "author": "CamaronMarket",
    "license": "LGPL-3",
    "category": "Productivity/Discuss",
    "depends": ["mail"],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
