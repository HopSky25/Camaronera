# CamaronMarket - Área contable (`shrimp_contabilidad`)

CamaronMarket **no compra ni vende camarón**: factura servicios. Este módulo pone
al área contable a trabajar sobre lo que la plataforma ya genera (los cobros
`shrimp.charge` y sus facturas) sin cambiar el flujo de facturación de
`shrimp_marketplace` ni el puente SRI `shrimp_l10n_ec`.

- Versión 19.0.1.0.0, LGPL-3.
- Depende de `account`, `l10n_ec`, `l10n_ec_sri_community`, `shrimp_marketplace` y `analytic`.
- No trae datos demo.

## Qué crea (idempotente)

Lo crea el `post_init_hook`, y también la carga del plan `ec` en una compañía
nueva, para cada compañía con plan contable `ec`. Lo que ya existe se
reutiliza (busca primero por xmlid, después por código de cuenta y por último
por nombre). Nunca duplica nada y nunca pisa una decisión de la empresa: solo
completa campos vacíos o que tengan el valor genérico de fábrica. Para volver
a correrlo:

```python
env["shrimp.contabilidad.setup"]._setup_all()
```

El resumen de lo creado y lo reutilizado queda en el log
(`shrimp_contabilidad: configuración contable de «…»`).

### Cuentas (plan Supercias de l10n_ec)

| Código | Nombre | Tipo | Origen |
|---|---|---|---|
| 41020101 | Comisiones por intermediación (marketplace) | Ingreso | nueva, hija de 410201 Prestación de servicios |
| 41020102 | Honorarios de verificación en campo | Ingreso | nueva |
| 41020103 | Comisiones de empaque y maquila | Ingreso | nueva |
| 41020104 | Suscripciones de empacadoras | Ingreso | nueva |
| 41020105 | Comisiones por ventas de larva y nauplio | Ingreso | nueva |
| 21030101 | Honorarios por pagar a verificadoras | Por pagar (conciliable) | nueva, hija de 210301 *Cuentas y documentos por pagar locales* (no relacionadas; las relacionadas son 2108) |
| 51040804 | Costo de honorarios de verificadoras | Costo directo | nueva, bajo 510408 *Otros costos de producción* |
| 52022819 | Servidores y dominios | Gasto | nueva, bajo 520228 *Otros gastos* (5202 Gastos administrativos) |
| 52022820 | Software y licencias | Gasto | nueva |
| 520211 | Promoción y publicidad | Gasto | **reutilizada** (5202 Gastos administrativos) |
| 520205 | Honorarios, comisiones y dietas a personas naturales | Gasto | **reutilizada** |
| 520302 | Comisiones (5203 Gastos financieros) | Gasto | **reutilizada** como comisiones bancarias |

En l10n_ec, 5201 es *Gastos de ventas*, 5202 *Gastos administrativos* y 5203
*Gastos financieros*. Además crea los grupos de cuentas 410201 y 210301 para
que las cuentas nuevas se vean bajo su cuenta madre.

### Productos de servicio

| Producto | Código | Cuenta | IVA | Línea analítica |
|---|---|---|---|---|
| Comisión Marketplace (de `shrimp_marketplace`, reutilizado) | COMISION-MKT | 41020101 | IVA 15 % servicios | Marketplace |
| Verificación de camarón en campo (de `shrimp_verification`, si está instalado) | VERIF-CAMPO | 41020102 (y costo 51040804 en la factura de la verificadora) | IVA 15 % servicios / IVA 15 % compras | Verificación |
| Comisión de la plataforma por empaque (de `shrimp_copacking`, si está instalado) | COMISION-EMPAQUE | 41020103 | IVA 15 % servicios | Empaque |
| Chequeo de producto (legado, solo si ya existe) | CHEQUEO-MKT | 41020102 | IVA 15 % servicios | Verificación |
| Suscripción mensual de empacadora | SUSC-EMPACADORA | 41020104 | IVA 15 % servicios | Suscripciones |
| Comisión marketplace – larva y nauplio | COMISION-LARVA | 41020105 | IVA 15 % servicios | Larva y nauplio |
| Servidores y dominios | GASTO-SERVIDORES | 52022819 | IVA 15 % compras (510, sustento 01) | – |
| Software y licencias | GASTO-SOFTWARE | 52022820 | IVA 15 % compras | – |
| Publicidad y marketing | GASTO-PUBLICIDAD | 520211 | IVA 15 % compras | – |
| Honorarios profesionales | GASTO-HONORARIOS | 520205 | IVA 15 % compras | – |
| Comisiones bancarias | GASTO-BANCO | 520302 | IVA 0 % (517, sustento 02) | – |

El IVA de venta es `tax_vat_15_411_services` (IVA 15 % de **servicios**), no el
de bienes que l10n_ec deja por defecto. Si la compañía todavía no tiene
facturas de cliente, el hook también lo pone como impuesto de venta por
defecto de la compañía.

Los productos de los cobros se obtienen con el propio
`shrimp.charge._get_service_product` (mismo xmlid), así que nunca hay dos.
Si un producto de cobro aparece más tarde (por ejemplo, si se instala
`shrimp_verification` después de este módulo), se configura solo la primera
vez que se factura.

### Cambios en los cobros

1. La **comisión de una venta de larva o nauplio** se factura con el producto
   *Comisión marketplace – larva y nauplio* (cuenta 41020105). El criterio es
   `shrimp.product._shrimp_tipo()`, el mismo de las pestañas del catálogo. El
   camarón sigue con *Comisión Marketplace* (41020101).
2. **Factura de la verificadora**: antes de crearla, si el socio tiene la
   cuenta por pagar por defecto, se le pone 21030101 *Honorarios por pagar a
   verificadoras*. Una cuenta elegida a mano no se toca.

### Contabilidad analítica

Plan **Línea de negocio** con las cuentas Marketplace (MKT), Verificación
(VER), Empaque (EMP), Suscripciones (SUS) y Larva y nauplio (LAR), más un
modelo de distribución por producto de ingreso. Así cada línea de factura (y
la factura de la verificadora) queda etiquetada, y el estado de resultados se
puede ver por línea en *Informes › Informe analítico*.

### Diarios y términos de pago

| Diario | Qué hace el hook |
|---|---|
| Facturas de servicio (venta, documentos SRI) | Usa el `shrimp_service_journal_id` de la compañía si está puesto. Si no, el diario de ventas de l10n_ec (001-001), que pasa a llamarse *001-001 Facturas de servicio* y queda como diario de servicio si ese campo estaba vacío. Solo si no hay ningún diario de ventas crea uno con la sugerencia 001-003. |
| Banco principal (banco) | Renombra el *Banco/Bank* de fábrica; si no existe, lo crea (BNCP). |
| Caja chica (efectivo) | La crea (CAJA). |
| Provisiones y ajustes (general) | Lo crea (PROV). |

Solo se renombran los diarios que conservan el nombre de fábrica. Términos de
pago: se reutilizan los de Odoo (Pago inmediato = contado, 15 días y 30 días).

## Grupos

Categoría **Contabilidad CamaronMarket**:

- **Asistente contable**: implica `account.group_account_user` (asientos,
  conciliación, informes), la contabilidad analítica y la creación de
  contactos. Tiene lectura de `shrimp.charge`, `shrimp.transaction`,
  `shrimp.product`, `shrimp.uom` y `shrimp.stage`, y también de
  `shrimp.verification` y `shrimp.copack.order` si esos módulos están
  instalados (se otorga por código en cada `-u`). Puede usar el generador
  SRI de ATS y de los auxiliares 103 y 104. **No** modifica impuestos, plan de
  cuentas ni diarios.
- **Contador general**: lo del asistente más `account.group_account_manager`
  (configuración contable y puntos de emisión SRI).

Ninguno de los dos implica `base.group_system`, `base.group_erp_manager` ni
los grupos de administración de la plataforma (`shrimp_*` manager). El módulo
SRI no tiene grupos propios: usa los de `account`.

## Menú «Contabilidad»

Es un menú de nivel superior que abre el tablero de diarios. Contiene:
Clientes, Proveedores (con las retenciones emitidas), Bancos (extractos y
apuntes por conciliar), Cobros de la plataforma (solo lectura, filtros por
estado y tipo), Asientos y apuntes, Informes, SRI (comprobantes, retenciones,
ATS/103/104, puntos de emisión) y Configuración.

**Informes** es el menú nativo `account.menu_finance_reports`, que se cuelga
aquí para no duplicarlo. Es donde `shrimp_estados_financieros` pone
*Estados financieros*. Al desinstalar este módulo, el `uninstall_hook` lo
devuelve a Facturación.

## Usuario contable

```bash
cd /home/ccarballo/odoo19
venv/bin/python src/odoo-bin shell -c odoo.conf -d <BASE> --no-http \
    < custom_addons/Camaronera/shrimp/shrimp_contabilidad/scripts/crear_usuario_contable.py
```

Crea `contabilidad@camaronmarket.com` (*Contabilidad CamaronMarket*) con el
grupo Contador general, **sin contraseña** y sin enviar invitación. Al final
imprime el comando para ponerle la contraseña.

## Pruebas

```bash
cd /home/ccarballo/odoo19 && venv/bin/python src/odoo-bin -c odoo.conf -d cont_test_$(date +%s) \
  -i shrimp_contabilidad --test-enable --test-tags /shrimp_contabilidad --stop-after-init \
  --without-demo --http-port=8093 --gevent-port=8094 --max-cron-threads=0
```

Los tests crean su propia compañía ecuatoriana con el plan `ec`. Comprueban:

- que el hook es idempotente;
- los diarios;
- las cuentas e IVA de los productos;
- que el contador ve los asientos y factura un servicio con 41020101, IVA 15 % de servicios y la distribución analítica;
- que el asistente no puede tocar los impuestos;
- que los cobros son de solo lectura;
- que ninguno de los dos es administrador;
- que la comisión de larva va a su propio producto;
- la cuenta por pagar de la verificadora;
- que el menú de Informes se reutiliza.

## Supuestos por confirmar con la empresa

- **Principal o agente en la verificación (NIIF 15).** Hoy el honorario se factura completo como ingreso (41020102) y lo que se paga a la verificadora va como costo (51040804). Si la empresa es agente, solo el margen es ingreso.
- **IVA 0 % en las comisiones bancarias.** Se tomó el art. 56 de la LRTI (servicios financieros). Hay que revisar cada cargo.
- **IVA del software y los servidores.** Si el proveedor es del exterior, corresponde el IVA de servicios digitales (510, sustento 15).
- **Cuentas de publicidad y honorarios.** La publicidad podría ir en 520111 (gastos de ventas). Los honorarios a sociedades podrían ir en 52022815 (servicios contratados a terceros).
- **Suscripciones de empacadoras.** Hoy la plataforma no tiene un cobro automático de suscripción: el producto está listo para facturar a mano.
