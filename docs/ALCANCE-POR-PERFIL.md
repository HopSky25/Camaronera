# Alcance de la app CamaronMarket por perfil

Una sola app para todos los perfiles de la plataforma. Cada cosa que se ve o se hace en la app está
respaldada por un endpoint real de `shrimp_api` (`/api/v1`). Lo que la API no permite (registrar socios,
publicar listas de precios, gestionar técnicos...) NO va en la app: en su lugar se muestra el aviso "Esto se
hace desde el portal web". Desde la versión **1.2.0+3** la app implementa además
`odoo19/docs/app/CONTRATO-MEJORAS-APP.md` (C0-C6): ver **Mejoras 1.2.0** al final.

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

## Modo supervisor

Contrato: `odoo19/docs/app/CONTRATO-FIRMA-DISPOSITIVO.md`, **Anexo B** (v1, 12-oct-2026). Un usuario
INTERNO con el grupo "Supervisor de la app" ve la app de cualquier socio **en solo lectura**; nunca
transacciona en su nombre.

**Flujo de pantallas.**

| Paso | Pantalla | Qué pasa | Endpoint |
|---|---|---|---|
| Entrar | Entrada (la misma) | El login responde `"supervisor": true` (en la raíz o en `me`): la app lo marca en `me` (que se guarda en SQLite) y entra en modo supervisor. | `POST /auth/login` |
| Proteger | **Protege tu cuenta** (la misma) | Igual que cualquier clave de app: bloqueo de pantalla, registro del teléfono con la clave de supervisor (primer registro sin firma) y la clave cifrada bajo la huella / PIN. El texto explica que cada acceso a un socio se confirma con la huella. | `POST /me/devices` |
| Abrir la app | **Desbloqueo** (la misma) | Huella o PIN. Con la clave de supervisor NO se pide `GET /me` (no tiene scopes de datos). | — |
| Elegir socio | **Elige un socio** | Buscador (`q`, con espera de 400 ms), chips por tipo (Todos, Camaronera, Empacadora, Verificadora, Maquilador, Laboratorio, Semillero) y lista paginada ("Cargar más", 30 por página) con nombre, tipo, ciudad / provincia y perfiles. Menú: Seguridad y "Salir del modo supervisor". | `GET /supervisor/partners?q&type&limit&cursor` (clave de supervisor, sin `X-Shrimp-Role`) |
| Ver su app | diálogo de huella (o "Ver su app con el PIN" en el menú de la tarjeta) | `POST /supervisor/act-as {"partner": id}` **firmado** por el teléfono del supervisor: cadena canónica de la sección 3, `Idempotency-Key`, `X-Device-Id`, `X-Signature-Timestamp`, `X-Signature`. La clave de solo lectura que devuelve vive **solo en memoria** (no se guarda ni cifrada; al cerrar la app se vuelve al directorio). Su `me` tampoco se guarda. | `POST /supervisor/act-as` |
| Perfil del socio | **¿Con qué perfil quieres ver su app?** (la de elegir perfil) | Las mismas reglas que una cuenta normal (técnico → directo; un aprobado → directo; varios → elegir). El perfil va solo en `X-Shrimp-Role`; nunca `POST /me/active-role`. | cabecera `X-Shrimp-Role` |
| Panel del socio | El panel de su perfil, con la **franja fija** arriba | "Viendo como *socio* · solo lectura" con "Cambiar de socio" y "Salir del modo supervisor", encima de TODAS las pantallas (también las abiertas encima del panel y los diálogos). Las lecturas usan la clave del socio, **sin firma**. | `GET` de cada perfil |

**Solo lectura.**

* Se ocultan todas las acciones de escritura y en su lugar aparece "Solo lectura en modo supervisor":
  comprar con verificación y comprar el lote reservado (empacadora), aceptar / rechazar el informe
  (vendedor y comprador), programar o cambiar la cita del despacho, ofertar en maquila, registrar recepción
  y empaque, firmar el acta, y en el detalle de la verificación (verificadora / técnico): asignar técnico,
  iniciar campo, llegada del camión, informe de campo, fotos y veredicto.
* Desaparecen "Pendientes de enviar" y "Mis datos y privacidad" (es el portal del supervisor, no del
  socio). "Cambiar de perfil" sigue (solo cambia `X-Shrimp-Role`). "Cerrar sesión" pasa a ser "Salir del
  modo supervisor".
* La cola no encola ni firma nada (`Sincronizador` corta con `ErrorSoloLectura` antes de serializar, firmar
  o guardar, ni siquiera un borrador de informe o una foto), y en modo supervisor no se sube nada de la cola
  aunque hubiera quedado algo. Solo se baja la bandeja si el socio es verificadora / técnico.
* Si aun así el servidor responde `403 insufficient-scope` con la clave del socio, el mensaje es "Solo
  lectura en modo supervisor: el servidor no permite hacer cambios en nombre del socio."

**Cambiar de socio, salir y caducidad.**

| Caso | Qué hace la app |
|---|---|
| "Cambiar de socio" | Vuelve al directorio y revoca la clave del socio (`POST /auth/logout` con esa clave; sin señal caduca sola a las 8 h). Lo bajado con ese socio (bandeja, caché de listas) se borra del teléfono; la sesión del supervisor sigue. |
| "Salir del modo supervisor" | Revoca la clave del socio y la de supervisor (`/auth/logout` con cada una) y borra todo, como "Cerrar sesión". |
| 401 con la clave del socio (8 h o revocada) | Vuelta al **directorio** con el aviso "Terminó el acceso de solo lectura a *socio*…"; también al volver a la app si ya pasó su `expires_at`. |
| 401 con la clave de supervisor | Vuelta a la **entrada** (como cualquier sesión caducada). Revocar la de supervisor revoca las de act-as: el 401 del socio lleva al directorio y el del directorio a la entrada. |
| Entrar como supervisor en un teléfono con datos de otra cuenta | Se borra lo de la cuenta anterior (su cola no se puede enviar con otra clave). |

**Detalles del servidor que la app maneja (Anexo B, confirmados por backend el 12-oct-2026).**

1. `type` del filtro usa el código del perfil: `camaronera`, `empacadora`, `verificador`, `maquilador`,
   `laboratorio`, `semillero` (el chip "Verificadora" manda `verificador`); otro valor → 400
   `invalid-parameter`.
2. El directorio pagina por cursor en orden de última modificación: la app muestra lo cargado en orden
   alfabético (sin repetir socios entre páginas). `roles` puede venir como lista de códigos o de objetos
   `{role, label, state}`; `city` / `province` pueden venir `false`.
3. La clave de act-as caduca a las 8 h pero nunca después de la de supervisor: la app usa siempre el
   `expires_at` de la respuesta de act-as (solo si faltara, 8 h acotadas a la caducidad del supervisor).
4. Un reintento idempotente de act-as devuelve `"api_key": null` (el servidor no guarda el secreto): la app
   hace un act-as NUEVO, con otra `Idempotency-Key` y otra firma (otra huella). Repetir act-as sobre el mismo
   socio revoca la clave anterior de ese socio.
5. El `me` de act-as trae `read_only: true` y `viewed_by: {"name"}`: la franja muestra "Supervisor: *nombre*".
   El `me` de supervisor trae además `id`, `email`, `active_role` y `api_key` (la app no le pide `GET /me`).
6. Con la clave de supervisor solo se llama a `/supervisor/*`, `/me/devices` y `/auth/logout`; con la de
   act-as, solo a `GET` y a `/auth/logout` (nunca `POST /me/devices`, que da `insufficient-scope`).
7. Errores con mensaje propio: 403 `supervisor-key`, `supervisor-only`, `act-as-internal-user`,
   `insufficient-scope` (con la clave del socio: "Solo lectura en modo supervisor…"), 422
   `partner-not-approved`, `partner-without-portal-user`, 404 `not-found`, 400 `invalid-parameter`.
8. `X-Shrimp-Role` funciona con la clave de act-as igual que con una clave normal.

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
| **Camaronera** (y **semillero**, **laboratorio**: mismo panel de vendedor) | Lotes publicados con unidades disponibles · ventas en curso (por verificar / por aceptar / confirmadas) · informes por aceptar · próximos despachos | Ver mis lotes (publicados, borrador, vendidos) y su detalle · **comprar larva** (camaronera) o **nauplio** (laboratorio) con compra directa (C5) · ver mis ventas con estado y trazabilidad (texto + enlace público) · ver informes de verificación y **aceptar / rechazar** (la venta se confirma sola cuando aceptan las dos partes) · ver despachos y **programar / editar** la cita (cosecha, salida, ETA, transportista, placa, teléfono) | Publicar o editar lotes, precios, certificados, fotos · contraofertar el informe · declarar cosechas, reservas y confirmaciones · fincas y piscinas · evoluciones | `GET /products`, `GET /products/{id}`, `GET /transactions?role=seller`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /verifications?role=seller`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance {decision, reason}`, `GET /dispatches`, `GET /dispatches/{id}`, `PATCH /dispatches/{id}`, `GET /public/products?seller_type=`, `POST /purchases` |
| **Empacadora** | Compras en curso · informes por aceptar · llegadas previstas hoy · proveedores en el ranking y **top 3** | Catálogo de lotes publicados con búsqueda (camarón, larva, nauplio) y **comprar con verificación** o, si el lote no la exige, **compra directa** (C5) (cantidad, modo plataforma/declarada, verificadora del directorio) · mis compras con estado y trazabilidad (la compra se confirma sola cuando aceptan las dos partes) · informes (aceptar / rechazar) · despachos (solo ver) · reservas de cosecha (ver) y **comprar el lote reservado** · ranking completo con orden · listas de precios (lectura) | Contraofertar · publicar o editar listas de precios · nuevas reservas, retirar o desistir · pedir maquila · facturas, certificado PDF y pagos | `GET /public/products`, `GET /public/products/{id}`, `GET /public/directory/verifiers`, `POST /verifications {product, qty, verification_mode, verifier}`, `GET /transactions?role=buyer`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /verifications?role=buyer`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance`, `GET /dispatches`, `GET /dispatches/{id}`, `GET /harvest/commitments`, `GET /harvest/commitments/{id}`, `POST /harvest/commitments/{id}:buy {verifier, verification_mode}`, `GET /suppliers/ranking?order=`, `GET /price-lists?role=issuer`, `GET /price-lists/{id}` |
| **Verificadora (administradora)** | Bandeja de la empresa (abiertas / en campo / cerradas), cuántas están sin técnico | **Asignar o cambiar técnico** (lista de `GET /me/technicians`; si falla, los técnicos ya vistos en la bandeja o pegar el identificador) · iniciar campo, informe de campo, llegada del camión, fotos y veredicto reutilizando las pantallas del técnico · todo funciona sin internet | Dar de alta o quitar técnicos (Mi equipo) · acreditación | `GET /verifications?role=verifier`, `GET /verifications/{id}`, `GET /me/technicians`, `POST /verifications/{id}:assign {technician}`, `POST /verifications/{id}:start`, `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `POST /verifications/{id}:verdict` |
| **Técnico de campo** | Lo mismo que antes, sin regresiones: bandeja, informe sin internet, cola de envío, fotos, veredicto | Iniciar campo (solo con técnico asignado: `assigned`) · informe de campo (pesos, clasificación, conteos, metabisulfito, sabor, gramajes, GPS; en verificaciones de larva: cantidad, supervivencia, tamaño y sanidad) · llegada del camión · **fotos subidas por la cola firmada** (C4, con su estado) · veredicto (la app comprueba antes lo que exige el servidor: peso en planta, presentación, metabisulfito, tallas y sabor; en larva, los cuatro datos de larva) | — | `GET /verifications?role=technician`, `POST /verifications/{id}:start`, `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `POST /verifications/{id}:verdict`, `POST /verifications/{id}/photos` |
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

## Mejoras 1.2.0 (CONTRATO-MEJORAS-APP.md, C0-C6)

Versión de la app **1.2.0+3** (`pubspec.yaml`; la constante `versionApp` de `lib/datos/version_app.dart`
tiene que coincidir, lo comprueba una prueba).

| | Qué hace la app | Dónde |
|---|---|---|
| **C0** cabeceras | `X-App-Version: 1.2.0` y `X-App-Platform: android\|ios` en TODAS las peticiones: privadas, firmadas, login, logout y públicas (`app-version`, `app-errors`). | `lib/datos/version_app.dart`, `ApiCliente` |
| **C1** versión mínima | `GET /public/app-version?platform=` al abrir (cuando se sabe el servidor) y al volver del segundo plano, **como mucho una vez por hora**. Versión < `min_version` → pantalla bloqueante **"Actualiza la app"** encima de todo (también del desbloqueo y la entrada) con botón al `download_url` (url_launcher), "comprobar de nuevo" y **"Cerrar sesión"** (el logout no da 426). Un **426 `app-update-required`** en cualquier respuesta (también login y públicas) la muestra igual, con el `download_url` / `min_version` del cuerpo. Si solo hay una `latest_version` más nueva: aviso discreto abajo, descartable. Comparación semántica `x.y.z`. Si el servidor quita el mínimo, se desbloquea. | `lib/datos/control_version.dart`, `lib/pantallas/inicio/pantalla_actualizar.dart` |
| **C2** errores | `FlutterError.onError`, `PlatformDispatcher.onError` y `runZonedGuarded` → cola local (SQLite `errores_app`, esquema 5) → `POST /public/app-errors` con señal (al abrir, al volver, al recuperar la red), **máx. 20 por día** por instalación (`errores_envios`), sin repetir el mismo error. Solo los 8 campos del contrato. **Sin datos personales**: de un `ErrorApi` solo viaja estado y tipo (no el `detail`); se borran correos, claves `trz_…`, `Bearer`, contraseñas, números de 7+ dígitos (cédula, RUC, teléfono) y los parámetros de las URL. `screen` = archivo de `lib/pantallas/` de la pila o la última ruta con nombre. En depuración no se reporta. 422 → se descarta; 429 / 5xx / sin señal → queda. | `lib/datos/reporte_errores.dart` |
| **C3** avisos | Campana con contador (`meta.unread_count`) en la barra de cuenta; pantalla **Avisos** paginada (Todos / Sin leer); tocar un aviso lo marca leído (`POST /me/notifications/{id}:read`, Idempotency-Key, sin firma) y abre su destino; "Marcar todo como leído" (`:read-all`). **Modo supervisor**: se ve la bandeja del socio, pero no se marca nada (ni se intenta) y no hay notificaciones ni tarea en segundo plano. Notificaciones locales (`flutter_local_notifications`, canal "Avisos de CamaronMarket", ícono propio, texto oculto en la pantalla de bloqueo), permiso `POST_NOTIFICATIONS` (Android 13+) pedido una vez con una explicación. Tarea `workmanager` cada ~15 min con red. | `lib/datos/avisos.dart`, `lib/datos/notificaciones.dart`, `lib/pantallas/comunes/pantalla_avisos.dart` |
| **C4** fotos | Cada foto se guarda primero en el teléfono; luego se comprime (lado mayor ≤ 1600 px, JPEG 75, orientación EXIF corregida, en otro isolate), se firma con la huella (una por foto) y va a la cola: `POST /verifications/{id}/photos` firmado con `filename`, `mime`, `data` (base64), `taken_at`, `latitude`/`longitude` (última posición conocida) y `note`. Estado por foto: **sin confirmar** (falta la huella; "Subir"), **pendiente**, **subida**, **error**. Un rechazo definitivo (p. ej. **409 `invalid-state`**: la verificación ya no está en campo) queda con un mensaje claro y **no se reintenta solo**; descartado en Pendientes, la foto vuelve a "sin confirmar". Si el cuerpo no cabe en una fila de SQLite (~1,8 MB) se reduce a 1280 / 1024 px. | `lib/modelos/foto_campo.dart`, `Sincronizador.subirFoto`, `lib/pantallas/pantalla_fotos.dart` |
| **C5** compra directa | En el catálogo, los lotes sin verificación obligatoria (larva, nauplio) dicen "Compra directa" y su detalle tiene **Comprar** → cantidad (mayor que 0, no más que el disponible; unidades enteras, millares hasta 3 decimales, libras hasta 2) y nota opcional → confirmación con el total estimado → huella → `POST /purchases` **firmado** → "Compra registrada" con la transacción. **No pasa por la cola**: sin señal no se compra; "Reintentar" reenvía la MISMA operación firmada (misma Idempotency-Key, no duplica). 403 `forbidden` y 422 `business-rule` muestran el `detail`; 422 `verification-required` ofrece "Comprar con verificación". Oculto en modo supervisor. Entradas nuevas: "Comprar larva" (camaronera) y "Comprar nauplio" (laboratorio). Precios "\$ 2.97 por libra" / "por millar" (singular, minúscula). | `lib/modelos/compra_directa.dart`, `Sincronizador.firmarCompraDirecta/enviarCompraDirecta`, `PantallaCompraDirecta` |
| **C6** enlaces | App Links `https` con `android:autoVerify="true"` en camaronmarket.com, www., demo., verificadores., empaque., verificadores.demo. y empaque.demo. (rutas del contrato) y esquema `camaronmarket://` con la misma ruta (`app_links`; el enrutador por defecto de Flutter apagado). El destino espera a que la sesión esté lista (entrada, protección, desbloqueo, perfil, socio en modo supervisor) y se abre encima del inicio. Ruta desconocida o id que no es uuid → inicio. `/marketplace/dispatch/<uuid>` lleva el uuid de la **compra** (así lo pide el portal) → abre la compra. Los avisos navegan por `target` (el del despacho trae el uuid del despacho) y, si no se reconoce, por `link`; `/copacker/requests/<uuid>` → solicitudes de empaque. | `lib/navegacion/destinos.dart`, `lib/navegacion/enrutador.dart`, `AndroidManifest.xml` |

**La clave de API y la tarea en segundo plano (C3).** La clave `trz_…` está cifrada con una clave AES del
Keystore que exige la huella o el PIN; WorkManager no puede descifrarla y **no se guarda ninguna copia
legible** (ni una segunda clave del Keystore sin autenticación, que la dejaría al alcance de cualquiera con
acceso a la app). Por eso:

1. Si el proceso de la app sigue vivo (la clave está en memoria desde el desbloqueo; también con la app
   bloqueada), la tarea le pide a la app viva —en el mismo isolate o por `IsolateNameServer`— que consulte
   `unread=true` y muestre una notificación por cada aviso nuevo (título y cuerpo cortos del servidor). La
   app viva además revisa cada 15 min con un temporizador.
2. Si la app está cerrada, la tarea no consulta nada: con un estado sin secretos guardado por la app
   (`avisos_estado.json`: contador de no leídos, ids ya conocidos y una bandera) muestra **una sola vez**
   "Tienes avisos nuevos en CamaronMarket" cuando la app vio avisos sin leer que nadie había recordado.
   Al tocarla se abre la bandeja de avisos tras desbloquear.

Consecuencia: con la app cerrada del todo, un aviso que llegue después no se notifica hasta abrirla (el
contrato no tiene una credencial solo-lectura para avisos). Si se quisiera, haría falta una clave de app
limitada a `GET /me/notifications` (cambio de contrato).

**Firma de publicación.** `android/app/build.gradle.kts` firma el release con `key.properties` si existe
(variable `CAMARONMARKET_KEY_PROPERTIES`, `android/key.properties` o `C:/dev/llaves/key.properties`) y, si
no, con la llave de depuración. `key.properties`, `*.jks` y `*.keystore` están en `.gitignore`. La huella
SHA-256 del certificado de subida va en `shrimp_api.android_cert_sha256` para `assetlinks.json`; con Play
App Signing hay que añadir también la huella de la llave de firma de la app que da Google Play.

**Correcciones de la prueba en el emulador (1.2.0).** Informe de campo: se validan también los campos fuera
de pantalla, aviso "Revisa los campos marcados en rojo", la vista baja al primer error y el error se borra al
corregir; etiqueta "Cam./lb". GPS con la ubicación apagada: "Sin ubicación: activa la ubicación del
teléfono". Cámara: `<queries>` de `IMAGE_CAPTURE` para que `image_picker` dé el permiso explícito; el aviso de
Android 17 sobre el grant implícito de `ACTION_IMAGE_CAPTURE` sigue pendiente del plugin
(`image_picker_android` 0.8.13+25 es la última y no pone los `FLAG_GRANT_*` en el intent).

**iPhone.** Preparado sin compilar (no hay Mac): ver `ios/README-SEGURIDAD.md`.

**Lo que no se puede verificar sin teléfono / Mac.** Que Android verifique los App Links (necesita
`assetlinks.json` con la huella en cada dominio), que WorkManager despierte la app y la notificación salga
con la app en segundo plano / cerrada, el diálogo del permiso de notificaciones de Android 13+, tocar una
notificación con la app cerrada, la cámara + compresión real y el GPS de las fotos, todo lo de iPhone.
