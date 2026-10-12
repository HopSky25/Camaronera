{
    "name": "CamaronMarket — Facturación electrónica SRI de la plataforma",
    "version": "19.0.1.1.0",
    "summary": "Puente entre los cobros de la plataforma (shrimp.charge) y la "
               "facturación electrónica del SRI (l10n_ec_sri_community)",
    "description": """
Puente SRI de CamaronMarket
===========================

Se instala solo (auto_install) cuando están shrimp_marketplace y
l10n_ec_sri_community. Todo lo específico del SRI vive aquí, para que los
módulos de la plataforma no dependan de la localización ecuatoriana.

* Facturas de SERVICIO de la plataforma (comisión, honorario de verificación,
  comisión de empaque): diario, punto de emisión, tipo de documento (01
  Factura / 04 Nota de crédito) y forma de pago SRI configurables en Ajustes.
  Al contabilizar se crea el comprobante (ec.sri.document) en borrador; un cron
  de este módulo lo firma y lo encola (action_queue) y el cron del módulo SRI
  lo envía. Nada se firma ni se envía dentro de la petición del usuario.
* Identificación de los socios: tipo RUC / cédula / pasaporte según el número
  (reglas de Ecuador), tanto el de l10n_latam como el del SRI
  (ec_sri_identification_type), al registrarse y en la migración.

La plataforma NO emite facturas de mercadería: esas las emite el vendedor y
se registran en la compra (shrimp.transaction.seller_invoice_*).
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Accounting/Localizations/EDI",
    "depends": ["shrimp_marketplace", "l10n_ec_sri_community"],
    "data": [
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "views/shrimp_charge_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "auto_install": True,
    "installable": True,
}
