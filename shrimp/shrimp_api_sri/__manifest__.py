{
    "name": "Camaronera — API externa: comprobantes SRI",
    "version": "19.0.1.0.0",
    "summary": "Expone en /api/v1/invoices los comprobantes electrónicos autorizados por el SRI "
               "(XML autorizado y RIDE) a su cliente o proveedor, y el webhook invoice.authorized",
    "description": """
Puente pequeño entre shrimp_api y l10n_ec_sri_community. Se instala solo
(auto_install) cuando están los dos.

* GET /api/v1/invoices (scope invoices:read): comprobantes autorizados o
  anulados cuyo receptor (o proveedor, en liquidaciones y retenciones) es el
  socio dueño de la clave.
* GET /api/v1/invoices/{clave_de_acceso}, /xml (solo el XML AUTORIZADO) y
  /ride.pdf.
* Webhook ``invoice.authorized``.

Nunca expone el XML sin firmar o firmado-sin-autorizar, ni certificados,
claves o configuración del SRI.
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Industries",
    "depends": ["shrimp_api", "l10n_ec_sri_community"],
    "data": [],
    "auto_install": True,
    "installable": True,
}
