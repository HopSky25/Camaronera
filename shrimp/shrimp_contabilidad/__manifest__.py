{
    "name": "CamaronMarket - Área contable",
    "version": "19.0.1.0.0",
    "summary": "Grupos contables, plan de cuentas de servicios, productos, analítica, "
               "diarios y menú «Contabilidad» para el área contable de CamaronMarket",
    "description": """
Área contable de CamaronMarket
==============================

La plataforma no compra ni vende camarón: factura servicios (comisión por
venta, honorario de verificación, comisión de empaque, suscripción de
empacadoras y comisión de larva/nauplio). Este módulo deja al área contable
todo a la mano sobre lo que la plataforma ya genera (shrimp.charge):

* Grupos «Asistente contable» y «Contador general» (sin administración del
  sistema ni de la plataforma).
* Cuentas de ingreso por línea de servicio bajo 410201 (Supercias), pasivo de
  honorarios por pagar a verificadoras, costo de verificadoras y gastos
  administrativos frecuentes (reutiliza las del plan l10n_ec cuando existen).
* Productos de servicio con cuenta e IVA 15 % (reutiliza los de los cobros).
* Plan analítico «Línea de negocio» y modelos de distribución por producto.
* Diarios: Facturas de servicio, Banco principal, Caja chica, Provisiones y
  ajustes; términos de pago Contado/15/30 días.
* Menú de nivel superior «Contabilidad» con accesos a clientes, proveedores,
  bancos, cobros de la plataforma (solo lectura), asientos, informes, SRI y
  configuración.

Todo lo crea el post_init_hook de forma idempotente. Ver README.md.
""",
    "author": "Carlos Carballo",
    "category": "Accounting/Accounting",
    "license": "LGPL-3",
    "depends": [
        "account",
        "l10n_ec",
        "l10n_ec_sri_community",
        "shrimp_marketplace",
        "analytic",
    ],
    "data": [
        "security/shrimp_contabilidad_groups.xml",
        "security/ir.model.access.csv",
        "data/shrimp_contabilidad_data.xml",
        "views/shrimp_charge_views.xml",
        "views/account_actions.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "application": True,
    "installable": True,
}
