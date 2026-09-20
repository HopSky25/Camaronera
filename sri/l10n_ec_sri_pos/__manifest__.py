{
    'name': 'Ecuador SRI - Punto de Venta',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations/Point of Sale',
    'summary': 'Emite factura electrónica al SRI automáticamente desde el POS',
    'author': 'Desarrollo personalizado',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'l10n_ec_sri_community'],
    'data': [
        'views/pos_config_views.xml',
        'views/establishment_views.xml',
    ],
    # Puente POS <-> SRI: cada venta del POS en una caja con punto de emisión
    # asignado genera automáticamente su factura electrónica (comprobante 01),
    # se firma en el momento y se envía al SRI de forma asíncrona (sin bloquear
    # la caja). El esquema offline del SRI no contempla "nota de venta"
    # electrónica; las ventas a mostrador se emiten como factura a consumidor
    # final (9999999999999) hasta USD 50.
    'installable': True,
}
