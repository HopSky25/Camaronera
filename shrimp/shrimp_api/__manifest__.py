{
    "name": "Camaronera — API externa (v1)",
    "version": "19.0.1.3.0",
    "summary": "API REST/JSON versionada (/api/v1) con claves con hash, scopes, "
               "webhooks firmados, trazabilidad pública por token y OpenAPI 3.1",
    "description": """
API externa del marketplace de camarón
======================================

Dueña de todo lo que cuelga de ``/api/v1``. Sustituye a la API que vivía en
``shrimp_marketplace/controllers/api.py`` manteniendo las rutas de productos,
instalaciones y piscinas.

* Claves ``trz_<prefijo>_<secreto>``: solo se guarda el prefijo y un hash
  PBKDF2; el secreto se enseña una vez. Caducidad, revocación, lista de IPs,
  orígenes CORS y scopes granulares.
* Lecturas con el usuario dueño de la clave, sin sudo: la visibilidad la
  deciden las ACL y las ir.rule del portal.
* Escrituras por los mismos métodos de negocio que usa el portal.
* Errores application/problem+json (RFC 9457), paginación por cursor opaco,
  Idempotency-Key, límite de peticiones por clave y webhooks firmados.
* Página pública de trazabilidad por token revocable (/t/<token>).

La guía para integradores está en README.md.
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Industries",
    "depends": ["shrimp_copacking", "portal", "website"],
    "data": [
        "security/ir.model.access.csv",
        "security/shrimp_api_security.xml",
        "data/api_scope_data.xml",
        "data/ir_cron.xml",
        "views/api_key_views.xml",
        "views/webhook_views.xml",
        "views/transaction_views.xml",
        "views/menus.xml",
        "views/portal_templates.xml",
        "views/traceability_public_templates.xml",
        # Pasos internos entre perfiles de la misma empresa (por herencia).
        "views/profile_transfer_public_templates.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
