# Ecuador SRI — Community

Comprobantes electrónicos del SRI para Odoo 19 Community: factura, liquidación
de compra, nota de crédito, nota de débito, guía de remisión y comprobante de
retención. Firma XAdES-BES, RIDE, ATS y auxiliares 103 / 104.

## Independiente a propósito

Este módulo no depende de nada del marketplace de camarón ni al revés: cero
referencias cruzadas en las dos direcciones. Depende solo de Odoo estándar
(`l10n_ec`, `account_debit_note`, `stock`, `mail`), así que se puede instalar
en cualquier base ecuatoriana, y se puede distribuir por separado.

## Instalación

    ./venv/bin/python src/odoo-bin -c odoo.conf -d TU_BASE --stop-after-init \
        -i l10n_ec_sri_community

Odoo arrastra solo las dependencias.

**Si la base tiene datos de demo activados** (`without_demo = False` en
`odoo.conf`), antes de instalar conviene apagarlos, o entrarán las facturas,
cuentas e impuestos de ejemplo de los módulos de contabilidad y de stock —en
una prueba fueron 28 facturas y 506 cuentas de más—:

    psql -d TU_BASE -c "update ir_module_module set demo = false"

Esa línea solo marca que no se cargue demo de aquí en adelante; no borra nada
de lo que ya existe.

Requisitos de Python: `lxml`, `cryptography`, `requests`.

## Qué se configura solo

Al instalar corre `post_init_hook`, que es conservador —solo rellena campos
vacíos y es idempotente— y hace tres cosas:

1. **Anota el código del catálogo del SRI en cada IVA** identificable por su
   tarifa: 15 % → `codigoPorcentaje` 4, 0 % → 0, y también las tarifas
   históricas (12 %, 14 %) porque una base con histórico las sigue teniendo.
   Sin esto el XML sale sin la sección de impuestos y el SRI lo rechaza.
2. **Habilita las compañías de Ecuador en ambiente de PRUEBAS.** Nunca en
   producción: eso exige pasar la homologación y marcarlo a mano.
3. **Deduce el tipo de identificación de los contactos** por la longitud del
   número: 13 dígitos es RUC, 10 es cédula. Un pasaporte no se puede deducir,
   así que esos se dejan vacíos a propósito.

Un impuesto con una tarifa que no está en el catálogo se deja en blanco para
que alguien lo mire. Es mejor que asignarle un código inventado.

## Qué hay que completar a mano

Son datos de identidad fiscal: inventarlos sería peor que dejarlos en blanco,
porque acabarían dentro de un comprobante electrónico. Van en
`scripts/configurar_sri.py`, que es idempotente y dice qué falta:

    # 1) rellenar DATOS dentro del script
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d TU_BASE --no-http \
        < custom_addons/Camaronera/l10n_ec_sri_community/scripts/configurar_sri.py

Lo que pide:

| Dato | Dónde | Por qué |
|---|---|---|
| RUC de 13 dígitos | Compañía | Va en cada comprobante |
| Dirección matriz | Compañía | Obligatoria en el XML |
| Punto de emisión | `ec.sri.point` | Uno por tipo de documento (001-001) |
| Certificado `.p12` | Variables de entorno | Firma XAdES-BES |

El módulo no emite hasta tenerlos: `_ec_sri_company_data()` lo valida.

## El certificado no se guarda en la base

Se lee de variables de entorno del servidor, con el prefijo que se configure
en la compañía (`EC_SRI_...`):

    export EC_SRI_TRAZUL_P12_PATH=/ruta/segura/firma.p12
    export EC_SRI_TRAZUL_P12_PASSWORD='...'

Es deliberado: un `.p12` guardado en una tabla se acaba filtrando en un
respaldo. El campo del prefijo además está restringido a `base.group_system`.

## Pruebas antes de producción

1. Emitir en ambiente de **Pruebas** (`ec_sri_environment = '1'`) contra el
   servicio de certificación del SRI.
2. Superar la homologación.
3. Marcar *Pruebas de homologación completadas* en la compañía y cambiar el
   ambiente a Producción.

Mientras el ambiente sea Pruebas, las pantallas muestran el aviso
**AMBIENTE DE PRUEBAS — SIN VALIDEZ TRIBUTARIA**.

## Pruebas automatizadas

    ./venv/bin/python src/odoo-bin -c odoo.conf -d TU_BASE --stop-after-init \
        --test-enable --test-tags /l10n_ec_sri_community -u l10n_ec_sri_community

Cubren lo que más duele en una integración fiscal: que un timeout no se
confunda con un rechazo, que la clave de acceso no cambie al corregir, que un
documento en espera de autorización no se reenvíe y que un `.p12` inválido
revierta la preparación sin dejar el comprobante a medias.

## Alcance del ATS

El ATS que genera cubre operaciones nacionales ordinarias en USD.
Importaciones, exportaciones, reembolsos y regímenes sectoriales hay que
completarlos con la información específica. El archivo no se presenta
automáticamente al SRI.
