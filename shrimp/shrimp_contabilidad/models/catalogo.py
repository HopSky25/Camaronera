"""Catálogo contable de CamaronMarket: lo que el área contable necesita.

Un solo sitio con los códigos, nombres y relaciones. El setup
(shrimp.contabilidad.setup) lo aplica de forma idempotente; la guía y el
README lo describen con los mismos datos.

Códigos verificados contra el plan Supercias de l10n_ec
(src/addons/l10n_ec/data/template/account.account-ec.csv y
account.group-ec.csv):

* 4102 (grupo) Prestación de servicios -> 410201 (cuenta) Prestación de
  servicios. Las cuentas de ingreso por línea cuelgan de 410201 con dos
  dígitos más (410201xx), como pide la estructura Supercias.
* 2103 (grupo) Cuentas y documentos por pagar -> 210301 «Cuentas y documentos
  por pagar locales» (NO relacionadas; las relacionadas son 2108). El pasivo
  con las verificadoras es 21030101, hijo de 210301.
* 5202 (grupo) Gastos administrativos (en l10n_ec 5201 es «Gastos de ventas»
  y 5203 «Gastos financieros») -> 520228 «Otros gastos»: 52022801..52022817 y
  52022818 (grupo de no deducibles) ya existen, las nuevas son 52022819 y
  52022820.
* 5104 Otros costos indirectos -> 510408 «Otros costos de producción»
  (51040801..51040803 existen): el costo de las verificadoras es 51040804.
"""

# Líneas de negocio (plan analítico «Línea de negocio").
# clave -> (nombre de la cuenta analítica, código)
LINEAS = {
    "marketplace": ("Marketplace", "MKT"),
    "verificacion": ("Verificación", "VER"),
    "empaque": ("Empaque", "EMP"),
    "suscripciones": ("Suscripciones", "SUS"),
    "larva": ("Larva y nauplio", "LAR"),
}
PLAN_ANALITICO = "Línea de negocio"

# Cuentas. clave -> dict(code, name, type, reconcile, tmpl)
#   tmpl: xmlid de la plantilla l10n_ec (sin «account.<cid>_») cuando la
#   cuenta ya viene en el plan; se busca primero por ese xmlid, después por
#   código y después por nombre. Si no aparece, se crea.
CUENTAS = {
    # Ingresos (410201xx)
    "ing_marketplace": dict(code="41020101", name="Comisiones por intermediación (marketplace)",
                            type="income"),
    "ing_verificacion": dict(code="41020102", name="Honorarios de verificación en campo",
                             type="income"),
    "ing_empaque": dict(code="41020103", name="Comisiones de empaque y maquila",
                        type="income"),
    "ing_suscripciones": dict(code="41020104", name="Suscripciones de empacadoras",
                              type="income"),
    "ing_larva": dict(code="41020105", name="Comisiones por ventas de larva y nauplio",
                      type="income"),
    # Pasivo con las verificadoras (hijo de 210301, no relacionadas)
    "pas_verificadoras": dict(code="21030101", name="Honorarios por pagar a verificadoras",
                              type="liability_payable", reconcile=True),
    # Costo de la verificación (la parte que se paga a la verificadora)
    "cos_verificadoras": dict(code="51040804", name="Costo de honorarios de verificadoras",
                              type="expense_direct_cost"),
    # Gastos administrativos (5202)
    "gas_servidores": dict(code="52022819", name="Servidores y dominios", type="expense"),
    "gas_software": dict(code="52022820", name="Software y licencias", type="expense"),
    # Reutilizadas del plan l10n_ec
    "gas_publicidad": dict(code="520211", name="Promocion y publicidad", type="expense",
                           tmpl="ec520211"),
    "gas_honorarios": dict(code="520205",
                           name="Honorarios, comisiones y dietas a personas naturales",
                           type="expense", tmpl="ec520205"),
    "gas_comisiones_bancarias": dict(code="520302", name="Comisiones", type="expense",
                                     tmpl="ec520302"),
}

# Grupos de cuentas de 6 dígitos que no trae l10n_ec y que ordenan las
# cuentas nuevas debajo de su cuenta madre en los informes.
GRUPOS_CUENTAS = [
    ("410201", "Prestación de servicios"),
    ("210301", "Cuentas y documentos por pagar locales"),
]

# Impuestos l10n_ec (xmlid de plantilla).
IVA_VENTA_SERVICIOS = "tax_vat_15_411_services"   # IVA 15 % (411) servicios
IVA_COMPRA_15 = "tax_vat_15_510_sup_01"           # IVA 15 % (510, sustento 01 crédito)
IVA_COMPRA_0 = "tax_vat_517_sup_02"               # IVA 0 % (517, sustento 02 costo IR)

# Tipos de cobro de shrimp.charge -> línea de negocio.
COBRO_A_LINEA = {
    "commission": "marketplace",
    "verification_fee": "verificacion",
    "check_fee": "verificacion",      # chequeo del flujo antiguo (legado)
    "copack_platform": "empaque",
}

# Línea -> cuenta de ingreso.
LINEA_A_CUENTA = {
    "marketplace": "ing_marketplace",
    "verificacion": "ing_verificacion",
    "empaque": "ing_empaque",
    "suscripciones": "ing_suscripciones",
    "larva": "ing_larva",
}

# Productos de ingreso propios de este módulo (los que no tienen tipo de
# cobro en la plataforma). clave -> (xmlid, nombre, código interno, línea)
PRODUCTOS_INGRESO_PROPIOS = {
    "suscripciones": ("shrimp_contabilidad.product_suscripcion_empacadora",
                      "Suscripción mensual de empacadora", "SUSC-EMPACADORA",
                      "suscripciones"),
    "larva": ("shrimp_contabilidad.product_comision_larva",
              "Comisión marketplace – larva y nauplio", "COMISION-LARVA", "larva"),
}

# Productos de gasto frecuente. clave -> (xmlid, nombre, código, cuenta, IVA)
PRODUCTOS_GASTO = {
    "servidores": ("shrimp_contabilidad.product_gasto_servidores",
                   "Servidores y dominios", "GASTO-SERVIDORES", "gas_servidores",
                   IVA_COMPRA_15),
    "software": ("shrimp_contabilidad.product_gasto_software",
                 "Software y licencias", "GASTO-SOFTWARE", "gas_software", IVA_COMPRA_15),
    "publicidad": ("shrimp_contabilidad.product_gasto_publicidad",
                   "Publicidad y marketing", "GASTO-PUBLICIDAD", "gas_publicidad",
                   IVA_COMPRA_15),
    "honorarios": ("shrimp_contabilidad.product_gasto_honorarios",
                   "Honorarios profesionales", "GASTO-HONORARIOS", "gas_honorarios",
                   IVA_COMPRA_15),
    # Servicios financieros: IVA 0 % (art. 56 LRTI). Por confirmar con el contador.
    "comisiones_bancarias": ("shrimp_contabilidad.product_gasto_comisiones_bancarias",
                             "Comisiones bancarias", "GASTO-BANCO",
                             "gas_comisiones_bancarias", IVA_COMPRA_0),
}

# Diarios. clave -> dict(name, type, code, renombrables)
#   renombrables: nombres de fábrica (plantilla) que se pueden renombrar al
#   nombre de CamaronMarket. Un nombre puesto por la empresa no se toca.
DIARIOS = {
    "servicio": dict(name="Facturas de servicio", type="sale", code="SERV",
                     renombrables={"001-001 Facturas de cliente": "001-001 Facturas de servicio",
                                   "Sales": "Facturas de servicio",
                                   "Ventas": "Facturas de servicio"}),
    "banco": dict(name="Banco principal", type="bank", code="BNCP",
                  renombrables={"Bank": "Banco principal", "Banco": "Banco principal"}),
    "caja": dict(name="Caja chica", type="cash", code="CAJA",
                 renombrables={"Cash": "Caja chica", "Efectivo": "Caja chica",
                               "Caja": "Caja chica"}),
    "provisiones": dict(name="Provisiones y ajustes", type="general", code="PROV",
                        renombrables={}),
}

# Términos de pago (se reutilizan los de Odoo).
TERMINOS_PAGO = {
    "contado": "account.account_payment_term_immediate",
    "15_dias": "account.account_payment_term_15days",
    "30_dias": "account.account_payment_term_30days",
}
