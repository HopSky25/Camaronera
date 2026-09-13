# Ecuador SRI para Odoo 19 Community

Código de un módulo independiente de Enterprise. Nombre técnico: `l10n_ec_sri_community`.

## Estado de la entrega

Se han ejecutado pruebas del núcleo Python, XSD oficiales, firma criptográfica, SOAP simulado y estructura del paquete. **No se ha instalado en Odoo ni se ha obtenido una autorización real del SRI en esta entrega.** Las pruebas ORM incluidas se ejecutan cuando exista una base Odoo 19 de pruebas. No se debe interpretar esta entrega como certificación fiscal ni como cobertura de todos los regímenes ecuatorianos.

## Funciones implementadas

| Función | Implementación |
|---|---|
| Facturas, notas de crédito/débito y liquidaciones | Generación desde `account.move` publicado, con numeración de la localización Community |
| Retenciones 2.0.0 | Desde una factura nacional de proveedor; códigos/bases/porcentajes explícitos; contabilización y conciliación opcional después de autorización de producción |
| Guías 1.1.0 | Desde una transferencia preparada o realizada; transportista, placa, fechas, ruta y destinatario |
| XML externo | Importación sin firma de los seis tipos admitidos, con validación XSD e identidad; útil para estructuras avanzadas elaboradas por otro sistema |
| Firma | XAdES-BES 1.3.2, PKCS#12 RSA, tres referencias firmadas; verificación de integridad local |
| SRI | Recepción y autorización SOAP offline en pruebas/producción, TLS validado, reintentos con espera creciente |
| Trazabilidad | XML generado/firmado/autorizado, SHA256, respuestas y registro de correcciones; clave y secuencial conservados |
| RIDE | PDF QWeb del XML autorizado, con clave de barras, detalle fiscal y marca de pruebas/anulación |
| ATS | ZIP `ATmmaaaa.zip` con XML mensual para operaciones nacionales ordinarias en USD, validado contra XSD, con compras, ventas agrupadas, retenciones y anulados |
| Reportes CSV | Libros de compras/ventas, retenciones emitidas, estados SRI, balance de comprobación, auxiliares por casilleros 103 y 104 |
| Seguridad | Reglas por empresa, historial de solo lectura, bloqueo de campos fiscales, secretos fuera de la base de datos |

## Instalación en el servidor de Odoo

1. Copiar únicamente la carpeta `l10n_ec_sri_community` a un directorio incluido en `addons_path`.
2. Instalar `requirements.txt` con el Python del servicio Odoo. No instalar otra versión de Odoo con pip.
3. Reiniciar Odoo, activar modo desarrollador, actualizar lista de aplicaciones y buscar **Ecuador SRI - Community**.
4. Instalar. Dependencias Community: `l10n_ec`, `account_debit_note`, `stock`, `mail`.
5. En una base vacía, configurar Ecuador como país y cargar la localización contable ecuatoriana. En una base existente, revisar el plan de cuentas antes de cambiar la localización.

Ejemplo Linux:

```bash
/opt/odoo/venv/bin/python -m pip install -r /ruta/paquete/requirements.txt
/opt/odoo/venv/bin/python /opt/odoo/odoo/odoo-bin -c /etc/odoo.conf -d BASE_PRUEBAS -i l10n_ec_sri_community --stop-after-init
```

No hay dependencias de `l10n_ec_edi`, `account_reports` ni otros módulos Enterprise.

## Configuración

1. **Empresa → SRI Community:** habilitar, RUC, dirección matriz, ambiente de pruebas, régimen, contabilidad, resolución de agente/contribuyente especial cuando corresponda. Configurar RUC del proveedor del sistema y gran contribuyente cuando apliquen.
2. **Certificado:** definir en el servicio las variables de `config/sri.env.example`; el administrador técnico escribe el prefijo `EC_SRI_EMPRESA1` en la empresa. El archivo debe ser legible solo por el usuario del servicio. Reiniciar tras cambiar variables. Cada empresa utiliza su propio prefijo y certificado. La correspondencia entre titular del certificado y emisor debe verificarse durante homologación; no se infiere del CN.
3. **Contactos:** indicar tipo de identificación SRI, identificación y parte relacionada. Para consumidor final se utiliza `9999999999999`.
4. **Impuestos:** indicar código XML (`2` IVA, `3` ICE, `5` IRBPNR), código de porcentaje del catálogo y clasificación ATS. Incluso IVA cero/no objeto/exento requiere impuesto explícito. No se deduce un código fiscal por el nombre del impuesto. Revisar tarifas vigentes y fechas de aplicación con el responsable contable.
5. **Puntos de emisión:** crear uno por empresa, establecimiento, punto, documento y ambiente. Se crea una secuencia automáticamente. Ajustar el siguiente número para guías/retenciones según el historial real. Facturas y notas usan el número del diario contable, no una segunda secuencia.
6. **Diarios contables:** activar documentos latinoamericanos, configurar establecimiento/punto y seleccionar el tipo de documento correcto. El número debe ser `001-001-000000001` (puede tener prefijo de Odoo).
7. **Cron:** mantener activa la acción `Ecuador SRI: enviar y consultar comprobantes`.

## Flujo

### Facturas, notas y liquidaciones

Publicar el movimiento → **Comprobante SRI** → revisar punto y datos → **Validar, firmar y encolar**. El cron enviará y consultará la autorización. Un estado RECIBIDA no es autorización. Cuando esté AUTORIZADO se habilitan XML autorizado y RIDE. Las notas requieren factura original y motivo. No reenviar como propio el XML de una factura de proveedor: para esas compras corresponde registrar el documento recibido y, si aplica, emitir retención.

### Retenciones

En factura de proveedor, registrar autorización, forma de pago y código de sustento → **Crear retención SRI** → completar códigos, bases y porcentajes → firmar/encolar. En cada línea configurar cuenta de pasivo y, si se usa auxiliar 103, casilleros base/valor. Después de autorizar en producción, un administrador contable puede **Contabilizar retención**: debita cuentas por pagar, acredita las cuentas tributarias y concilia con la factura. El botón es idempotente. No crear ese asiento si la retención ya se contabilizó por otra vía. La anulación SRI no revierte automáticamente este asiento.

### Guías

Completar la pestaña Transporte SRI en una transferencia preparada o realizada → **Guía SRI**. La fecha del documento corresponde al inicio del transporte. En preparada se usan cantidades previstas; en realizada, cantidades efectivas. Verificar cantidades antes de enviar. Para múltiples destinatarios u otros casos avanzados, usar XML externo completo.

### Errores

- DEVUELTA / NO AUTORIZADO: corregir con el botón **Corregir**, conservando clave y secuencial. Se conserva el XML previo en historial.
- Timeout / error de red: no crear otro documento. El sistema consulta antes de reenviar.
- RECIBIDA / clave registrada: consultar autorización; no asumir que ya fue autorizada.
- 20 intentos agotados: queda en revisión; **Consultar autorización** o **Reanudar**. No se descartan archivos.
- Anulación: realizarla por el procedimiento del SRI y luego registrar motivo, fecha y constancia. No hay servicio de anulación automática en este módulo.

## Reportes y alcance fiscal

**103 y 104 son auxiliares CSV por casilleros configurados, no declaraciones completas ni presentadas.** No calculan automáticamente arrastres de crédito tributario, saldos anteriores, intereses, multas, compensaciones ni ajustes particulares. La localización Community no aporta el motor de reportes Enterprise. Los auxiliares requieren conciliación contable.

**ATS nativo:** un mes calendario completo, USD, operaciones nacionales ordinarias, documentos contables registrados y ventas autorizadas en producción. El generador bloquea la salida si detecta datos faltantes o casos no cubiertos; no omite silenciosamente facturas con errores. Las retenciones recibidas se ingresan en campos de la factura; esos campos no crean asientos. Revise que los movimientos contables del periodo coincidan con las obligaciones del anexo.

Cada factura requiere seleccionar **Tratamiento ATS**. La ficha técnica prevé exclusiones para determinados comprobantes electrónicos: el responsable contable debe seleccionar y sustentar esa exclusión, no basta con tener una clave de acceso. El sistema exige autorización electrónica para excluir y no decide por sí mismo si se cumplen las condiciones fiscales adicionales. La clasificación se conserva en la factura. Las retenciones recibidas electrónicas no deben duplicarse en los campos de retenciones recibidas destinados al anexo cuando aplique la exclusión. Las ventas ordinarias incluidas se agrupan con código ATS 18; las notas conservan 04/05.

No se generan de manera nativa importaciones/exportaciones, reembolsos, dividendos, fideicomisos, ATS semestral, anexos de nómina RDEP, impuesto a la renta 101/102, ICE sectorial, ISD, devolución automática de IVA DIG, subsidios, facturas negociables ni documentos de transporte comercial con requisitos especiales adicionales. El soporte XML externo permite firmar/transmitir estructuras válidas, pero no calcula sus datos fiscales ni transforma esos casos en un ATS completo.

No se incluye descarga masiva desde SRI, lectura automática de comprobantes recibidos, envío de correo, integración específica POS/eCommerce, ni presentación automática de declaraciones. Las facturas contables originadas en esos canales se pueden tramitar por el flujo descrito.

## Pruebas

Sin Odoo:

```bash
python -m pip install -r requirements-test.txt
python -m pytest tests -q
```

En una base Odoo 19 separada (no producción):

```bash
odoo-bin -c /etc/odoo.conf -d BASE_PRUEBAS -i l10n_ec_sri_community --test-enable --test-tags /l10n_ec_sri_community --stop-after-init
```

Las pruebas locales utilizan certificados efímeros de prueba y respuestas SOAP simuladas, sin enviar datos al SRI. Revisar `VALIDACION.md` para el estado de verificación.

## Verificación pendiente al instalar

1. Instalar en base nueva y en copia de una base Community 19 existente; ejecutar las pruebas ORM.
2. Comprobar permisos de facturación/administrador, dos empresas y dos puntos de emisión.
3. Autorizar un ejemplo real de cada uno de los seis tipos en **pruebas SRI** con el certificado del emisor; revisar rechazo, corrección, timeout y reinicio del cron.
4. Conciliar impuestos, redondeos, retenciones y asientos con documentos de referencia del contador.
5. Renderizar RIDE en el servidor con wkhtmltopdf y verificar legibilidad, saltos de página y contenido en los seis tipos. La plantilla se entrega sin renderizar porque aquí no existe Odoo.
6. Validar el ATS en el software vigente del SRI y conciliar las inclusiones/exclusiones; revisar los auxiliares 103/104.
7. Solo después, configurar producción y los secuenciales reales.

La consulta SRI y firma local están implementadas; no se verificó revocación o cadena de confianza del certificado localmente. La verificación criptográfica no sustituye la autorización SRI. La conservación a largo plazo depende de respaldos y retención de la base de datos administrados por el usuario.

## Fuentes técnicas

- SRI, ficha técnica offline 2.34 (julio 2026), disponible en https://www.sri.gob.ec/facturacion-electronica
- XSD oficiales descargados del mismo portal el 11/09/2026; listado de URL y hashes en `l10n_ec_sri_community/schemas/SOURCES.json`.
- ATS: https://www.sri.gob.ec/formularios-e-instructivos1 y https://descargas.sri.gob.ec/download/anexos/ats/ats.xsd
- Código Community de referencia: https://github.com/odoo/odoo/tree/19.0/addons/l10n_ec
- Perfil XMLDSig: https://www.w3.org/TR/2002/REC-xmldsig-core-20020212/

La ficha técnica y los catálogos se actualizan independientemente de los XSD. Validar un XSD no certifica que un caso cumpla todas las reglas fiscales ni que el SRI lo autorice.

## Licencia

Código propio LGPL-3.0-or-later; ver `LICENSE`. Los esquemas SRI y W3C se conservan como recursos de sus respectivos autores. Consultar `THIRD_PARTY_NOTICES.md`.
