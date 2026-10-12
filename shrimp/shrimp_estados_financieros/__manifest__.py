{
    "name": "CamaronMarket - Estados financieros",
    "version": "19.0.1.0.0",
    "summary": "Balance de comprobación, libro mayor, libro diario, estado de situación "
               "financiera, estado de resultados integral y cartera por antigüedad, "
               "en PDF y Excel",
    "description": """
Estados financieros para Odoo 19 Community
==========================================

Odoo 19 Community no trae informes contables (son de Enterprise). Este módulo
añade, en Contabilidad > Informes > Estados financieros, un asistente que
genera en PDF y en Excel:

* Balance de comprobación (saldo inicial, débitos, créditos, saldo final).
* Libro mayor por cuenta, con saldo acumulado.
* Libro diario.
* Estado de situación financiera, con el resultado del ejercicio dentro del
  patrimonio y la comprobación Activo = Pasivo + Patrimonio.
* Estado de resultados integral, con columna comparativa opcional.
* Cartera por cobrar y por pagar por antigüedad.

Los niveles se arman agrupando por prefijo del código de cuenta (plan de la
Superintendencia de Compañías, l10n_ec). Detalle de los cálculos en README.md.
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Accounting/Accounting",
    "depends": ["account", "l10n_ec"],
    "data": [
        "security/ir.model.access.csv",
        "report/estados_financieros_report.xml",
        "wizard/estados_financieros_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
}
