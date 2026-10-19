# API externa — qué se puede hacer desde afuera

Módulos: `shrimp_api` (19.0.1.0.0) y `shrimp_api_sri` (19.0.1.0.0, se instala solo cuando
están `shrimp_api` y `l10n_ec_sri_community`).

Documentación interactiva (Redoc, OpenAPI 3.1 generado del propio código):

* `https://TU-DOMINIO/api/v1/docs`
* `https://TU-DOMINIO/api/v1/openapi.json` (impórtalo en Postman, Insomnia u openapi-generator)

En todos los ejemplos `https://TU-DOMINIO` es la URL pública del Odoo y `$KEY` la clave de API.

---

## 1. Reglas generales

| Tema | Regla |
|---|---|
| Base | `/api/v1` (privado) y `/api/v1/public/...` (público, sin clave) |
| Formato | JSON UTF-8. Los cuerpos (POST/PUT/PATCH) tienen que ser un **objeto** JSON con `Content-Type: application/json` |
| Identificadores | **Siempre `uuid`** (`"id": "6f1c…"`). Nunca ids enteros de la base. Los comprobantes SRI se identifican por su clave de acceso de 49 dígitos |
| Relaciones | `{"id": "<uuid>", "name": "…"}` |
| Fechas | ISO-8601 en UTC: `2026-10-01T14:30:00Z`; fechas sin hora `2026-10-01`. Si mandas una hora sin zona se toma como UTC |
| Importes | `{"amount": 2.75, "currency": "USD"}` |
| Errores | `application/problem+json` (RFC 9457), ver §6 |
| Escrituras | POST crea o ejecuta una acción, PATCH es parcial, PUT reemplaza. Las acciones son `POST /recurso/{id}:accion` (también vale `POST /recurso/{id}/accion`) |
| Idempotencia | Todo POST exige la cabecera `Idempotency-Key` (§5) |

---

## 2. Cómo conseguir una clave

1. Entra al portal con tu usuario de socio → **Mi cuenta → Integraciones (API)** (`/my/api-keys`).
2. «Crear una clave»: nombre, caducidad (30, 90, 180 días o 1 año; **por defecto y como máximo 1 año**,
   parámetro `shrimp_api.key_lifetime_days`), los **scopes** que la integración necesita
   (solo esos), y opcionalmente:
   * **IPs permitidas** (una IP o red CIDR por línea): si se rellena, cualquier otra IP recibe 403.
   * **Orígenes CORS**: solo si vas a llamar desde un navegador (ver §9).
3. La clave se muestra **una sola vez**: `trz_<prefijo>_<secreto>`. La plataforma guarda solo el prefijo y un
   hash PBKDF2-SHA512; si la pierdes, revócala y crea otra.
4. Revocar es inmediato (botón «Revocar» en el portal, o el administrador desde
   *Camaronera → Configuración → Integraciones (API) → Claves de API*).
5. **Caducidad y renovación** (D-46): toda clave caduca. 7 días antes (`shrimp_api.key_expiry_notice_days`)
   llega un correo al dueño y aparece un aviso en `/my` y en `/my/api-keys`. El botón **«Renovar»** alarga la
   **misma** clave (mismo secreto: no hay que tocar la integración) un periodo completo desde hoy; sirve
   también para una clave ya caducada, pero no para una revocada o desactivada ni con la cuenta bloqueada.
   Una clave caducada responde `401` con `"code": "key-expired"`.

Las claves son de **socios del portal**. Un usuario interno no puede tener clave de esta API: sin las
reglas del portal vería los datos de todos. Las integraciones internas usan la API JSON-2 nativa de Odoo
(módulo `rpc`).

### Scopes

| Scope | Para qué |
|---|---|
| `catalog:read` | (reservado; los catálogos son públicos) |
| `profile:write` | `PATCH /me` |
| `facilities:read` / `facilities:write` | instalaciones y piscinas |
| `products:read` / `products:write` | productos (lotes publicados), evolución, aprobar solicitudes de chequeo |
| `lots:read` / `lots:write` | inventario por lote, movimientos, siembras en piscina |
| `transactions:read` / `transactions:write` | compras/ventas, trazabilidad, cobros, recepción, solicitudes de chequeo |
| `verifications:read` / `verifications:write` | verificaciones, informe de campo, veredicto, aceptación, ranking de proveedores |
| `dispatch:write` | despacho: cita (ETA), salida, llegada real |
| `pricelists:read` / `pricelists:write` | listas de precios y avisos de lotes |
| `harvest:read` / `harvest:write` | reserva anticipada: cosechas declaradas, compromisos, confirmaciones |
| `copack:read` / `copack:write` | maquila: solicitudes, ofertas, órdenes, acta, tarifas |
| `exports:read` / `exports:write` | salidas / exportaciones (`GET/POST /exports`, `POST /exports/{id}:cancel`): fecha, destino, DAE, factura, contenedor y de qué lote(s) sale; es el último eslabón de la trazabilidad |
| `invoices:read` | comprobantes electrónicos SRI (módulo `shrimp_api_sri`) |
| `webhooks:manage` | suscripciones a webhooks |

Una ruta de lectura acepta el scope `:read` o el `:write` correspondiente. Además del scope, **cada
operación comprueba el papel** del socio (no basta con tener `copack:write` para firmar el acta de otro).

---

## 3. Autenticación

```
Authorization: Bearer trz_1a2b3c4d_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```
o bien
```
X-API-Key: trz_1a2b3c4d_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

| Respuesta | Cuándo |
|---|---|
| 401 `missing-api-key` | no hay clave |
| 401 `invalid-api-key` | no existe o el secreto no coincide |
| 401 `expired-api-key` (`code: key-expired`, `renew_url`) | caducada: se renueva en `/my/api-keys` |
| 401 `revoked-api-key` | revocada |
| 403 `user-not-allowed` | el usuario dueño está inactivo o no es de portal |
| 403 `ip-not-allowed` | la IP no está en la lista de la clave |
| 403 `insufficient-scope` | falta el scope; `detail` dice cuál |
| 403 `account-pending` (`code: account_pending`) | la cuenta (el perfil con el que actúa la petición) está pendiente de aprobación: solo lectura (GET), las escrituras se rechazan; no se pueden crear claves nuevas |
| 403 `account-rejected` (`code: account_rejected`) | la plataforma rechazó la cuenta: todo se rechaza salvo `GET /me`; al rechazarla se desactivan sus claves |
| 403 `legal-acceptance-required` (`code`, `accept_url`, `pending_documents`) | hay versiones MAYORES de documentos legales sin aceptar: todo se rechaza salvo `GET /me` hasta aceptarlas en `accept_url` (/my/legal/accept), igual que en el portal |
| 403 `seller-suspended` (`code: seller_suspended`) | publicación suspendida por strikes: `POST /products` y `POST /products/{id}:publish` se rechazan; el resto de la API sigue disponible |

`GET /me` responde siempre (también a una cuenta bloqueada) e incluye
`account_state` (approved / pending / rejected) y `legal`
(`acceptance_required`, `pending_documents`, `accept_url`) para diagnosticar.

Comprobar la clave:

```bash
curl -s https://TU-DOMINIO/api/v1/me -H "Authorization: Bearer $KEY"
```
```json
{"id": "8d4…", "name": "Camaronera Las Mareas", "type": "camaronera", "province": "El Oro",
 "ratings": {"as_seller": {"avg": 4.6, "count": 12}, "as_buyer": {"avg": 0.0, "count": 0}},
 "email": "admin@lasmareas.ec",
 "api_key": {"prefix": "1a2b3c4d", "name": "ERP finca", "scopes": ["facilities:read", "harvest:write"],
             "expires_at": "2027-01-01T00:00:00Z"}}
```

### Iniciar sesión con correo y contraseña (app móvil)

La app móvil no pide al socio que cree una clave a mano: entra con el correo y la contraseña del portal y
recibe una clave de API normal, que es lo que usa en adelante (se ve en **Mi cuenta → Integraciones (API)**
como «App móvil – <dispositivo>» y se puede revocar desde ahí).

`POST /api/v1/auth/login` — **público** (sin clave, sin `Idempotency-Key`), cuerpo JSON:

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"login": "admin@lasmareas.ec", "password": "********", "device": "Pixel 7 de Juan"}'
```
```json
{"api_key": "trz_1a2b3c4d_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
 "expires_at": "2027-10-11T18:20:00Z",
 "device_required": true,
 "me": { ...exactamente lo que devuelve GET /me con esa clave... }}
```

* La clave es **de la app** (`device_required: true`): antes de una operación sensible la app registra el
  teléfono (`POST /me/devices`) y firma cada una de esas peticiones (§5 bis).

* `device` es opcional (máx. 64 caracteres); solo sirve para nombrar la clave.
* La clave dura **365 días** y lleva todos los scopes `*:read` más `profile:write`, `verifications:write`,
  `dispatch:write`, `harvest:write`, `pricelists:write`, `copack:write`, `transactions:write`,
  `products:write`, `facilities:write`, `lots:write` y `exports:write` (nunca `webhooks:manage`). Sin lista
  de IPs ni CORS. El secreto solo viaja en esta respuesta: guárdalo en el almacén seguro del teléfono.
* Solo usuarios **de portal** y activos. Un perfil pendiente de aprobación no impide entrar (la app
  muestra el estado en `me.roles`).
* Si el socio ya tiene 10 claves activas (tope del portal), se revocan primero las claves «App móvil» más
  antiguas; si las 10 son manuales, 409 `too-many-keys`.

| Respuesta | Cuándo |
|---|---|
| 401 `invalid-credentials` | correo o contraseña incorrectos (no se dice cuál, ni si el correo existe) |
| 403 `user-not-allowed` | el usuario es interno o está inactivo |
| 403 `account-not-approved` | la cuenta no tiene ningún perfil (ni activo ni solicitado) |
| 403 `mfa-not-supported` | el usuario tiene verificación en dos pasos: que cree la clave desde el portal |
| 429 `rate-limited` | más de **5 intentos fallidos en 15 minutos** desde la misma IP para el mismo correo (`Retry-After` en segundos) |
| 426 `app-update-required` | hay versión mínima de la app y `X-App-Version` es menor o no viene (§5 quater) |

`POST /api/v1/auth/logout` — privado (con la clave, exige `Idempotency-Key` como todo POST privado), sin
scope: revoca la clave con la que se llama, igual que «Revocar» en el portal. Responde
`{"revoked": true}`; una segunda llamada con la misma clave recibe 401 `revoked-api-key`.

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/auth/logout -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $(uuidgen)"
```

---

## 4. Listas, paginación y sincronización

Todas las listas devuelven:

```json
{"data": [ ... ], "meta": {"next_cursor": "eyJ3Ijo…", "count": 50, "limit": 50, "has_more": true}}
```

* `limit` (1–200, por defecto 50).
* `cursor`: el valor opaco de `meta.next_cursor`. No lo construyas tú.
* `updated_since=2026-10-01T00:00:00Z`: solo lo modificado desde esa fecha.
* El orden es estable por (fecha de última modificación, id): sirve para sincronizar.

Patrón de sincronización incremental (guarda la hora de inicio de cada pasada):

```python
import requests, datetime as dt
BASE, H = "https://TU-DOMINIO/api/v1", {"Authorization": f"Bearer {KEY}"}

def sync(path, since):
    started = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"limit": 200, "updated_since": since}
    while True:
        page = requests.get(BASE + path, headers=H, params=params, timeout=30).json()
        for item in page["data"]:
            upsert(item["id"], item)          # el id es un uuid estable
        if not page["meta"]["next_cursor"]:
            return started                     # úsalo como 'since' la próxima vez
        params = {"limit": 200, "cursor": page["meta"]["next_cursor"],
                  "updated_since": since}
```

---

## 5. Idempotencia

Todo `POST` privado exige `Idempotency-Key` (recomendado: un UUID v4 por operación de negocio).

* Mismo `Idempotency-Key` + misma ruta y cuerpo durante 24 h → se devuelve **la misma respuesta**, sin
  volver a ejecutar, con la cabecera `Idempotent-Replayed: true`. También se repiten los 4xx.
* Mismo `Idempotency-Key` con otra ruta o cuerpo → 422 `idempotency-key-reused`.
* Dos peticiones simultáneas con la misma clave → la segunda recibe 409 `idempotency-in-progress`.
* Si el servidor falla con 5xx, la clave se libera y puedes reintentar con la misma.

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/facilities \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: 6b2f3a2e-3c4b-4b8e-9a63-7a0d2f4b5c11" \
  -d '{"name": "Finca Puerto Bolívar", "facility_type": "farm", "province": "El Oro"}'
```

---

## 5 bis. Dispositivos y firma de operaciones (app móvil)

Contrato completo: `docs/app/CONTRATO-FIRMA-DISPOSITIVO.md` (versión 1.2). Solo afecta a las **claves de la
app** (`is_app_key`), es decir, las que emite `POST /auth/login`; las claves creadas a mano en el portal
(ERP, integraciones) **no cambian**: ni registran dispositivos ni firman.

La clave de API de la app vive en el almacén seguro del teléfono, pero una clave robada serviría desde
cualquier sitio. Por eso las operaciones que comprometen al socio exigen además la firma de un
**dispositivo registrado**: un par EC P-256 cuya clave privada vive en el Android Keystore / Secure
Enclave, no se puede exportar y solo firma tras la huella (o la credencial del teléfono).

### Registro del dispositivo

`POST /api/v1/me/devices` — con la clave de la app, exige `Idempotency-Key` y no exige scope. El **primer
registro** de una clave va **sin firma**; **sustituir** el dispositivo (la clave ya tiene uno activo) va
**firmado por el dispositivo activo** (cabeceras y cadena de abajo, con su `X-Device-Id`):

```json
{"name": "Samsung A54 de Juan", "platform": "android",
 "public_key": "<SubjectPublicKeyInfo DER en base64 estándar>", "alg": "ES256"}
```
```json
{"id": "5c0e…", "name": "Samsung A54 de Juan", "platform": "android", "alg": "ES256",
 "created_at": "2026-10-12T15:00:00Z", "active": true,
 "last_used_at": null, "revoked_at": null, "current": true}
```

* Una clave de app tiene **como máximo un dispositivo activo**: registrar otro (firmado por el actual)
  revoca el anterior (no la clave). Sin firma → 403 `signature-required`; firmado con otra clave → 403
  `invalid-signature`; con un `X-Device-Id` que no es de la clave → 403 `device-not-registered`. Así quien
  robe la clave de API no puede reemplazar el teléfono legítimo.
* **Teléfono perdido**: el socio lo revoca en el portal (eso revoca también su clave) y vuelve a iniciar
  sesión; la clave nueva no tiene dispositivo y su primer registro vuelve a ir sin firma.
* Registrar o sustituir deja una **nota interna en el historial del socio** (dispositivo, plataforma, IP,
  prefijo de la clave y, si sustituye, a cuál).
* 422 `validation-error` si la clave pública no es una SubjectPublicKeyInfo DER EC P-256 en base64 estándar
  o si `alg` no es `ES256` (campo en `errors[]`); 409 `not-an-app-key` con una clave que no es de la app.
* `GET /api/v1/me/devices` → `{"data": [...]}`: los dispositivos de mis claves de la app, activos y
  revocados (`current: true` es el de la clave con la que se llama).
* `POST /api/v1/me/devices/{id}:revoke` revoca el dispositivo **y su clave de API**. Lo mismo hace el botón
  «Revocar» de **Mi cuenta → Integraciones (API) → Mis dispositivos**, y `POST /auth/logout` (o revocar la
  clave) deja revocado su dispositivo.

### Firma de cada operación sensible

Cabeceras:
```
X-Device-Id: <uuid del dispositivo>
X-Signature-Timestamp: <segundos Unix, entero>
X-Signature: <firma ES256 en base64 estándar, formato DER (ASN.1)>
```
Cadena firmada (UTF-8, líneas separadas por `\n`, **sin** salto final):
```
METODO            POST / PATCH, en mayúsculas
RUTA              path exacto sin host ni query: /api/v1/verifications/abc:verdict
TIMESTAMP         el mismo texto de X-Signature-Timestamp
IDEMPOTENCY-KEY   el valor de la cabecera (línea vacía si un PATCH no la lleva)
SHA256_CUERPO     hex en minúsculas del SHA-256 de los BYTES EXACTOS del cuerpo (vacío = sha256 de "")
```
La ruta es la que llega al servidor (`request.httprequest.path`): si la app usa la forma `/x/{id}/accion`,
firma esa. El cuerpo se firma tal cual se envía: guarda los bytes serializados y manda exactamente esos.

* **Ventana**: el timestamp vale hasta **7 días en el pasado** (la cola sin internet firma en campo y envía
  después) y **10 minutos en el futuro** (reloj del teléfono).
* **Antirreplay**: el servidor guarda 8 días el hash de cada firma aceptada (de la firma normalizada, `r` y
  `s` bajo, para que reescribir la firma no la «renueve»). Repetirla es 409 `signature-replayed`, **salvo**
  el reintento idempotente legítimo (misma `Idempotency-Key`, ruta y cuerpo), que recibe la respuesta
  guardada (`Idempotent-Replayed: true`) mientras dure la idempotencia (24 h, §5). Como la
  `Idempotency-Key` va dentro de la cadena, la misma firma con otra `Idempotency-Key` ya no verifica (403
  `invalid-signature`).
* Los **PATCH** firmados que llevan `Idempotency-Key` también guardan su respuesta (como un POST); sin
  `Idempotency-Key` se ejecutan y repetir la firma es un replay. La app debería mandarla siempre.
* Orden de las comprobaciones: clave → scope → límite → **dispositivo y firma** → idempotencia (respuesta
  guardada) → antirreplay → operación. Los errores de firma no se guardan como respuesta idempotente: se
  puede reintentar con la misma `Idempotency-Key` y una firma buena.
* Si la operación falla con 5xx, la firma se devuelve (se puede reintentar con la misma).

Rutas firmadas (con clave de la app): `POST /verifications`, `POST /verifications/{id}/acceptance`,
`POST /verifications/{id}/acceptance:revert`, `POST /verifications/{id}:assign|:start|:verdict`,
`PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `PATCH /dispatches/{id}`,
`POST /transactions/{id}:complete`, `POST /harvest/commitments/{id}:buy`,
`POST /copack/requests/{id}/offers`, `POST /copack/orders/{id}:reception|:packing|:sign|:revert-signature|:reopen|:close`,
`POST /me/active-role`, `POST /supervisor/act-as`; desde 19.0.1.7.0 (contrato de mejoras de la app, §5 quater),
`POST /verifications/{id}/photos` y `POST /purchases`, y desde 19.0.1.16.0 `POST /products/{id}:publish` (publicar un
lote; crear y editar el borrador no se firman). Las decisiones de la verificación también van firmadas:
`POST /verifications/{id}:respond` (la verificadora acepta o declina la orden),
`POST /verifications/{id}:support-technicians` (técnicos de apoyo), `POST /verifications/{id}:second-opinion`
(el vendedor impugna el rechazo) y, si la verificadora declinó o no cumplió, `POST /transactions/{id}:switch-verifier`
(cambiar de verificadora) y `POST /transactions/{id}:cancel` (cancelar la compra; también firmada en sus otras causas).
En `/api/v1/openapi.json` llevan `x-signed: true`. `POST /me/devices` se firma
solo para sustituir el dispositivo (`x-signed: "if-device"`). Las lecturas (GET), `/auth/login` y
`/auth/logout` no se firman.

Ejemplo en Python (lo mismo que hace la app; en el teléfono la clave privada no sale del Keystore):

```python
import base64, hashlib, json, time, uuid
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

BASE = "https://TU-DOMINIO"
KEY = "trz_…"                                   # la de POST /auth/login

# 1. Par de claves y registro del dispositivo (una vez).
priv = ec.generate_private_key(ec.SECP256R1())
spki = priv.public_key().public_bytes(serialization.Encoding.DER,
                                      serialization.PublicFormat.SubjectPublicKeyInfo)
dev = requests.post(BASE + "/api/v1/me/devices", headers={
        "Authorization": "Bearer " + KEY, "Idempotency-Key": str(uuid.uuid4())},
    json={"name": "Pixel 7 de Juan", "platform": "android", "alg": "ES256",
          "public_key": base64.b64encode(spki).decode()}).json()

# 2. Operación firmada.
def signed_post(path, payload):
    body = json.dumps(payload).encode()          # estos bytes exactos se firman y se envían
    ts = str(int(time.time()))
    idem = str(uuid.uuid4())
    canonical = "\n".join(["POST", path, ts, idem, hashlib.sha256(body).hexdigest()])
    signature = priv.sign(canonical.encode("utf-8"), ec.ECDSA(hashes.SHA256()))   # DER
    return requests.post(BASE + path, data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
        "Idempotency-Key": idem, "X-Device-Id": dev["id"], "X-Signature-Timestamp": ts,
        "X-Signature": base64.b64encode(signature).decode()})

r = signed_post("/api/v1/verifications/ab2…:verdict", {"verdict": "approve", "notes": "OK"})
```

Errores (RFC 9457, como el resto):

| status | `type` (sufijo) | cuándo |
|---|---|---|
| 403 | `device-not-registered` | clave de app sin dispositivo activo pidiendo una ruta sensible (o `X-Device-Id` que no es de esta clave) |
| 403 | `signature-required` | faltan las cabeceras de firma (`detail` dice cuáles) |
| 403 | `device-revoked` | el dispositivo fue revocado (también si se revocó con él su clave): volver al inicio de sesión |
| 403 | `invalid-signature` | la firma no verifica (o `X-Signature`/`X-Signature-Timestamp` mal formados) |
| 403 | `signature-expired` | timestamp de más de 7 días o más de 10 minutos en el futuro |
| 409 | `signature-replayed` | firma ya usada y sin respuesta guardada que devolver |
| 409 | `not-an-app-key` | `POST /me/devices` con una clave que no es de la app |

**Auditoría**: cada operación firmada aceptada queda en *Camaronera → Configuración → Integraciones (API) →
Operaciones firmadas* (usuario, dispositivo, método, ruta, timestamp firmado, hash de la firma, sha256 del
cuerpo, IP, fecha de recepción y código HTTP de la respuesta), en la misma transacción que la operación. Es de
solo lectura y solo la ven los administradores; no se borra. El antirreplay sí: el cron «API externa:
antirreplay de firmas de la app (limpieza)» borra cada hora los hashes de más de 8 días. Los dispositivos se
ven en *Integraciones (API) → Dispositivos de la app* y en la ficha de cada clave.

## 5 ter. Modo supervisor (app móvil)

Contrato: `docs/app/CONTRATO-FIRMA-DISPOSITIVO.md`, Anexo B. Un usuario **interno** con el grupo
**«Supervisor de la app»** (*Ajustes → Usuarios → Camaronera (app móvil)*; todo administrador del sistema
lo tiene porque `base.group_system` lo implica) entra a la app y ve la cuenta de cualquier socio
aprobado **en solo lectura**. Nunca transacciona en nombre de un socio.

**1. Inicio de sesión (B.1).** `POST /auth/login` con su correo y contraseña. Mismos límites que el del
socio (5 fallos en 15 min por IP y correo → 429, MFA → 403 `mfa-not-supported`, mismos logs). Responde 200:

```json
{"supervisor": true, "api_key": "trz_…", "expires_at": "2026-10-12T22:41:07Z",
 "me": {"id": "…", "name": "Ana Supervisora", "email": "…", "type": null, "active_role": null,
        "supervisor": true, "roles": [], "api_key": {"prefix": "…", "scopes": [], …}},
 "device_required": true}
```

Es una **clave de supervisor** (`is_supervisor_key`): dueño = el usuario interno, de la app
(`is_app_key`), **8 h**, **sin ningún scope**. Un usuario interno SIN el grupo sigue recibiendo 403
`user-not-allowed`. Quitarle el grupo (o archivar el usuario) corta en el acto su clave y las que emitió.

Con la clave de supervisor solo funcionan: `GET /me` (el `me` de arriba), `GET|POST /me/devices` y
`POST /me/devices/{id}:revoke` (su propio teléfono, igual que el socio: el primer registro sin firma,
sustituirlo firmado), `POST /auth/logout`, y las dos rutas de abajo. **Cualquier otra ruta** (todas las de
datos, también `PATCH /me` y `POST /me/active-role`) → 403 `supervisor-key`. Las rutas `/supervisor/*` con
cualquier otra clave → 403 `supervisor-only`.

**2. Directorio (B.2).** `GET /supervisor/partners?q=&type=&limit=&cursor=` → lista paginada (cursor, como
el resto) de socios con algún perfil aprobado y un usuario de portal activo (nunca cuentas que tengan un
usuario interno): `{"id", "name", "type" (perfil activo), "roles" (aprobados), "city", "province"}`. `q`
busca en nombre, correo y RUC/cédula; `type` filtra por perfil aprobado (valor desconocido → 400
`invalid-parameter`).

**3. Actuar como (B.3).** `POST /supervisor/act-as` `{"partner": "<uuid del socio>"}`, con
`Idempotency-Key` y **firmado por el dispositivo de la clave de supervisor** (§5 bis; sin firma → 403
`signature-required`, sin dispositivo → 403 `device-not-registered`). Responde 201:

```json
{"api_key": "trz_…", "expires_at": "…", "read_only": true,
 "partner": {"id": "…", "name": "Camaronera A", "type": "camaronera", "roles": ["camaronera"], …},
 "me": {…lo mismo que GET /me con esa clave, con "read_only": true y "viewed_by": {"name": "…"}…}}
```

La clave emitida es del **usuario de portal del socio** (el primero activo por id), así que se aplican las
mismas reglas de acceso del portal; lleva `supervisor_user_id` y `supervisor_key_id`, **solo los scopes
`*:read`**, `is_app_key`, y caduca a las 8 h — o antes, cuando caduque la clave de supervisor que la emitió
(una vista no sobrevive a la sesión del supervisor). No tiene dispositivo propio ni lo registra: toda
escritura con ella → 403 `insufficient-scope` (también `POST /me/devices` y `:revoke`), salvo
`POST /auth/logout`, que la revoca. `X-Shrimp-Role` funciona igual (ver cada perfil del socio). Un nuevo
act-as del mismo supervisor sobre el mismo socio revoca la clave anterior.

Errores propios: 404 `not-found` (el socio no existe), 403 `act-as-internal-user` (la cuenta tiene un
usuario interno), 422 `partner-not-approved` (ningún perfil aprobado), 422 `partner-without-portal-user`
(sin usuario de portal activo).

**Revocación en cascada.** Revocar la clave de supervisor (logout, portal, backend, revocar su dispositivo
o el tope de 10 claves en un nuevo login) revoca todas las de «actuar como» que emitió. El socio también
las ve en *Mi cuenta → Integraciones* con la etiqueta «Supervisor (solo lectura)» y puede revocarlas; no
cuentan para su tope de 10 claves.

**Rastro.** Cada act-as deja (a) una nota en el historial del socio: «El supervisor X vio tu cuenta desde
la app (solo lectura) el dd/mm/aaaa a las hh:mm», con la clave, el dispositivo y la IP; y (b) un registro
en la auditoría de operaciones firmadas (`shrimp.api.signed.operation`, la misma de §5 bis: usuario = el
supervisor, dispositivo, firma, IP…) con dos campos propios: **Socio visto** (`act_as_partner_id`) y
**Clave de solo lectura emitida** (`act_as_key_id`); filtro «Actuar como (supervisor)».

**El secreto no se guarda.** Como en el resto de claves, solo queda el hash. La respuesta guardada para la
idempotencia lleva `api_key: null`: un reintento con la misma `Idempotency-Key` recibe la misma respuesta
sin el secreto; si se perdió, se pide otro act-as (nueva clave de idempotencia y nueva firma).

---

## 5 quater. App móvil: versión mínima, errores, avisos, fotos, compra directa y enlaces

Contrato: `docs/app/CONTRATO-MEJORAS-APP.md` (versión 1, C0–C6). La app envía siempre
`X-App-Version: x.y.z` (la de `pubspec`; el `+compilación` no cuenta) y `X-App-Platform: android|ios`.

**C1 · Versión mínima obligatoria.** `GET /api/v1/public/app-version?platform=android` (público):
```json
{"min_version": "1.2.0", "latest_version": "1.3.0",
 "download_url": "https://play.google.com/store/apps/details?id=com.camaronmarket.verificador_campo",
 "message": "Hay una versión nueva obligatoria."}
```
Se configura en **Ajustes › CamaronMarket › App móvil** (parámetros `shrimp_api.app_min_version`,
`shrimp_api.app_latest_version`, `shrimp_api.app_download_url`, `shrimp_api.app_update_message`; vacíos por
defecto = `null`, sin obligación). Con `min_version` fijada, **toda petición con una clave de la app**
(las de `/auth/login`, las de supervisor y las de «actuar como») **y `POST /auth/login`** cuyo
`X-App-Version` sea menor (comparación x.y.z) o no venga (o no sea una versión) recibe **426
`app-update-required`**, con `min_version` y `download_url` dentro del problem+json:
```json
{"type": "https://TU-DOMINIO/api/v1/docs#problem-app-update-required", "title": "Actualiza la app",
 "status": 426, "detail": "Hay una versión nueva obligatoria.", "request_id": "…",
 "min_version": "1.2.0", "download_url": "https://…"}
```
Las claves manuales de integración y las rutas públicas no cambian.

**C2 · Errores de la app (sin terceros).** `POST /api/v1/public/app-errors` (público, sin clave ni
`Idempotency-Key`), cuerpo `{"app_version", "platform", "os_version", "device_model", "screen", "error"
(obligatorio, ≤ 4000), "stack" (≤ 20000), "occurred_at" (ISO)}`; campos extra → 422. Responde **204**.
Límite **30 por hora por IP** (`shrimp_api.app_errors_per_hour`; 429 `rate-limited` con `Retry-After`),
contado en la misma tabla de límites (ventana horaria). Se agrupan por **huella** = sha256(error + primera
línea útil de la pila, saltando marcos `dart:` y `package:flutter/`) en el modelo `shrimp.app.error`
(primera/última vez, veces, versiones), visible solo para administradores en **Integraciones (API) ›
Errores de la app**. Un cron diario borra los grupos sin reportes desde hace 90 días.

**C3 · Bandeja de avisos.** Modelo `shrimp.app.notification` (destinatario, perfil, `kind`, título, texto,
destino `{type, id}`, `link`, creado, leído). Nacen en el mismo punto que los webhooks
(`shrimp.webhook.delivery._emit`) y pasan la misma regla de audiencia del evento más `has_access` del
usuario. Qué evento deja qué aviso:

| Evento | `kind` | Para | `link` |
|---|---|---|---|
| `verification.assigned` (al crearse) | `verification_assigned` | verificadora | `/verifier/verifications/<verificación>` |
| `verification.assigned` (técnico) | `verification_assigned` | técnico asignado | `/verifier/verifications/<verificación>` |
| `verification.verdict_issued` aprobada | `report_ready` | comprador y vendedor | `/marketplace/verifications/<verificación>/acceptance` |
| `verification.verdict_issued` rechazada | `verification_rejected` | comprador y vendedor | ídem |
| `verification.declared_submitted` | `report_ready` | la parte que debe confirmar | ídem |
| `verification.acceptance_due` (nuevo) | `acceptance_due` | las partes que no respondieron | ídem |
| `transaction.state_changed` (nace la compra) | `purchase_received` | vendedor | `/marketplace/sales/<compra>` |
| `transaction.state_changed` → `confirmed` | `purchase_confirmed` | comprador (y vendedor si no recibió ya «Nueva compra» en la misma operación) | `/marketplace/purchases/<compra>` · `/marketplace/sales/<compra>` |
| `dispatch.eta_changed` | `dispatch_scheduled` (1.ª vez) / `dispatch_eta_changed` | comprador, verificadora y técnico | comprador: `/marketplace/dispatch/<compra>`; verificadora/técnico: `/verifier/verifications/<verificación>` |
| `dispatch.arrived` | `truck_arrived` | comprador | `/marketplace/dispatch/<compra>` |
| `copack.request_published` (nuevo) | `copack_request_published` | maquiladores que la pueden ver | `/copacker/requests/<solicitud>` |
| `copack.order_state_changed` (orden nacida de una oferta) | `copack_offer_accepted` | maquilador | `/copacker/orders/<orden>` |

`target` lleva siempre el uuid del recurso de la API (`verification`, `transaction`, `dispatch`,
`copack_request`, `copack_order`). Ojo: la página web del despacho se abre por la **compra**
(`/marketplace/dispatch/<uuid de la compra>`), así que en ese enlace el uuid es el de la compra y el del
despacho va en `target.id`.

* `GET /api/v1/me/notifications?unread=true&limit=50&cursor=…` → `{"data": [{"id", "kind", "title", "body",
  "target": {"type", "id"}, "link", "created_at", "read"}], "meta": {"next_cursor", "count", "limit",
  "has_more", "unread_count"}}`, del más nuevo al más viejo. Con `X-Shrimp-Role`, solo los de ese perfil y
  los que no tienen perfil. Sin scope especial. La clave de supervisor recibe la lista vacía; la de «actuar
  como» ve los del socio.
* `POST /api/v1/me/notifications/{id}:read` (devuelve el aviso) y `POST /api/v1/me/notifications:read-all`
  (`{"updated": n, "unread_count": 0}`): con `Idempotency-Key`, sin firma y sin scope. La clave de «actuar
  como» recibe 403 `insufficient-scope`; la de supervisor, 403 `supervisor-key`.
* Retención: un cron diario borra los avisos **leídos** de más de 60 días. Un cron horario emite
  `verification.acceptance_due` cuando quedan menos de 12 h del plazo de aceptación (una vez por plazo).

**C4 · Fotos de campo.** `POST /api/v1/verifications/{id}/photos` — **firmada**, `Idempotency-Key`, scope
`verifications:write`. Cuerpo `{"filename", "mime": "image/jpeg"|"image/png", "data": "<base64>", "taken_at",
"latitude", "longitude", "note"}` (esta ruta admite cuerpos de hasta 8 MB). Solo el técnico asignado o el
administrador de la verificadora, con la verificación `in_field` o `done` (si no, 409 `invalid-state`). La
imagen se valida por su cabecera real (JPEG `FF D8 FF` o PNG; si no, 422) y como máximo 5 MB decodificada
(413 `payload-too-large`). Se guarda **igual que en el portal**: `ir.attachment` privado de la verificación
con token, enlazado en `photo_ids` (la ve el portal en su galería); `taken_at`, la nota y la ubicación se
guardan en el adjunto (la ubicación solo la ven usuarios internos). Respuesta 201
`{"id": "<token de la foto>", "filename", "taken_at", "size"}`. `GET /api/v1/verifications/{id}/photos` →
`{"data": [{"id", "filename", "mime", "size", "taken_at", "note", "created_at"}]}` (sin binario ni GPS; lo
pide quien ve la verificación).

**C5 · Compra directa.** `POST /api/v1/purchases` — **firmada**, `Idempotency-Key`, scope
`transactions:write`. Cuerpo `{"product": "<uuid del lote publicado>", "qty": 100, "notes": null}`. La
misma lógica que «Comprar» en el portal (`shrimp.product.execute_purchase_flow`): stock, perfil que puede
comprar (`motivo_no_comprable`, con el perfil de `X-Shrimp-Role`), unidad y precio para el comprador. Errores:
422 `verification-required` si el lote exige verificación (usar `POST /verifications`), 403 `forbidden`
con el motivo si el perfil no puede comprar ese lote, 422 `business-rule` sin stock suficiente. Respuesta
201: la compra, con el mismo serializer que `GET /transactions/{id}`. La nota queda en el historial de la
compra.

**C6 · Enlaces directos.** `GET /.well-known/assetlinks.json` (público, `application/json`) publica
`com.camaronmarket.verificador_campo` (parámetro opcional `shrimp_api.android_package_name`) con las huellas
SHA-256 del parámetro `shrimp_api.android_cert_sha256` (Ajustes › CamaronMarket › App móvil, separadas por
coma; se normalizan a `AA:BB:…` y se descartan las mal formadas). Sin huellas no hay entrada de
CamaronMarket. Odoo 19 ya sirve esa ruta desde `auth_passkey` (auto_install) con la app oficial de Odoo
(`com.odoo.mobile`, para passkeys); `shrimp_api` hereda ese controlador y **añade** la entrada de
CamaronMarket delante sin quitar la de Odoo, así que sin huellas la respuesta es la de Odoo (y `[]` solo si
`auth_passkey` no está instalado).

---

## 6. Errores (RFC 9457)

```json
{"type": "https://TU-DOMINIO/api/v1/docs#problem-validation-error",
 "title": "Datos no válidos", "status": 422,
 "detail": "El cuerpo de la petición no es válido.",
 "request_id": "c20d9817e5d94a8bbdd130dc1138bded",
 "errors": [{"field": "name", "message": "Es obligatorio."}]}
```

| status | `type` (sufijo) | significado |
|---|---|---|
| 400 | `invalid-json`, `body-not-object`, `invalid-parameter`, `invalid-cursor`, `idempotency-key-required` | petición mal formada |
| 401 / 403 | ver §3, `invalid-credentials`, `account-not-approved`, `access-denied`, `forbidden` | autenticación / permisos / papel |
| 403 | `device-not-registered`, `signature-required`, `device-revoked`, `invalid-signature`, `signature-expired` | firma de la app (§5 bis) |
| 404 | `not-found` | no existe **o no es tuyo** (no se distingue a propósito) |
| 405 | `method-not-allowed` | cabecera `Allow` con los métodos válidos |
| 409 | `invalid-state`, `conflict`, `idempotency-in-progress`, `too-many-keys`, `signature-replayed`, `not-an-app-key` | el recurso no está en el estado necesario / firma repetida (§5 bis) |
| 413 / 415 | `payload-too-large`, `unsupported-media-type` | cuerpo > 1 MB (8 MB en las fotos de campo; foto > 5 MB) / no es JSON |
| 422 | `validation-error`, `business-rule`, `idempotency-key-reused`, `verification-required` | datos inválidos o regla de negocio (el `detail` es el mensaje del negocio); `verification-required`: `POST /purchases` de un lote que exige verificación |
| 426 | `app-update-required` | clave de la app (o `/auth/login`) con `X-App-Version` menor que la mínima o sin ella; lleva `min_version` y `download_url` (§5 quater) |
| 429 | `rate-limited` | ver §7 |
| 500 | `internal-error` | nunca lleva trazas; da el `request_id` a soporte |

Los campos desconocidos **no se ignoran**: provocan 422 (un error de nombre del integrador tiene que verse).

---

## 7. Límites de peticiones

* Por clave: **600 lecturas/min** y **60 escrituras/min** (ventana fija por minuto, compartida por todos los
  workers). Ajustables por clave (campos *Lecturas/min*, *Escrituras/min*) o globalmente con los
  parámetros de sistema `shrimp_api.rate_read_per_min` y `shrimp_api.rate_write_per_min`.
* Cabeceras: `X-RateLimit-Limit`, `X-RateLimit-Remaining`; al superarlo, 429 con `Retry-After` (segundos).
* Endpoints públicos: límite por IP **desactivado por defecto** dentro de Odoo (`shrimp_api.public_rate_per_min`,
  0 = sin límite) porque detrás de un proxy sin `proxy_mode` todas las peticiones parecen venir de la misma IP.
  Hazlo en nginx:

```nginx
# http { ... }
limit_req_zone $binary_remote_addr zone=trz_public:10m rate=5r/s;
limit_req_zone $binary_remote_addr zone=trz_private:10m rate=30r/s;

server {
    # ...
    location /api/v1/public/ {
        limit_req zone=trz_public burst=20 nodelay;
        limit_req_status 429;
        proxy_pass http://odoo;
    }
    location /t/ {
        limit_req zone=trz_public burst=10 nodelay;
        limit_req_status 429;
        proxy_pass http://odoo;
    }
    location /api/v1/ {
        limit_req zone=trz_private burst=60 nodelay;
        limit_req_status 429;
        client_max_body_size 1m;
        proxy_pass http://odoo;
    }
}
```

Con nginx delante pon `proxy_mode = True` en `odoo.conf` (para que la IP de la lista blanca sea la del
cliente) y configura `dbfilter` (o `db_name`): las rutas de la API no llevan sesión y Odoo tiene que saber a
qué base va la petición.

---

## 8. Webhooks

Gestión: portal `/my/webhooks` o API (`webhooks:manage`). La URL tiene que ser **https** y pública (no se
entregan webhooks a redes privadas ni a localhost). El secreto de firma se muestra **una vez** al crear la
suscripción (o al rotarlo).

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/webhooks -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"name": "ERP planta", "url": "https://erp.miempresa.com/hooks/trazul",
       "events": ["dispatch.eta_changed", "dispatch.arrived", "verification.verdict_issued"]}'
```
```json
{"id": "0c9…", "name": "ERP planta", "url": "https://erp.miempresa.com/hooks/trazul",
 "events": ["dispatch.arrived", "dispatch.eta_changed", "verification.verdict_issued"],
 "active": true, "secret": "whsec_Q2…"}
```

Otras operaciones: `GET /webhooks`, `GET|PATCH|DELETE /webhooks/{id}`, `POST /webhooks/{id}:ping`,
`POST /webhooks/{id}:rotate-secret`, `GET /webhooks/{id}/deliveries`, `GET /webhooks/events`.

### Eventos

| Evento | Recurso | Lo reciben |
|---|---|---|
| `transaction.state_changed` | transaction | comprador y vendedor |
| `check_request.created` | check_request | comprador y vendedor |
| `verification.assigned` | verification | empresa verificadora y técnico asignado |
| `verification.verdict_issued` | verification | comprador, vendedor, verificadora |
| `verification.acceptance_decided` | verification | comprador, vendedor, verificadora |
| `verification.acceptance_reverted` | verification | comprador, vendedor, verificadora (una parte deshizo su decisión) |
| `dispatch.eta_changed` / `dispatch.arrived` | dispatch | vendedor, empacadora, verificadora, técnico |
| `price_list.published` | price_list | emisora y destinatarias (y sus grupos) |
| `forecast.published` | forecast | camaronera y empacadoras destinatarias |
| `commitment.accepted` / `commitment.broken` / `commitment.settled` | commitment | camaronera y empacadora |
| `harvest.confirmation_required` | commitment | camaronera y empacadora |
| `harvest.confirmation_reverted` | commitment | camaronera y empacadora (una parte deshizo su firma) |
| `lot.published` | product | quien puede comprar ese lote (según la cadena) y el vendedor |
| `copack.offer_received` | copack_offer | cliente que pidió el servicio |
| `copack.order_state_changed` / `copack.acta_signed` | copack_order | cliente y maquilador |
| `copack.signature_reverted` | copack_order | cliente y maquilador (una parte deshizo su firma del acta) |
| `export.registered` | export | quien despacha (salida / exportación registrada) |
| `copack.request_published` | copack_request | maquiladores que la pueden ver (el de destino si va dirigida; si es abierta, los que pueden actuar como maquilador) |
| `verification.acceptance_due` | verification | comprador y vendedor (el plazo para aceptar el informe vence en menos de 12 h; una vez por plazo) |
| `invoice.authorized` | invoice (uuid = clave de acceso) | receptor del comprobante (`shrimp_api_sri`) |
| `ping` | webhook_subscription | la propia suscripción |

Además de ser «parte», el usuario dueño de la suscripción tiene que poder leer el recurso con sus
permisos; si no, no se encola nada.

### Carga y cabeceras

```http
POST /hooks/trazul HTTP/1.1
Content-Type: application/json
User-Agent: Trazul-Webhooks/1.0
X-Trazul-Event: dispatch.eta_changed
X-Trazul-Delivery: 5f0d2c1e-…
X-Trazul-Signature: t=1759329000,v1=6f0b…c2

{"id":"5f0d2c1e-…","type":"dispatch.eta_changed","api_version":"v1",
 "occurred_at":"2026-10-01T14:30:00Z","resource":{"type":"dispatch","uuid":"9a1…"}}
```

La carga es mínima a propósito: con el `uuid` pides el detalle a la API (`GET /dispatches/9a1…`), que
vuelve a pasar por los permisos. Responde 2xx en menos de 7 s; procesa en segundo plano.

**Reintentos**: si no hay 2xx, se reintenta a los 1, 5, 15, 60 min, 3 h, 6 h, 12 h y 24 h. Tras 8 intentos
la entrega queda `dead` (visible en `GET /webhooks/{id}/deliveries`, en `/my/webhooks/{id}` y en el
backend). Los fallos internos del envío también cuentan como intento: un evento «envenenado» acaba en `dead`
en vez de reintentarse para siempre. La entrega es *al menos una vez*: deduplica por `id` (o por
`X-Trazul-Delivery`).

**Reenviar** (D-44): `POST /webhooks/{id}/deliveries/{delivery}:resend` o el botón «Reenviar» de
`/my/webhooks/{id}` vuelven a poner en cola una entrega `dead`, `retrying` o `success` con los intentos a
cero y el mismo `id` de evento (`resend_count` cuenta los reenvíos). La suscripción tiene que estar activa
(si no, `409 subscription-inactive`).

**Desactivación automática**: 50 entregas `dead` seguidas desactivan la suscripción (`auto_disabled_at`,
`disabled_reason`). El dueño recibe un **correo** y ve un **aviso** en `/my` y en `/my/webhooks`. Mientras
una suscripción está inactiva (desactivada sola o pausada) sus eventos se guardan en estado `held`; al
**reactivarla** (portal, `PATCH active=true` o backend) se reenvían automáticamente las entregas `held` y
`dead` de los **últimos 7 días** (`shrimp_api.webhook_resend_days`); lo retenido más antiguo se descarta.

### Verificar la firma

La firma es `HMAC-SHA256(secreto, "<t>." + cuerpo_crudo)` en hexadecimal. Rechaza si `t` tiene más de 5
minutos de antigüedad.

Python:
```python
import hmac, hashlib, time

def verify(secret: str, header: str, raw_body: bytes, tolerance=300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    t, sig = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or abs(time.time() - int(t)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)

# Flask:  verify(SECRET, request.headers["X-Trazul-Signature"], request.get_data())
```

Node.js (Express, cuerpo crudo):
```js
const crypto = require("crypto");
function verify(secret, header, rawBody, tolerance = 300) {
  const parts = Object.fromEntries(header.split(",").map(p => p.split("=", 2)));
  const t = parts.t, sig = parts.v1 || "";
  if (!/^\d+$/.test(t) || Math.abs(Date.now() / 1000 - Number(t)) > tolerance) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${t}.`).update(rawBody).digest("hex");
  return sig.length === expected.length &&
         crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
}
app.post("/hooks/trazul", express.raw({ type: "application/json" }), (req, res) => {
  if (!verify(process.env.TRAZUL_SECRET, req.get("X-Trazul-Signature"), req.body)) return res.sendStatus(401);
  res.sendStatus(204);  // y encola el trabajo
});
```

---

## 9. CORS

* Endpoints **públicos** (`/api/v1/public/*`, `/openapi.json`, `/docs`): `Access-Control-Allow-Origin: *`, solo
  GET/HEAD/OPTIONS, sin credenciales. Se pueden usar desde cualquier web (p. ej. el widget de trazabilidad
  de un importador).
* Endpoints **privados**: sin CORS por defecto (están pensados para servidor a servidor). Si una clave tiene
  «Orígenes CORS», ese origen recibe `Access-Control-Allow-Origin: <origen>` y el preflight se responde.
  Ojo: poner una clave en un navegador la expone a quien use ese navegador; úsalo solo para herramientas
  internas y con scopes de lectura.

---

## 10. Versionado

* La versión va en la ruta (`/api/v1`). Dentro de v1 solo se hacen cambios compatibles: campos nuevos en las
  respuestas, endpoints nuevos, valores nuevos en enumerados de **salida**. Tu cliente debe ignorar campos
  que no conoce.
* Un cambio incompatible irá a `/api/v2`, y v1 se mantendrá al menos 6 meses con la cabecera `Deprecation`.

### Cambios respecto a la API anterior (la de `shrimp_marketplace`)

| Antes | Ahora |
|---|---|
| Clave en texto plano, scopes `read/write/admin` | `trz_<prefijo>_<secreto>` con hash; las claves viejas **siguen valiendo** (se migraron a hash al instalar) con scopes equivalentes: `read` → `products:read`, `facilities:read`; `write` → además `products:write`, `facilities:write`. `admin` desaparece (actuar por cualquier vendedor) y las claves de usuarios internos quedaron desactivadas |
| Respuesta `{"product": {...}}`, `{"products": [...], "total": …}` | el objeto directamente / `{"data": [...], "meta": {...}}` |
| `?offset=` | `?cursor=` |
| `PUT` | `PATCH` (PUT se mantiene como alias en productos, instalaciones y piscinas) |
| `DELETE /facilities/{id}` borraba | archiva (`active=false`) |
| `seller_role` de una camaronera = "laboratorio" | `seller_role` = tipo real del vendedor |
| POST sin `Idempotency-Key` | obligatorio |

**La API anterior está retirada (D-32).** Su controlador (`shrimp_marketplace/controllers/api.py`) ya no se
cargaba desde que existe este módulo y se **eliminó** en 19.0.1.10.0: las mismas URLs
(`/api/v1/products`, `/api/v1/facilities`, `/api/v1/ponds`, con `X-API-Key` o `Bearer`) las sirve
`shrimp_api` con los alias de compatibilidad de la tabla. No hace falta una respuesta `410 Gone`: ninguna
URL de la API anterior desaparece. Lo único que no tiene equivalente es el scope `admin` (crear o editar
productos de **otro** vendedor), retirado a propósito: eso es una integración interna y va por JSON-2.

---

## 11. Qué puede hacer cada actor

> En todos los casos la empresa entra al portal, crea su clave con los scopes indicados y se la configura a
> su sistema. Los terceros que no son socios (consumidor, importador, certificadora, banco) usan los
> endpoints públicos, o una clave **de solo lectura** que les cede el socio que los contrata.

### 11.1 Exportador / importador (cliente de la empacadora)

No necesita cuenta. Con el **token del QR** del certificado de trazabilidad que le entrega la empacadora:

```bash
curl -s https://TU-DOMINIO/api/v1/public/traceability/Qm9x3nP0dXk1aZr8sV2wYw
```
```json
{"status": "done",
 "product": {"name": "Cosecha Piscina 7", "species": "Camarón blanco", "scientific_name": "Litopenaeus vannamei",
             "genetics_line": "SIS", "presentation": "entero", "size_grade": "30/40",
             "origin": {"facility": "Finca Las Mareas", "city": "Machala", "province": "El Oro"}},
 "chain": [{"company": "Laboratorio Mar Azul", "role": "Laboratorio", "province": "Santa Elena", "country": "Ecuador"},
           {"company": "Camaronera Las Mareas", "role": "Camaronera", "province": "El Oro", "country": "Ecuador"},
           {"company": "Empacadora del Pacífico", "role": "Empacadora", "province": "Guayas", "country": "Ecuador"}],
 "dates": {"production": null, "purchase": "2026-09-20", "harvest": "2026-09-21", "process": "2026-09-21", "delivery": "2026-09-21"},
 "evolution": {"records": 9, "first_date": "2026-06-02", "last_date": "2026-09-15",
               "survival_rate_first": 95.0, "survival_rate_last": 71.0, "stages": ["PL12", "Engorde"]},
 "certificates": [{"name": "BAP", "issuer": "Global Seafood Alliance", "number": "BAP-12345", "expiry_date": "2027-03-31"}],
 "verification": {"result": "approved", "verdict_date": "2026-09-21", "verifier": "Verificadora Costa",
                  "classes_pct": {"a": 82.1, "b": 15.4, "c": 2.5}, "yield_pct": 66.9, "metabisulfite": "pass", "taste": "good"},
 "packing": [{"plant": "Empacadora del Pacífico", "establishment_code": "AS-123", "province": "Guayas"}]}
```

También: directorio de empacadoras `GET /api/v1/public/directory/packers` y su perfil
`GET /api/v1/public/partners/{id}` (certificaciones BAP/ASC/HACCP, mercados, planta). La misma información en
HTML está en `https://TU-DOMINIO/t/<token>` (es a donde apunta el QR).

### 11.2 Certificadora (BAP / ASC / GlobalG.A.P.)

* Pública: trazabilidad por token (§11.1), lotes publicados con metadatos de certificados
  `GET /api/v1/public/products/{id}` (nombre, emisor, número, vigencia; **nunca** el archivo), catálogo de
  certificados reconocidos `GET /api/v1/public/catalogs/certificate-types`.
* Auditoría de un cliente: el auditado le crea una clave **de lectura** con caducidad corta y la IP de la
  certificadora: `facilities:read`, `products:read`, `lots:read`, `transactions:read`, `verifications:read`.
  Con ella: `GET /facilities`, `GET /ponds`, `GET /products/{id}/evolutions`, `GET /lots/{id}/moves`,
  `GET /transactions/{id}/traceability`, `GET /verifications/{id}`.

```bash
curl -s "https://TU-DOMINIO/api/v1/transactions/7c1…/traceability" -H "Authorization: Bearer $KEY_AUDITORIA"
```

### 11.3 Consumidor final

Escanea el QR → `https://TU-DOMINIO/t/<token>` (página pública, sin precios ni datos personales). Una app o
web de marca puede pintar lo mismo con `GET /api/v1/public/traceability/<token>` (CORS abierto).

### 11.4 ERP de empacadora (scopes `pricelists:*`, `harvest:*`, `transactions:read`, `verifications:read`, `dispatch:write`, `copack:*`, `webhooks:manage`, `invoices:read`)

| Necesidad | Endpoint |
|---|---|
| Aguajes elegibles para la lista (en curso y próximos) | `GET /public/catalogs/aguajes?upcoming=true` |
| Publicar la lista de precios del aguaje | `POST /price-lists` (con `aguaje`) → `POST /price-lists/{id}:publish` |
| Corregir/actualizar la matriz | `PUT /price-lists/{id}` (reemplaza renglones y bonificaciones si vienen) |
| Lotes nuevos que su lista cotiza | `GET /lot-alerts` o webhook `lot.published` |
| Compras y su estado | `GET /transactions?role=buyer&updated_since=…`, webhook `transaction.state_changed` |
| Cita del camión y llegada a planta | `GET /dispatches`, webhooks `dispatch.eta_changed` / `dispatch.arrived` |
| Informe del verificador (pesos, clases, metabisulfito) | `GET /verifications/{id}`; webhook `verification.verdict_issued` |
| Aceptar / rechazar / contraofertar el informe | `POST /verifications/{id}/acceptance` |
| Deshacer mi decisión sobre el informe (mientras la ronda siga abierta) | `POST /verifications/{id}/acceptance:revert` `{"reason": "…"}` |
| La verificadora declinó o no cumplió su plazo | `GET /transactions/{id}/verifier-options` (`overdue`, `chooser`, `can_switch`, `can_cancel`, `auto_cancel_at` y las verificadoras elegibles con su honorario) → `POST /transactions/{id}:switch-verifier` `{"verifier": "<uuid>"}` o `POST /transactions/{id}:cancel` `{"note": "…"}` (sin costo; las dos firmadas) |
| Segunda opinión tras un rechazo por el informe (vendedor) | `GET /verifications/{id}/second-opinion` (plazo, `can_request`, `problem` y verificadoras elegibles con su honorario) → `POST /verifications/{id}:second-opinion` `{"verifier": "<uuid>"}` (firmada; 201 con la nueva verificación). La compra queda retenida durante el plazo y se cancela cuando el rechazo queda firme |
| Concluir la compra verificada | `POST /transactions/{id}:complete` |
| Certificado PDF de trazabilidad | `GET /transactions/{id}/traceability.pdf` |
| Ranking de proveedores por rendimiento verificado | `GET /suppliers/ranking?order=puntaje` |
| Reserva anticipada: ver cosechas dirigidas y comprometerse | `GET /harvest/forecasts?open=true`, `POST /harvest/forecasts/{id}/commitments` |
| Retirar / desistir / comprar el lote reservado | `POST /harvest/commitments/{id}:withdraw|:desist|:buy` (`:buy` con `{"verifier": "<uuid>"}`: el adulto se compra con verificación en campo) |
| Pedir maquila a un tercero | `POST /copack/requests`, `POST /copack/offers/{id}:accept`, `POST /copack/orders/{id}:sign` |

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/price-lists -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" -d '{
  "name": "Semana 40", "aguaje": "7c4…", "dispatch_from": "2026-10-05", "open_ended": true,
  "advance_pct": 50, "advance_days": 5, "balance_days": 14,
  "recipients": ["2b5…", "9f1…"],
  "lines": [{"size_grade": "a77…", "quality": "ab", "price": 2.85},
            {"size_grade": "c10…", "channel": "directa", "quality": "a", "price": 3.10}],
  "bonuses": [{"name": "SMALL", "amount": 0.05}]}'
```
Respuesta 201 (extracto): `{"id": "e3d…", "state": "draft", "my_role": "issuer", "recipients": [...], "lines": [{"size_grade": {...}, "uom": "kg", "price": {"amount": 2.85, "currency": "USD"}}, ...]}`.
La `uom` y la calidad por defecto salen de la presentación de la talla (entero → kg, A-B; cola → lb, directa).

`aguaje` es **obligatorio** al crear y al publicar: es el uuid de un aguaje del calendario de la plataforma
(`GET /public/catalogs/aguajes?upcoming=true`), el que está en curso o uno próximo. Un aguaje que ya pasó se
rechaza con `422 business-rule` («El aguaje seleccionado ya pasó; elige el aguaje actual o uno próximo.»).
Si no envías `dispatch_from`, se toma del aguaje (su inicio, u hoy si ya está en curso). Un despacho que empieza
después de que termina el aguaje, o termina antes de que empiece, también se rechaza. En `PUT` puede omitirse:
la lista conserva el suyo (las listas históricas siguen valiendo con su aguaje, aunque haya pasado).

### 11.5 ERP de camaronera (scopes `facilities:*`, `products:*`, `lots:*`, `harvest:*`, `transactions:read`, `dispatch:write`, `pricelists:read`, `copack:*`)

| Necesidad | Endpoint |
|---|---|
| Sincronizar fincas y piscinas | `GET/POST/PATCH /facilities`, `GET/POST/PATCH /ponds` (DELETE archiva) |
| Inventario de larva comprada y siembras | `GET /lots`, `POST /lots/{id}/allocations` |
| Mediciones de crecimiento/supervivencia | `POST /products/{id}/evolutions` |
| Publicar camarón adulto | `POST /products` (engorde: `presentation` y `size_grade` obligatorias) → `:publish` |
| Declarar una cosecha futura a empacadoras | `POST /harvest/forecasts` (`open_call` o `recipients`) |
| Aceptar el compromiso de una empacadora | `POST /harvest/commitments/{id}:accept` |
| Registrar la cosecha real (nace el lote) | `POST /harvest/forecasts/{id}:harvest` |
| Firmar una cosecha fuera de banda | `POST /harvest/commitments/{id}/confirmation` |
| Deshacer mi firma de la cosecha fuera de banda | `POST /harvest/commitments/{id}/confirmation:revert` `{"reason": "…"}` |
| Fijar la cita del camión | `PATCH /dispatches/{id}` con `eta`, `farm_departure`, `carrier_name`, `vehicle_plate` |
| Precios vigentes que me pagan | `GET /price-lists?current=true` |

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/harvest/forecasts -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" -d '{
  "expected_date": "2026-11-20", "expected_lb": 40000, "presentation": "entero",
  "size_grade": "a77…", "pond": "51c…", "tolerance_lb_pct": 20, "open_call": true}'
```

```bash
curl -s -X PATCH https://TU-DOMINIO/api/v1/dispatches/9a1… -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"eta": "2026-10-02T19:30:00Z", "carrier_name": "Transportes Vera", "vehicle_plate": "GBA-1234"}'
```

### 11.6 LIMS de laboratorio / naupliero (scopes `products:*`, `lots:*`, `transactions:read`, `facilities:*`)

* Alta de lotes de larva: `POST /products` (`stage`, `species`, `genetics_line`, `avg_size_mg`,
  `survival_rate`, `initial_qty`; unidad por defecto: millar) → `POST /products/{id}:publish`.
* Seguimiento sanitario: `POST /products/{id}/evolutions` con `stage`, `avg_size_mg`, `survival_rate`,
  `health_status`, `note` (queda en el histórico y en el certificado de trazabilidad).
* Ventas: `GET /transactions?role=seller`, solicitudes de chequeo `GET /check-requests?role=seller`,
  `POST /check-requests/{id}:approve|:reject`.

```bash
curl -s -X POST https://TU-DOMINIO/api/v1/products/0f3…/evolutions -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"stage": "4e2…", "avg_size_mg": 2.4, "survival_rate": 92.5, "health_status": "PCR negativo WSSV", "note": "Muestreo 15/10"}'
```

### 11.7 Verificador / app de campo (scopes `verifications:*`, `dispatch:write`)

La clave puede ser de la empresa verificadora (administrador) o del técnico (cada técnico tiene su usuario).

La app móvil (Flutter) no necesita que el técnico cree una clave: entra por `POST /auth/login` con el correo
y la contraseña del portal (§3, «Iniciar sesión con correo y contraseña») y recibe una clave «App móvil –
<dispositivo>» de un año con los scopes de la app; al cerrar sesión llama a `POST /auth/logout`, que la
revoca. Cada técnico entra con su propio usuario, así que el informe queda firmado por quien lo cargó.

Además, la app **registra el teléfono** (`POST /me/devices`, una vez tras el inicio de sesión) y **firma**
con la clave del Keystore / Secure Enclave, tras la huella, cada operación sensible: asignar, iniciar campo,
cargar el informe, estampar la llegada, dictaminar, aceptar o revertir la aceptación, mover la cita del
despacho y cambiar de perfil activo (§5 bis). Las firmas valen 7 días, así que la cola de campo sin internet
se puede enviar después; cada una queda en la auditoría de operaciones firmadas. Si el socio pierde el
teléfono, lo revoca en **Mi cuenta → Integraciones (API) → Mis dispositivos** y esa sesión de la app se
cierra.

| Paso | Endpoint |
|---|---|
| Bandeja | `GET /verifications?role=verifier` (admin) o `?role=technician` |
| Listar los técnicos de la empresa | `GET /me/technicians` → `{"data": [{"id": "<uuid>", "name": "…", "role": "Supervisor", "active": true}]}` (403 si la cuenta no es verificadora) |
| Asignar técnico (admin) | `POST /verifications/{id}:assign` `{"technician": "<uuid del técnico>"}` |
| Iniciar campo | `POST /verifications/{id}:start` |
| Cargar el informe (parcial, desde el móvil) | `PATCH /verifications/{id}/report` (pesos, presentación, metabisulfito, sabor, `lines`, `counts`, GPS) |
| Estampar la llegada del camión | `POST /dispatches/{id}/arrival` |
| Aceptar o declinar la orden (admin) | `POST /verifications/{id}:respond` `{"decision": "accept|decline", "reason": "…"}` (motivo obligatorio al declinar; dentro de `verifier_response_deadline` y antes de ir a campo; asignar el técnico también la acepta) |
| Técnicos de apoyo (admin) | `POST /verifications/{id}:support-technicians` `{"add": [{"technician": "<uuid>"}], "remove": […]}` (exige el responsable asignado) |
| Veredicto | `POST /verifications/{id}:verdict` `{"verdict": "approve|approve_obs|reject", "notes": "…"}` |
| Rechazo por «Producto no presentado» | `POST /verifications/{id}:verdict` `{"verdict": "reject", "reject_reason": "not_presented", "arrival_at": "<UTC>", "notes": "…", "photos": [{"data": "<base64>", "name": "…"}]}` (sin trabajo de campo: desde `assigned`; fotos obligatorias si la verificación aún no tiene) |
| Registrar el teléfono (app) | `POST /me/devices` `{"name", "platform", "public_key", "alg": "ES256"}`; `GET /me/devices`; `POST /me/devices/{id}:revoke` |
Quién hace qué: el **técnico responsable** (`my_role` «technician») y la empresa firman el veredicto; el
**técnico de apoyo** (`my_role` «support_technician») inicia el campo, carga el informe y sube fotos, pero
no dictamina (403). El detalle de la verificación lo dice para quien consulta: `can_verdict`, `can_manage`
(administrador de la verificadora, aunque además vaya de responsable), `can_respond`
(la orden espera aceptar o declinar), `can_decline`, `can_request_second_opinion` (vendedor) y
`contest_hours` (horas que tendrá el vendedor para impugnar un rechazo por el informe; 0 = el rechazo queda
firme). Plazos visibles: `verifier_response_deadline`, `deadline_assign`, `deadline_verdict`,
`deadline_overdue` («assign», «verdict», «declined», «response»), `acceptance_deadline` y
`contest_deadline`. Un rechazo trae `rejection_reason` («field» o «not_presented») y, si fue por producto no
presentado, `not_presented_arrival`.

Con la clave de la app, todos los pasos de escritura de esta tabla van **firmados** (registrar el teléfono, solo
cuando sustituye a uno activo): cabeceras `X-Device-Id`, `X-Signature-Timestamp`, `X-Signature` (§5 bis).
**firmados** (cabeceras `X-Device-Id`, `X-Signature-Timestamp`, `X-Signature`; §5 bis).

```bash
curl -s -X PATCH https://TU-DOMINIO/api/v1/verifications/ab2…/report -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{
  "weight_sent_lb": 10000, "weight_plant_lb": 10520, "trash_lb": 40, "presentation": "entero",
  "metabisulfite_ppm": 45, "taste_result": "good",
  "lines": [{"quality_class": "a", "size_code": "21", "weight_lb": 5600},
            {"quality_class": "b", "size_code": "26", "weight_lb": 1200}],
  "counts": [{"value": 24}, {"value": 26}]}'
```
El GPS se guarda pero la API no lo devuelve; las fotos se siguen subiendo desde el portal (la API no maneja
archivos).

### 11.8 Transportista / garita de planta

No tienen rol propio en la plataforma. Se integran a través de quien los contrata:

* El **sistema del vendedor** (camaronera) publica la cita y el transporte: `PATCH /dispatches/{id}`
  (`farm_departure`, `eta`, `carrier_name`, `vehicle_plate`, `carrier_phone`).
* La **garita** de la planta consulta las citas del día con la clave de la empacadora:
  `GET /dispatches?state=programado` o recibe `dispatch.eta_changed`.
* La **llegada real** la estampa el técnico verificador (`POST /dispatches/{id}/arrival`): es deliberado,
  la hora la pone un tercero que no es ni vendedor ni comprador.

### 11.9 Maquilador (scopes `copack:*`)

| Necesidad | Endpoint |
|---|---|
| Bandeja de solicitudes | `GET /copack/requests?state=published` |
| Ofertar / rectificar | `POST /copack/requests/{id}/offers` (una por solicitud; repetir la edita) |
| Retirar oferta | `POST /copack/offers/{id}:withdraw` |
| Recepción en planta | `POST /copack/orders/{id}:reception` `{"received_lb": 40000, "supplies_received": true}` |
| Empaque terminado (abre el acta) | `POST /copack/orders/{id}:packing` `{"packed_lb": 39880, "boxes": 1994}` |
| Firmar / rechazar el acta | `POST /copack/orders/{id}:sign` `{"decision": "accepted"}` |
| Deshacer mi firma del acta (mientras no esté cerrada) | `POST /copack/orders/{id}:revert-signature` `{"reason": "…"}` |
| Reabrir un acta en disputa | `POST /copack/orders/{id}:reopen` `{"reason": "…"}` |
| Tarifas dirigidas a clientes | `POST /copack/tariffs`, `PUT /copack/tariffs/{id}`, `:publish`, `:archive` |

Webhooks útiles: `copack.order_state_changed`, `copack.acta_signed`, `copack.signature_reverted`.

### 11.10 Banco / financiera

No es socio. Un productor que pide financiamiento le **cede una clave de solo lectura** (caducidad corta e IP
del banco) con `transactions:read`, `harvest:read`, `invoices:read`, `verifications:read`:

* Ventas confirmadas y su estado: `GET /transactions?role=seller`.
* Compromisos de compra aceptados (la reserva es un contrato con una empacadora): `GET /harvest/commitments?state=accepted`.
* Facturas autorizadas por el SRI: `GET /invoices` + `/invoices/{clave}/xml`.
* Calidad verificada de su producto: `GET /verifications?role=seller`.

El banco no ve ni el precio de otros productores ni datos de terceros: la clave hereda exactamente los
permisos del productor.

### 11.11 ERP contable (scopes `invoices:read`, `transactions:read`)

```bash
curl -s "https://TU-DOMINIO/api/v1/invoices?updated_since=2026-09-01T00:00:00Z" -H "Authorization: Bearer $KEY"
```
```json
{"data": [{"access_key": "0110202601099…", "number": "001-001-000000123", "document_type": "01",
           "document_type_label": "Factura", "state": "authorized", "date": "2026-10-01",
           "authorization_number": "0110202601099…", "issuer": {"name": "Trazul S.A.", "vat": "0990000000001"},
           "totals": {"untaxed": {"amount": 300.0, "currency": "USD"}, "tax": {"amount": 0.0, "currency": "USD"},
                      "total": {"amount": 300.0, "currency": "USD"}}}],
 "meta": {"next_cursor": null, "count": 1, "limit": 50, "has_more": false}}
```

* `GET /invoices/{clave}/xml` → el **XML autorizado** (el que vale ante el SRI; nunca el borrador).
* `GET /invoices/{clave}/ride.pdf` → RIDE.
* `GET /charges` → servicios que la plataforma le factura (comisión por venta al vendedor,
  honorario de verificación a quien lo paga, comisión de empaque al maquilador), con su
  estado (`pending`, `invoiced`, `error`, `to_credit`, `credited`, `cancelled`) y el número
  de la factura electrónica.
* Webhook `invoice.authorized`.

Solo aparecen comprobantes **autorizados o anulados** cuyo receptor es la empresa del dueño de la clave.

---

## 12. Referencia rápida de endpoints

### Públicos (sin clave, CORS `*`)
`GET /api/v1/public/catalogs/{species|stages|genetics-lines|size-grades|uoms|taste-criteria|certificate-types|aguajes?year=|aguajes?upcoming=true}`,
`GET /public/products`, `GET /public/products/{id}`,
`GET /public/directory/{packers|copackers|verifiers|sellers}`, `GET /public/partners/{id}`,
`GET /public/verification-fee/quote?lb=`, `GET /public/traceability/{token}`,
`GET /openapi.json`, `GET /docs`. HTML: `GET /t/{token}`.
Sesión de la app móvil: `POST /auth/login` (correo + contraseña → clave de API; sin `Idempotency-Key`).
App móvil (§5 quater): `GET /public/app-version`, `POST /public/app-errors`. Fuera de `/api/v1`:
`GET /.well-known/assetlinks.json`.

### Privados
* Sesión de la app: `POST /auth/logout` (revoca la clave con la que se llama y su dispositivo)
* Dispositivos de la app (§5 bis): `GET|POST /me/devices` (sustituir uno activo va firmado por él), `POST /me/devices/{id}:revoke`. Con la clave de
  la app van **firmadas**: `POST /verifications`, `POST /verifications/{id}/acceptance`,
  `POST /verifications/{id}/acceptance:revert`, `POST /verifications/{id}:assign|:start|:verdict`,
  `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `PATCH /dispatches/{id}`,
  `POST /transactions/{id}:complete`, `POST /harvest/commitments/{id}:buy`, `POST /copack/requests/{id}/offers`,
  `POST /copack/orders/{id}:reception|:packing|:sign|:revert-signature|:reopen|:close`, `POST /me/active-role`,
  `POST /verifications/{id}/photos`, `POST /purchases`, `POST /products/{id}:publish` y
  `POST /verifications/{id}:respond|:support-technicians|:second-opinion` y `POST /transactions/{id}:switch-verifier|:cancel`
* App móvil (§5 quater): `GET /me/notifications`, `POST /me/notifications/{id}:read`,
  `POST /me/notifications:read-all`, `GET|POST /verifications/{id}/photos`, `POST /purchases`
* Modo supervisor (§5 ter, solo clave de supervisor): `GET /supervisor/partners`, `POST /supervisor/act-as` (firmada)
* Perfil: `GET /me`, `PATCH /me`, `POST /me/active-role`
* Instalaciones: `GET|POST /facilities`, `GET|PATCH|PUT|DELETE /facilities/{id}`; piscinas: ídem en `/ponds`
* Productos: `GET|POST /products`, `GET|PATCH|PUT|DELETE /products/{id}`, `POST /products/{id}:publish|:archive|:unarchive` (`:publish` firmada con la clave de la app), `GET|POST /products/{id}/evolutions`
* Lotes: `GET /lots`, `GET /lots/{id}`, `GET /lots/{id}/moves`, `GET|POST /lots/{id}/allocations`
* Compras/ventas: `POST /purchases` (compra directa, firmada), `GET /transactions`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /transactions/{id}/traceability.pdf`, `POST /transactions/{id}:receive|:complete`, `POST /transactions/{id}/trace-token:rotate`, `GET /charges`
* Chequeo: `GET /check-requests`, `GET /check-requests/{id}`, `POST /check-requests/{id}:approve|:reject`
* Verificación: `GET /me/technicians`, `GET /verifications`, `GET /verifications/{id}`, `GET|POST /verifications/{id}/photos`, `POST /verifications/{id}/acceptance`, `POST /verifications/{id}/acceptance:revert`, `POST /verifications/{id}:assign|:start|:verdict|:respond|:support-technicians`, `PATCH /verifications/{id}/report`, `GET /verifications/{id}/second-opinion`, `POST /verifications/{id}:second-opinion`, `GET /suppliers/ranking`
* Despacho: `GET /dispatches`, `GET /dispatches/{id}`, `PATCH /dispatches/{id}`, `POST /dispatches/{id}/arrival`
* Listas de precios: `GET|POST /price-lists`, `GET|PUT /price-lists/{id}`, `POST /price-lists/{id}:publish|:archive`, `GET /lot-alerts`
* Reserva: `GET|POST /harvest/forecasts`, `GET /harvest/forecasts/{id}`, `POST /harvest/forecasts/{id}:publish|:cancel|:harvest`, `POST /harvest/forecasts/{id}/commitments`, `GET /harvest/commitments`, `GET /harvest/commitments/{id}`, `POST /harvest/commitments/{id}:accept|:reject|:withdraw|:desist|:buy`, `POST /harvest/commitments/{id}/confirmation`, `POST /harvest/commitments/{id}/confirmation:revert`
* Maquila: `GET|POST /copack/requests`, `GET /copack/requests/{id}`, `POST /copack/requests/{id}:cancel`, `POST /copack/requests/{id}/offers`, `POST /copack/offers/{id}:accept|:withdraw`, `GET /copack/orders`, `GET /copack/orders/{id}`, `POST /copack/orders/{id}:reception|:packing|:sign|:revert-signature|:reopen|:close|:cancel`, `GET|POST /copack/tariffs`, `GET|PUT /copack/tariffs/{id}`, `POST /copack/tariffs/{id}:publish|:archive`
* Webhooks: `GET /webhooks/events`, `GET|POST /webhooks`, `GET|PATCH|DELETE /webhooks/{id}`, `POST /webhooks/{id}:ping|:rotate-secret`, `GET /webhooks/{id}/deliveries`, `POST /webhooks/{id}/deliveries/{delivery}:resend`
* SRI (`shrimp_api_sri`): `GET /invoices`, `GET /invoices/{clave}`, `GET /invoices/{clave}/xml`, `GET /invoices/{clave}/ride.pdf`

---

## 13. Lo que NO se expone, y por qué

| No se expone | Por qué |
|---|---|
| Ids enteros de la base | Permiten enumerar registros ajenos y atan a los integradores a detalles internos |
| RUC/cédula de personas, teléfonos y correos de terceros, cuentas bancarias | Datos personales (LOPDP). En `/me` sí ves los tuyos |
| Márgenes y comisiones de la plataforma (`margin_pct`, `platform_amount`, `verifier_amount`, `commission_cents`, `rate_cents`, `platform_rate_per_lb`) | Información comercial interna de la plataforma |
| Precios de otros productores, destinatarios de una lista o de una tarifa (salvo al emisor), compromisos de otras empacadoras | Confidencialidad comercial: es exactamente lo que protegen las reglas de acceso |
| Archivos (certificados, fotos de campo, PDF de acreditación) | Suelen llevar datos personales; se publican sus **metadatos** (nombre, emisor, número, vigencia) |
| GPS de la inspección, técnicos en la trazabilidad pública | Ubicación exacta de fincas y personas |
| Claves, hashes, secretos de webhooks (salvo al crearlos), certificados y credenciales del SRI, XML no autorizados | Credenciales |
| Registrar socios o gestionar técnicos | Flujos con verificación humana; siguen en el portal. (Desde 19.0.1.7.0 sí se compra —`POST /verifications`, `POST /purchases`— y se suben fotos de campo, firmadas con la app) |

---

## 14. Cómo funciona por dentro (para auditoría)

* **Lecturas sin sudo**: cada búsqueda privada corre con el usuario dueño de la clave, así que las ACL y las
  `ir.rule` del portal deciden qué registros existen para él (si no lo ve, 404). El módulo añade las ACL de
  lectura que faltaban para el portal (instalaciones, piscinas, lotes, movimientos, siembras, solicitudes de
  chequeo, cobros, certificados de producto, catálogos) con reglas **de solo lectura** acotadas a
  `user.partner_id` (el mismo criterio del resto del proyecto) y que el técnico de campo pueda leer su
  verificación. Los campos de salida los decide una lista blanca por modelo (`controllers/serializers.py`).
* **Escrituras**: igual que el portal — se encuentra el registro con los permisos del usuario, se comprueba el
  papel (vendedor, comprador, técnico…) y se llama al método de negocio (`action_*`, `registrar_plan`,
  `_close`, …) en sudo con el `actor` explícito. El dueño (vendedor, cliente, emisor) se fuerza siempre al
  socio de la clave.
* **sudo con dominio fijo** solo en: endpoints públicos, lectura de nombres de relaciones (`{id, name}`),
  procedencia de un lote propio y comprobantes SRI (el portal no tiene ACL contable).
* Cada operación corre en un *savepoint*: si falla, no queda nada a medias. Los correos que disparen los
  flujos se **encolan** (no SMTP dentro de la petición).
* Último uso de la clave: como mucho una escritura por minuto y sin bloquear (`SKIP LOCKED`), en un cursor
  aparte junto con el contador del límite de peticiones (`INSERT … ON CONFLICT`).
* La página `/marketplace/product/<ref>/user-certificate/<n>` (archivo del certificado personal del vendedor)
  ya no se sirve sin sesión: solo al vendedor, a usuarios internos, a sus contrapartes y a quien podría
  comprarle ese lote.
* El QR del certificado de trazabilidad apunta a `/t/<token>` (token aleatorio y rotable desde la API
  — `POST /transactions/{id}/trace-token:rotate` — o el backend). Rotarlo invalida los QR impresos.

---

## 15. Pendiente / fuera de alcance

* `GET /verifications/{id}/report.pdf`: no existe un informe PDF propio de la verificación en el sistema; el
  bloque de verificación va dentro del certificado de trazabilidad (`GET /transactions/{id}/traceability.pdf`,
  solo comprador). Queda pendiente si se diseña ese informe.
* Subida de certificados por API: pendiente (necesitaría antivirus). Las fotos de campo ya se suben
  (§5 quater, C4: solo JPEG/PNG reales de hasta 5 MB, firmadas).
* `/register/certificates` (formulario de registro) sigue devolviendo ids enteros del **catálogo** de
  certificados: cambiarlo exige tocar el registro web de dos módulos; no son datos personales.

---

## 16. Instalación y actualización

1. Copia de seguridad de la base (y del filestore).
2. Parar el servicio: `sudo systemctl stop odoo`.
3. Instalar (como el usuario del servicio):

```bash
sudo -u odoo /opt/odoo/venv/bin/python /opt/odoo/odoo/odoo-bin -c /etc/odoo/odoo.conf -d Pruebas \
     -u shrimp_marketplace -i shrimp_api,shrimp_api_sri --stop-after-init
```
   * `-u shrimp_marketplace` recarga el módulo cuyo controlador API viejo se desactivó y cuyo QR se corrigió.
   * `shrimp_api_sri` se instalaría solo (auto_install), pero se nombra para que quede explícito.
   * Al instalar: se generan los tokens de trazabilidad de las compras existentes y las claves antiguas se
     migran a hash (la columna de texto plano queda vacía).
4. `sudo systemctl start odoo`.
5. Recomendado en `odoo.conf` si hay nginx delante: `proxy_mode = True` y `dbfilter = ^Pruebas$`.
6. Comprobar: `curl -s https://TU-DOMINIO/api/v1/public/catalogs/species` y abrir `/api/v1/docs`.

Parámetros de sistema opcionales: `shrimp_api.rate_read_per_min` (600), `shrimp_api.rate_write_per_min`
(60), `shrimp_api.public_rate_per_min` (0 = sin límite en Odoo), `shrimp_api.webhook_allow_insecure`
(solo pruebas: permite `http://` y destinos privados), `shrimp_api.app_errors_per_hour` (30),
`shrimp_api.key_lifetime_days` (365: duración por defecto y máxima de una clave),
`shrimp_api.key_expiry_notice_days` (7: aviso previo a la caducidad) y `shrimp_api.webhook_resend_days`
(7: ventana de reenvío al reactivar una suscripción). Los de la app (`shrimp_api.app_min_version`,
`app_latest_version`, `app_download_url`, `app_update_message`, `android_cert_sha256`) se editan en
Ajustes › CamaronMarket › App móvil (§5 quater).

Crons: «API externa: enviar webhooks pendientes» (cada minuto), «API externa: contadores de uso e
idempotencia» (cada 10 min), «API externa: avisar claves por caducar» (diario) y «API externa: antirreplay
de firmas de la app (limpieza)» (cada hora; borra los hashes de firma de más de 8 días, la auditoría se
conserva). Desde 19.0.1.7.0: «App móvil: borrar errores de más de 90 días» y «App móvil: borrar avisos
leídos de más de 60 días» (diarios) y «App móvil: avisar de plazos de aceptación por vencer» (cada hora).

Al actualizar a 19.0.1.7.0 no hay migración de datos: se crean las tablas de errores y avisos y cuatro
columnas en `ir_attachment` (metadatos de las fotos de la app). La versión mínima nace vacía, así que
ninguna app deja de funcionar hasta que se fije en Ajustes. Si hay nginx delante, sube
`client_max_body_size` a 8m para `/api/v1/verifications/` (fotos de campo).

Al actualizar a 19.0.1.5.0, la migración marca como claves de la app (`is_app_key`) las que se llaman
«App móvil – …» (las emitió `POST /auth/login`): desde ese momento sus operaciones sensibles exigen
dispositivo y firma, así que una versión de la app que no firme recibirá 403 `device-not-registered` en
ellas. Publica la app que firma antes (o a la vez) de actualizar el servidor.

Tests (en una base desechable, nunca en la de trabajo):

```bash
/opt/odoo/venv/bin/python /opt/odoo/odoo/odoo-bin -c /etc/odoo/odoo.conf -d shrimp_api_test_$(date +%s) \
   -i shrimp_api,shrimp_api_sri --test-tags /shrimp_api,/shrimp_api_sri --stop-after-init \
   --http-port=8097 --workers=0 --max-cron-threads=0
```

---

## Cambios de la versión 19.0.1.1.0 (coherencia de la plataforma)

* **Facturación en dos pasos.** La plataforma solo factura SUS servicios (comisión, honorario
  de verificación, comisión de empaque) y lo hace al momento de la operación (la comisión, al
  cerrarse la compra; el honorario, al iniciar la compra verificada; la comisión de empaque,
  al firmarse el acta). La factura de la **mercadería** la emite el vendedor desde su sistema
  y la registra en la compra: `GET /transactions/{id}` trae `seller_invoice`
  (`number`, `access_key`, `date`, `has_file`) — solo para las partes, nunca en la
  trazabilidad pública.
* `GET /charges` lista los cobros **que paga** la clave (antes: los del vendedor).
* `POST /harvest/commitments/{id}:buy` acepta `{"verifier": "<uuid de un verificador
  acreditado>"}` y es obligatorio en camarón adulto: la compra queda pendiente de
  verificación y de la firma de las partes, como cualquier compra de adulto.
* `POST /copack/orders/{id}:packing`: `packed_presentation` es ahora `entero`, `cola` o
  `valor_agregado`; un texto libre (integraciones antiguas) se guarda en
  `packed_presentation_note` y la respuesta trae los dos campos.
* `/api/v1/docs` carga Redoc desde el propio servidor (`/shrimp_api/static/lib/redoc/`), sin
  CDN externo.
* Sin cambios: cabeceras `X-Trazul-*`, prefijo de clave `trz_` y `User-Agent: Trazul-Webhooks/1.0`
  (contrato con los integradores).



## Deshacer una decisión (firmas de dos partes)

Una parte que aceptó, rechazó o contraofertó sin querer puede deshacer **su**
decisión mientras el proceso no haya terminado. Mismas reglas que el portal
(«Deshacer mi decisión»); la API solo llama al mismo método del modelo
(`action_signoff_undo`) con la parte autenticada como actor. Cada postura/firma
trae `can_revert` (solo `true` en la propia y mientras se pueda).

| Flujo | Endpoint | Cuándo surte efecto la decisión | Hasta cuándo se deshace |
|---|---|---|---|
| Aceptación del informe | `POST /verifications/{id}/acceptance:revert` | Al cerrarse la ronda: las dos partes decidieron o venció el plazo (un rechazo con la otra parte pendiente ya NO cancela la compra en el acto) | Mientras `acceptance_state` sea `waiting`. Una contraoferta ya respondida no se deshace. |
| Acta de empaque | `POST /copack/orders/{id}:revert-signature` | En el acto (una «no conforme» pone el acta en disputa; dos conformes la cierran y registran la comisión) | Mientras la orden esté `packed` con el acta `open` o `disputed` (no tras cerrarla ni reabrirla) |
| Cosecha fuera de banda | `POST /harvest/commitments/{id}/confirmation:revert` | En el acto (un «no la acepto» libera el compromiso; dos conformes lo cumplen) | Con el compromiso `to_confirm`, siempre; un rechazo que liberó el compromiso, solo dentro de `shrimp.signoff_undo_minutes` (15) y si el lote sigue en borrador y sin compra |

Errores: `404` si el recurso no es tuyo, `403` si eres parte pero no titular de esa firma (p. ej. el verificador) o falta el scope de escritura, `422 business-rule` si ya no se puede deshacer o no hay decisión tuya que deshacer.
Webhooks: `verification.acceptance_reverted`, `copack.signature_reverted`,
`harvest.confirmation_reverted`.

---

## Cambios de la versión 19.0.1.10.0 (auditoría de procesos: D-32, D-44, D-46)

* **D-32** — Se eliminó el controlador muerto de la API anterior (`shrimp_marketplace/controllers/api.py`).
  Si alguien lo hubiera vuelto a importar, sus reglas estáticas (`/api/v1/products`…) habrían ganado en el
  enrutador de werkzeug a la regla genérica `/api/v1/<path:subpath>` de este módulo y habrían vuelto el
  bug de `seller_role` y las claves en claro. Ver §10.
* **D-44** — Webhooks: cada intento cuenta (también los fallos internos); aviso por correo y en el portal al
  desactivarse sola; eventos retenidos (`held`) mientras está inactiva y reenvío automático de los últimos
  7 días al reactivarla; «Reenviar» por entrega en el portal (`/my/webhooks/{id}`), la API y el backend.
  Campos nuevos: `shrimp_webhook_subscription.auto_disabled_at`, `shrimp_webhook_delivery.resend_count`.
* **D-46** — Claves: caducidad por defecto de 365 días, aviso 7 días antes (correo + portal), `401` con
  `"code": "key-expired"`, botón «Renovar». La migración da **un año desde la actualización** a las claves
  sin caducidad. Campos nuevos: `shrimp_api_key.expiry_notice_sent_at`, `shrimp_api_key.renewed_at`.

---

## Cambios de la versión 19.0.1.16.0 (publicar lotes desde la app)

* **`POST /products/{id}:publish` va firmada** con la clave de la app (§5 bis): publicar es una operación
  comercial y en la app se confirma con la huella. Crear el borrador (`POST /products`), editarlo (`PATCH`) y
  retirarlo (`:archive`) no se firman. Las claves de integración (ERP) no cambian. Las versiones de la app sin
  «Publicar lote» no usan esta ruta, así que no les afecta.
* `:publish` publica con `shrimp.product.action_publish`, el mismo método que el botón «Publicar» del portal
  (suspensión, perfil rechazado, stock, vendedor y permiso «Vender» del subusuario), y además exige la etapa:
  un borrador sin etapa (la API anterior lo permitía y se conserva para los ERP) da 422 al publicarse.
* Reglas del formulario del portal que la API no aplicaba (422 `validation-error`, con `errors[].field`):
  - la etapa tiene que poder publicarla el perfil del lote (naupliero y laboratorio: nauplio y larva;
    camaronera: camarón), como `STAGE_TIPOS_POR_ROL` del portal, al crear y al cambiar la etapa;
  - presentación y talla solo en camarón (el portal las descarta en nauplio y larva; la API las rechaza);
  - la instalación de origen no puede ser una bodega ni una oficina, y sin instalación no hay piscina.
* `GET /public/catalogs/stages` devuelve `type` (`nauplio`, `larva` o `camaron`) para que la app ofrezca solo
  las etapas que el perfil publica.
* Fotos y certificados de un lote siguen siendo solo del portal (la API no los recibe).
