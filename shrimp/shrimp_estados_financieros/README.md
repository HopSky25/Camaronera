# CamaronMarket - Estados financieros

Odoo 19 Community no trae informes contables (son de Enterprise). Este módulo
agrega **Contabilidad / Facturación > Informes > Estados financieros**: un
asistente que genera cada informe en **PDF** (QWeb) y en **Excel** (xlsxwriter,
una hoja por informe, encabezado congelado, montos `#,##0.00`).

Lo ven los usuarios con *lectura contable* (`account.group_account_readonly`,
incluido en "Contable") o *administrador de contabilidad*.

## Informes

| Informe | Fechas | Qué muestra |
|---|---|---|
| Balance de comprobación | desde / hasta | Por cuenta (o nivel): saldo inicial, débitos, créditos, saldo final; totales y diferencia si débitos ≠ créditos |
| Libro mayor | desde / hasta | Por cuenta: saldo inicial, cada movimiento (fecha, asiento, diario, contacto, referencia, débito, crédito, saldo acumulado) y saldo final. Filtro opcional de cuentas y de contacto |
| Libro diario | desde / hasta | Asientos del período con sus líneas, por fecha |
| Estado de situación financiera | al "hasta" | Activo (1), Pasivo (2), Patrimonio (3) por niveles, con el resultado del ejercicio dentro del patrimonio y la comprobación Activo = Pasivo + Patrimonio |
| Estado de resultados integral | desde / hasta | Ingresos (4), Costos y gastos (5), clase 6 si existe, y la utilidad (pérdida) |
| Cartera por antigüedad | al "hasta" | Por cobrar y por pagar por contacto: no vencido, 1-30, 31-60, 61-90, más de 90 días, total |

Opciones: solo asientos publicados (sí por defecto), diarios, nivel de detalle,
mostrar cuentas sin saldo y comparar con el mismo período del año anterior
(situación financiera y resultados).

## Cómo se calcula

* **Fuente**: `account.move.line` de la compañía activa (y sus sucursales
  activas), estado publicado (o publicado + borrador), diarios elegidos.
  Saldos con `_read_group` agrupado por cuenta: una consulta por período,
  sin N+1.
* **Clase** = primer dígito del código de la cuenta (código de la compañía,
  campo `code`). No se usa el tipo de cuenta de Odoo: en el plan `ec`, por
  ejemplo, la 110310 *Variación de inventarios* es de tipo gasto pero está en
  la clase 1 y aquí se presenta en el Activo, como dice su código.
* **Signos**: clases 1, 5 y 6 de naturaleza deudora (débito − crédito); 2, 3
  y 4 acreedora (crédito − débito). Cada sección sale en positivo cuando la
  cuenta tiene su saldo normal; una cuenta correctora (depreciación
  acumulada) sale en negativo y resta. En el balance de comprobación y en el
  mayor el saldo va con signo contable: positivo = deudor, negativo =
  acreedor.
* **Niveles**: no hay cuentas "vista"; los niveles superiores se arman
  sumando las cuentas por prefijo del código. Si el plan tiene grupos de
  cuentas (`account.group`, el plan `ec` de Odoo los trae: 1 / 11 / 1101 /
  110101 / 11010101), los niveles son esos prefijos y sus nombres. Si no hay
  grupos, se usa el patrón de la Superintendencia (1, 3, 5, 7, 9 dígitos). El
  nivel de detalle ofrece las longitudes que existen en el plan, más "Todas
  las cuentas".
* **Resultado del ejercicio** (situación financiera): Ingresos (4) − Costos y
  gastos (5) − clase 6, desde el inicio del ejercicio fiscal de la compañía
  hasta la fecha. Lo de ejercicios anteriores que no tuvo asiento de cierre va
  en otra línea, "Resultados de ejercicios anteriores no cerrados". Así el
  balance cuadra con o sin asiento de cierre. Si no cuadra (asientos
  descuadrados o cuentas fuera de las clases 1-6) se muestra la diferencia.
* **Saldo inicial** (comprobación y mayor): todo lo contabilizado antes de la
  fecha desde, también en las cuentas 4, 5 y 6: si no se hizo el asiento de
  cierre del año anterior, esas cuentas arrastran saldo inicial.
* **Cartera**: líneas de cuentas por cobrar (`asset_receivable`) y por pagar
  (`liability_payable`) con fecha hasta la del informe y saldo pendiente *a
  esa fecha*: el saldo de la línea menos las conciliaciones (parciales)
  hechas hasta esa fecha. Un cobro posterior no reduce la cartera de una fecha
  anterior. Días vencidos = fecha del informe − vencimiento (o fecha contable
  si no tiene vencimiento). Por pagar se presenta en positivo; un valor
  negativo es un anticipo o saldo a favor.

## Limitaciones

* Solo moneda de la compañía (USD); no hay columnas en moneda extranjera.
* No hay asiento de cierre automático ni "ganancias no distribuidas"
  automáticas: el contador hace el cierre a mano. Un asiento de cierre
  fechado el último día del año deja en cero el resultado de ese año si se
  consulta a esa fecha.
* El estado de resultados integral no separa los "otros resultados
  integrales" (en el plan `ec` están en el patrimonio, 35xx).
* No hay flujo de efectivo ni estado de cambios en el patrimonio.
* El libro mayor y el diario de un año completo pueden dar PDFs largos; para
  volúmenes grandes conviene el Excel.
* Las cuentas de clases distintas de 1 a 6 (cuentas de orden) solo aparecen
  en el balance de comprobación, el mayor y el diario; si tienen saldo, el
  balance general lo avisa en una nota.
