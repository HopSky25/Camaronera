{
    "name": "CamaronMarket - Firma electrónica del informe de verificación",
    "summary": "La verificadora firma el informe en PDF (PAdES) con su certificado .p12 "
               "acreditado en Ecuador; las partes y terceros comprueban la firma por QR",
    "description": """
Firma electrónica del informe de verificación
=============================================

* Informe de verificación en PDF propio, con todo lo medido en campo y un QR a
  la página pública de comprobación.
* La empresa verificadora (su administrador o el técnico asignado) lo firma
  desde el portal con su firma electrónica (.p12/.pfx) emitida por una entidad
  de certificación acreditada en Ecuador. Firma PAdES-B-B (PAdES-B-T si se
  configura un servidor de sellado de tiempo), visible en la última página.
* El certificado y su contraseña se usan solo en memoria: no se guardan en la
  base de datos, ni en disco, ni en el log.
* Página pública /signature/<token> para comprobar la firma y
  descargar el PDF firmado; bloque de firma en la aceptación y la trazabilidad.
* Opcional (shrimp_firma.obligatoria): las partes no aceptan el informe hasta
  que esté firmado.

Requiere la librería pyHanko en el entorno de Odoo (ver README.md).
    """,
    "author": "Carlos Carballo",
    "category": "Sales/Marketplace",
    "version": "19.0.1.1.0",
    "license": "LGPL-3",
    "depends": ["shrimp_verification", "website"],
    "external_dependencies": {
        "python": ["pyhanko", "cryptography", "asn1crypto", "qrcode"],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/report_informe_verificacion.xml",
        "views/firma_templates.xml",
        "views/shrimp_verification_views.xml",
    ],
    "installable": True,
}
