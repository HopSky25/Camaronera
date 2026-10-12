# Alcance de la app CamaronMarket por perfil

Una sola app para todos los perfiles de la plataforma. Cada cosa que se ve o se hace en la app está
respaldada por un endpoint real de `shrimp_api` (`/api/v1`). Lo que la API no permite (comprar sin
verificación, registrar socios, subir fotos o archivos, publicar listas de precios, gestionar técnicos...)
NO va en la app: en su lugar se muestra el aviso "Esto se hace desde el portal web".

## Entrada y perfiles

| Paso | Qué hace la app | Endpoint |
|---|---|---|
| Entrar por cuenta | Servidor (por defecto `https://demo.camaronmarket.com`), correo y contraseña. La clave queda en memoria hasta "Protege tu cuenta" (ver **Seguridad del teléfono**); `me` va a SQLite para abrir sin señal. `device` = marca y modelo del teléfono. | `POST /auth/login {"login","password","device"}` → `{api_key, expires_at, me, device_required}` |
| Protege tu cuenta | Obligatoria tras entrar. Ver **Seguridad del teléfono**. | `POST /me/devices` |
| ~~Entrar con clave de API~~ | Retirado: las claves creadas a mano en el portal no son de app (`409 not-an-app-key`) y no se pueden ligar al teléfono. Si una sesión así llega a "Protege tu cuenta", la app pide entrar con correo y contraseña. | — |
| Elegir perfil | Lee `me.roles`. Un perfil aprobado → entra directo. Dos o más → pantalla "¿Con qué perfil quieres trabajar?" (los pendientes se ven deshabilitados). El técnico de campo (`is_field_technician` y `company`) entra directo al flujo del técnico. | `me.roles`, `me.active_role` |
| "Actuar como" | El perfil elegido se guarda en el teléfono y va en la cabecera `X-Shrimp-Role` de **cada** petición. No se llama a `POST /me/active-role` (cambiaría los menús del portal de la misma cuenta). Valor inicial: `me.active_role`. | cabecera `X-Shrimp-Role` |
| Cambiar de perfil | Desde el menú de la cuenta o el botón "Cambiar" de la cabecera. | local |
| Mis datos y privacidad | Abre `https://<servidor>/my/datos` en el navegador. | portal |
| Cerrar sesión | Revoca la clave (y con ella el dispositivo) y borra todo lo guardado en el teléfono, también las claves del Keystore. Desde la pantalla de desbloqueo no se puede revocar en el servidor (la clave está cifrada): solo se borra en el teléfono. | `POST /auth/logout` |
| 401 (clave caducada o revocada) | Vuelve a la pantalla de entrada con el motivo; NO se borra lo capturado (la cola se envía al volver a entrar). | — |
| 403 | Mensaje claro ("Tu clave no tiene permiso...", o el `detail` del servidor); en la cola queda a la vista para corregir o descartar. | — |

## Seguridad del teléfono

Contrato: `odoo19/docs/app/CONTRATO-FIRMA-DISPOSITIVO.md` (versión 1.1).

**Claves del teléfono.** Canal nativo propio (`android/app/src/main/kotlin/.../SeguridadDispositivo.kt`,
canal `com.camaronmarket/seguridad`, `MainActivity` es `FlutterFragmentActivity`). En el Android Keystore,
no exportables y solo utilizables tras autenticar a la persona:

* par **ECDSA P-256** (ES256) para firmar: `SHA256withECDSA` → firma DER (ASN.1) en base64; la clave pública
  sale como SubjectPublicKeyInfo DER en base64. Dos ranuras (`_a`/`_b`) para poder sustituir el dispositivo.
* clave **AES-256-GCM** que cifra la clave de API `trz_…` (se guarda `base64(iv ‖ cifrado)` en
  flutter_secure_storage; la clave legible nunca se guarda).
* Android 11+ (API 30): cada uso exige su propia autenticación ligada a la operación (`BiometricPrompt` con
  `CryptoObject`), huella fuerte **o** PIN/patrón del teléfono (para guantes o manos mojadas).
  Android 7-10 (API 24-29): Android no admite PIN con `CryptoObject`; la clave queda autorizada 10 s tras
  autenticar (huella con `BiometricPrompt` o PIN con la pantalla del sistema) y la operación se hace en el acto.
* Añadir una huella o quitar el bloqueo invalida las claves (`setInvalidatedByBiometricEnrollment`): la app
  vuelve a la entrada ("vuelve a entrar con tu correo y contraseña").
* `minSdk` sigue en 24 (el de Flutter); permisos `USE_BIOMETRIC` y `USE_FINGERPRINT` (≤ API 27); tema
  AppCompat (lo pide el diálogo de huella en Android 7-8). Dependencias: `androidx.biometric:1.1.0`,
  `androidx.appcompat:1.7.0`.
* Se descartó `biometric_signature` (v13): no documenta que la firma EC sea DER ni que la pública sea SPKI, y
  no cifra datos con AES ligada a la huella (haría falta otro paquete para la clave de API).

**Flujo de pantallas.**

| Momento | Pantalla | Qué pasa |
|---|---|---|
| Tras entrar (o al actualizar desde la versión anterior, que tenía la clave legible) | **Protege tu cuenta** | Comprueba bloqueo de pantalla (sin él no se sigue: explica cómo activarlo y abre Ajustes). Crea las claves, registra el teléfono (`POST /me/devices`, primer registro SIN firma, `name` = marca y modelo) y cifra la clave de API (una huella o "Usar PIN del teléfono"). Sin señal, el registro queda pendiente y se completa al sincronizar; se puede firmar igual (la firma no incluye el `device_id`). `409 not-an-app-key` → "Entrar con correo y contraseña" (no pide huella; no revoca la clave). |
| Al abrir la app y al volver tras más de 2 min en segundo plano | **Desbloqueo** | Tapa toda la app (sin cerrar lo que estaba abierto): huella o "Usar PIN del teléfono". Desbloquear = descifrar la clave de API. Funciona sin señal. |
| Al confirmar una operación sensible | diálogo de huella del sistema | Se serializa el cuerpo UNA vez, se firma la cadena canónica (`METODO\nRUTA(/api/v1/…)\nTIMESTAMP\nIDEMPOTENCY-KEY\nsha256hex(cuerpo)`) y se guarda en la cola con el cuerpo serializado, la firma, el timestamp y el dispositivo. Al enviar se mandan esos bytes y las cabeceras `X-Device-Id`, `X-Signature-Timestamp`, `X-Signature`. Si no se confirma, no se guarda nada. |
| Menú de la cuenta → **Seguridad** | Seguridad | Nombre del teléfono, estado, fecha de registro y último uso (`GET /me/devices`), "Registrar de nuevo este teléfono" y la explicación de que la huella nunca sale del teléfono. |
| Pendientes | Pendientes | Lo que necesita huella (operaciones de la versión anterior sin firma, firmas de más de 7 días, firmas con un par anterior, o rechazadas por `signature-expired` / `invalid-signature` / `signature-required`) NO se envía y muestra "Confirmar con huella" ("Confirmar todos" si hay varias). Se vuelve a firmar el mismo cuerpo con la misma `Idempotency-Key` y la hora actual. |

**Registrar de nuevo este teléfono (contrato 1.1).** Se crea el par nuevo en la otra ranura; el par
VIEJO firma el `POST /me/devices` (huella) con el `X-Device-Id` actual; solo tras el 201 se activa el nuevo y se
borra el viejo. Si se cancela o no hay señal, no cambia nada. Si el par viejo ya no existe, la única vía es
cerrar sesión y volver a entrar (el login emite una clave nueva, sin dispositivo).

**Errores del contrato.**

| Error | Qué hace la app |
|---|---|
| `403 device-revoked` | Borra la clave cifrada, el dispositivo y el par de firma; vuelve a la entrada con "Este teléfono fue desconectado de tu cuenta…". Lo capturado se conserva (como con un 401). |
| `403 device-not-registered` | Vuelve a "Protege tu cuenta" (solo registro, sin huella); lo ya firmado sigue valiendo con el nuevo `device_id`. Si el servidor responde `signature-required` al registrar (la clave ya tiene otro teléfono), cierra la clave y pide entrar de nuevo. |
| `403 signature-expired` / `invalid-signature` / `signature-required` | La operación queda en Pendientes con "Confirmar con huella"; no se reintenta sola. |
| `409 signature-replayed` | Mensaje "Esta confirmación ya se usó…": descartar y volver a hacer. |
| Clave del Keystore invalidada | Al desbloquear o al firmar: vuelta a la entrada (al firmar, además se revoca la clave en el servidor con `/auth/logout`). |

**Lo que no se puede verificar sin un teléfono real.** Todo lo nativo: el diálogo de huella y de PIN, que
la clave se cree en hardware y no sea exportable, la invalidación al añadir una huella, el camino de Android
7-10 (ventana de 10 s y pantalla de PIN del sistema) y que el servidor real acepte una firma hecha por el
Keystore. Las pruebas usan un firmador falso con pointycastle que produce ES256 real (DER + SPKI) y un servidor
falso que verifica como `shrimp_api`.

## Datos y red

* Técnico y verificadora: bandeja local en SQLite (`verificaciones`), funciona sin internet.
* Camaronera / semillero / laboratorio / empacadora / maquilador: trabajan en línea; la última respuesta de
  cada lista y detalle queda en SQLite (`cache_listas`) y se muestra sin señal con el aviso
  "Datos de la última sincronización".
* Todas las acciones (aceptar informe, programar despacho, ofertar, comprar...) van por la misma cola
  (`cola`) con `Idempotency-Key` (UUID v4) y se envían en orden cuando hay señal. Todas son rutas firmadas:
  la cola guarda el cuerpo ya serializado y la firma (esquema 4 de la base local; las filas antiguas se
  conservan y quedan "por confirmar").
* Paginación con `limit` (100) y `cursor` de la API ("Cargar más").

## Tabla por perfil

| Perfil | Qué ve en el panel | Qué puede hacer en la app | Qué se hace solo en la web | Endpoints usados |
|---|---|---|---|---|
| **Camaronera** (y **semillero**, **laboratorio**: mismo panel de vendedor) | Lotes publicados con unidades disponibles · ventas en curso (por verificar / por aceptar / confirmadas) · informes por aceptar · próximos despachos | Ver mis lotes (publicados, borrador, vendidos) y su detalle · ver mis ventas con estado y trazabilidad (texto + enlace público) · ver informes de verificación y **aceptar / rechazar** (la venta se confirma sola cuando aceptan las dos partes) · ver despachos y **programar / editar** la cita (cosecha, salida, ETA, transportista, placa, teléfono) | Publicar o editar lotes, precios, certificados, fotos · contraofertar el informe · declarar cosechas, reservas y confirmaciones · fincas y piscinas · evoluciones | `GET /products`, `GET /products/{id}`, `GET /transactions?role=seller`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /verifications?role=seller`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance {decision, reason}`, `GET /dispatches`, `GET /dispatches/{id}`, `PATCH /dispatches/{id}` |
| **Empacadora** | Compras en curso · informes por aceptar · llegadas previstas hoy · proveedores en el ranking y **top 3** | Catálogo de lotes publicados con búsqueda y **comprar con verificación** (cantidad, modo plataforma/declarada, verificadora del directorio) · mis compras con estado y trazabilidad (la compra se confirma sola cuando aceptan las dos partes) · informes (aceptar / rechazar) · despachos (solo ver) · reservas de cosecha (ver) y **comprar el lote reservado** · ranking completo con orden · listas de precios (lectura) | Comprar lotes sin verificación · contraofertar · publicar o editar listas de precios · nuevas reservas, retirar o desistir · pedir maquila · facturas, certificado PDF y pagos | `GET /public/products`, `GET /public/products/{id}`, `GET /public/directory/verifiers`, `POST /verifications {product, qty, verification_mode, verifier}`, `GET /transactions?role=buyer`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /verifications?role=buyer`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance`, `GET /dispatches`, `GET /dispatches/{id}`, `GET /harvest/commitments`, `GET /harvest/commitments/{id}`, `POST /harvest/commitments/{id}:buy {verifier, verification_mode}`, `GET /suppliers/ranking?order=`, `GET /price-lists?role=issuer`, `GET /price-lists/{id}` |
| **Verificadora (administradora)** | Bandeja de la empresa (abiertas / en campo / cerradas), cuántas están sin técnico | **Asignar o cambiar técnico** (lista de `GET /me/technicians`; si falla, los técnicos ya vistos en la bandeja o pegar el identificador) · iniciar campo, informe de campo, llegada del camión, fotos y veredicto reutilizando las pantallas del técnico · todo funciona sin internet | Dar de alta o quitar técnicos (Mi equipo) · acreditación · subir fotos al servidor | `GET /verifications?role=verifier`, `GET /verifications/{id}`, `GET /me/technicians`, `POST /verifications/{id}:assign {technician}`, `POST /verifications/{id}:start`, `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `POST /verifications/{id}:verdict` |
| **Técnico de campo** | Lo mismo que antes, sin regresiones: bandeja, informe sin internet, cola de envío, fotos, veredicto | Iniciar campo (solo con técnico asignado: `assigned`) · informe de campo (pesos, clasificación, conteos, metabisulfito, sabor, gramajes, GPS; en verificaciones de larva: cantidad, supervivencia, tamaño y sanidad) · llegada del camión · fotos (solo en el teléfono) · veredicto (la app comprueba antes lo que exige el servidor: peso en planta, presentación, metabisulfito, tallas y sabor; en larva, los cuatro datos de larva) | Subir fotos (la API no recibe archivos) | `GET /verifications?role=technician`, `POST /verifications/{id}:start`, `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `POST /verifications/{id}:verdict` |
| **Maquilador** | Solicitudes abiertas (y cuántas sin tu oferta) · órdenes en curso (por recibir / por empacar) · actas por firmar · tarifas vigentes | Ver solicitudes y **ofertar / cambiar la oferta** (tarifa, capacidad, fechas, notas) · órdenes: **registrar recepción** (libras, insumos), **registrar empaque** (libras, cajas), **firmar el acta** (conforme / no conforme con motivo) · tarifas (lectura) | Retirar una oferta · deshacer firma, reabrir acta, cancelar orden · crear, publicar o archivar tarifas · empaque propio (`POST /copack/orders`) | `GET /copack/requests?state=`, `POST /copack/requests/{id}/offers`, `GET /copack/orders`, `GET /copack/orders/{id}`, `POST /copack/orders/{id}:reception`, `POST /copack/orders/{id}:packing`, `POST /copack/orders/{id}:sign`, `GET /copack/tariffs`, `GET /copack/tariffs/{id}` |

## Lo que se comprobó contra la API real

* `POST /verifications/{id}:start` solo vale en `assigned` (422 en `received`): el botón "Iniciar trabajo de campo" aparece solo con técnico asignado.
* El veredicto (cualquiera, también rechazar) exige el informe completo según el alcance (`scope`): la app lo comprueba con el informe local antes de encolar y no adelanta el estado local; la bandeja cambia cuando responde el servidor.
* `POST /transactions/{id}:complete` no se usa: la compra pasa sola a `confirmed` cuando ambas partes aceptan.
* `PATCH .../report` manda siempre `lines` y `counts` (vacíos si el técnico los borró): la API los reemplaza.
* `type` de los errores es `.../docs#problem-<slug>`; la app usa el slug para los mensajes propios.
* `expires_at` del login se guarda y la cabecera avisa cuando quedan menos de 15 días de sesión.
* La cola manda cada operación con el perfil (`X-Shrimp-Role`) con el que se hizo, aunque después se cambie de perfil.
* Si tras caducar la sesión entra OTRA cuenta, se borra lo de la anterior (su cola no se puede enviar con otra clave).

## Lo que no se cubrió y por qué

* **Contraoferta de precio** sobre el informe (`decision: counter`): el endpoint existe, pero es una
  negociación con importes; se deja en el portal para no cerrar un trato desde un botón de teléfono.
* **Deshacer decisiones** (`acceptance:revert`, `revert-signature`, `reopen`): existen, pero son
  excepciones con motivo y consecuencias; se dejan en el portal.
* **Retirar oferta de maquila** (`:withdraw`): no se puso botón para no retirar por error desde la lista;
  se puede rectificar la oferta (repetir el POST la edita).
* **Fotos de campo**: siguen guardándose solo en el teléfono; la API no recibe archivos.
* **Listas de precios**: solo lectura; la matriz de precios por talla se edita mejor en pantalla grande.
* **Reservas nuevas, fincas, piscinas, evoluciones, lotes de inventario**: existen en la API pero no son
  trabajo de campo ni de bolsillo; quedan en el portal (o en el ERP de la empresa).
* **Verificación declarada** (`declared-report`, `:declare`): cargar el informe declarado desde la app
  no entró en este alcance; la app sí permite elegir el modo "declarada" al comprar.
* El **ranking** solo lo devuelve la API a una cuenta con el perfil empacadora; para otras cuentas el
  panel sigue sin esa tarjeta.
* **Un técnico con varios perfiles en su propia cuenta** entra siempre al flujo del técnico (la app
  prioriza `is_field_technician` + `company`).

## Supuestos sobre la API que hay que confirmar con el backend

1. `POST /auth/login` es una ruta pública: la app envía `Idempotency-Key` igual (inofensivo) y espera
   `{"api_key","expires_at","me"}` con `me` exactamente como `GET /me`. Errores esperados: 401
   `invalid-credentials`, 403 `user-not-allowed` / `account-not-approved`, 429 `rate-limited` (RFC 9457).
2. `POST /auth/logout` revoca la clave con la que se llama; la app lo intenta y, si falla (sin señal), sale igual.
3. `GET /me/technicians` devuelve `{"data": [{id, name, role, active}]}` (así está en
   `private_verification.py`); la app también acepta una lista plana. 403 si la cuenta no tiene el perfil
   verificador: entonces se usan los técnicos ya vistos en la bandeja o el identificador pegado.
4. `X-Shrimp-Role` se acepta en todas las rutas privadas (incluidas las que no dependen del perfil) sin
   error; si el perfil no está aprobado el servidor responde 403 y la app lo muestra.
5. Las rutas públicas (`/public/products`, `/public/directory/verifiers`) ignoran la cabecera
   `Authorization` y `X-Shrimp-Role` que la app manda siempre.
6. En `GET /verifications` (lista, sin detalle) `acceptance_state == "waiting"` es suficiente para contar
   "informes por aceptar"; para saber si **mi** postura sigue pendiente la app lee `acceptances[].decision`
   del detalle.
7. `GET /dispatches` devuelve `my_role` = `seller | buyer | verifier`; solo el vendedor puede `PATCH`.
8. `POST /harvest/commitments/{id}:buy` acepta `{verifier, verification_mode}` y la compra solo procede
   con `state == "honored"`; la app muestra el botón en ese estado y deja que el servidor valide el resto.
9. `GET /copack/requests` para un maquilador devuelve, en `offers`, solo su propia oferta (serializer
   `copack_request` con `detail=True`); la app usa `offers.first` como "mi oferta".
10. `GET /copack/tariffs` devuelve también las tarifas que me dirigieron (como cliente); el maquilador
    cuenta solo las suyas (`my_role == "copacker"`).
