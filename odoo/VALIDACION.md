# Verificación de la entrega

Fecha: 11 de septiembre de 2026.

## Ejecutado

- 30 pruebas independientes de Odoo: aprobadas.
- Los seis generadores nativos pasan los XSD oficiales con datos de prueba.
- Todas las versiones de XSD declaradas por el módulo compilan localmente.
- Firma XAdES con certificado RSA efímero: integridad de las tres referencias y verificación criptográfica; manipulación del documento detectada.
- Clave de acceso, consistencia de RUC/ambiente/fecha, valores monetarios, IVA mixto, notas y límite de consumidor final.
- Bloqueo de DTD/entidades externas.
- SOAP simulado: recepción, rechazo, clave registrada, autorización pendiente, XML autorizado, timeout, HTTP y respuestas inesperadas.
- ATS: XSD, agrupación de ventas con código 18 y notas de crédito.
- Sintaxis Python, XML de vistas, rutas del manifest y existencia de métodos de botones.

Comando ejecutado: `python -m pytest tests -q --tb=short`.

## Incluido pero no ejecutado

Seis pruebas ORM en `l10n_ec_sri_community/tests/test_document.py`: requieren una base Odoo 19. Cubren preparación, bloqueo de campos, corrección, fallos de conexión, estados pendientes y certificado inválido.

## No verificado en este entorno

- Instalación/actualización del addon en un registro Odoo 19 real.
- Vistas y permisos ejecutados dentro de Odoo.
- Concurrencia entre workers y transacciones reales PostgreSQL.
- Asiento y conciliación reales de retenciones.
- Firma con certificado del contribuyente y aceptación de cada XML en SRI.
- Renderizado PDF del RIDE.
- Validación funcional del ATS con DIMM/validador vigente del SRI.
- Cobertura contable/fiscal de una empresa o régimen concreto.

Las pruebas aprobadas son verificaciones del código y de estructura, no una homologación con el SRI. Los formularios 103/104 se entregan como auxiliares por casilleros; los casos sectoriales y declaraciones completas no están implementados.
