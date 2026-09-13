{
    'name': 'Ecuador SRI - Community',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations/EDI',
    'summary': 'Comprobantes electrónicos, XAdES-BES, RIDE, ATS y auxiliares tributarios',
    'author': 'Desarrollo personalizado',
    'license': 'LGPL-3',
    'depends': ['l10n_ec', 'account_debit_note', 'stock', 'mail'],
    'external_dependencies': {'python': ['lxml', 'cryptography', 'requests']},
    'data': [
        'security/security.xml', 'security/ir.model.access.csv',
        'data/cron.xml', 'views/config_views.xml', 'views/document_views.xml',
        'views/account_views.xml', 'views/stock_views.xml',
        'wizard/report_views.xml', 'report/ride.xml', 'views/menus.xml',
    ],
    # Al instalar anota el código del catálogo del SRI en los IVA que se
    # puedan identificar por su tarifa y habilita las compañías de Ecuador
    # en ambiente de PRUEBAS. Sin eso el XML sale sin impuestos y el SRI
    # lo rechaza, y anotarlo a mano es trabajo mecánico y fácil de errar.
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
