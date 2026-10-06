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
2. «Crear una clave»: nombre, caducidad (30, 90, 180 días o 1 año), los **scopes** que la integración necesita
   (solo esos), y opcionalmente:
   * **IPs permitidas** (una IP o red CIDR por línea): si se rellena, cualquier otra IP recibe 403.
   * **Orígenes CORS**: solo si vas a llamar desde un navegador (ver §9).
3. La clave se muestra **una sola vez**: `trz_<prefijo>_<secreto>`. La plataforma guarda solo el prefijo y un
   hash PBKDF2-SHA512; si la pierdes, revócala y crea otra.
4. Revocar es inmediato (botón «Revocar» en el portal, o el administrador desde
   *Camaronera → Configuración → Integraciones (API) → Claves de API*).

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
| 401 `expired-api-key` / `revoked-api-key` | caducada / revocada |
| 403 `user-not-allowed` | el usuario dueño está inactivo o no es de portal |
| 403 `ip-not-allowed` | la IP no está en la lista de la clave |
| 403 `insufficient-scope` | falta el scope; `detail` dice cuál |

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
| 401 / 403 | ver §3, `access-denied`, `forbidden` | autenticación / permisos / papel |
| 404 | `not-found` | no existe **o no es tuyo** (no se distingue a propósito) |
| 405 | `method-not-allowed` | cabecera `Allow` con los métodos válidos |
| 409 | `invalid-state`, `conflict`, `idempotency-in-progress` | el recurso no está en el estado necesario |
| 413 / 415 | `payload-too-large`, `unsupported-media-type` | cuerpo > 1 MB / no es JSON |
| 422 | `validation-error`, `business-rule`, `idempotency-key-reused` | datos inválidos o regla de negocio (el `detail` es el mensaje del negocio) |
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
la entrega queda `dead` (visible en `GET /webhooks/{id}/deliveries` y en el backend, con botón
«Reintentar»). 50 entregas muertas seguidas desactivan la suscripción. La entrega es *al menos una vez*:
deduplica por `id` (o por `X-Trazul-Delivery`).

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

### 11.6 LIMS de laboratorio / semillero (scopes `products:*`, `lots:*`, `transactions:read`, `facilities:*`)

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

| Paso | Endpoint |
|---|---|
| Bandeja | `GET /verifications?role=verifier` (admin) o `?role=technician` |
| Asignar técnico (admin) | `POST /verifications/{id}:assign` `{"technician": "<uuid del técnico>"}` |
| Iniciar campo | `POST /verifications/{id}:start` |
| Cargar el informe (parcial, desde el móvil) | `PATCH /verifications/{id}/report` (pesos, presentación, metabisulfito, sabor, `lines`, `counts`, GPS) |
| Estampar la llegada del camión | `POST /dispatches/{id}/arrival` |
| Veredicto | `POST /verifications/{id}:verdict` `{"verdict": "approve|approve_obs|reject", "notes": "…"}` |

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

### Privados
* Perfil: `GET /me`, `PATCH /me`
* Instalaciones: `GET|POST /facilities`, `GET|PATCH|PUT|DELETE /facilities/{id}`; piscinas: ídem en `/ponds`
* Productos: `GET|POST /products`, `GET|PATCH|PUT|DELETE /products/{id}`, `POST /products/{id}:publish|:archive|:unarchive`, `GET|POST /products/{id}/evolutions`
* Lotes: `GET /lots`, `GET /lots/{id}`, `GET /lots/{id}/moves`, `GET|POST /lots/{id}/allocations`
* Compras/ventas: `GET /transactions`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /transactions/{id}/traceability.pdf`, `POST /transactions/{id}:receive|:complete`, `POST /transactions/{id}/trace-token:rotate`, `GET /charges`
* Chequeo: `GET /check-requests`, `GET /check-requests/{id}`, `POST /check-requests/{id}:approve|:reject`
* Verificación: `GET /verifications`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance`, `POST /verifications/{id}/acceptance:revert`, `POST /verifications/{id}:assign|:start|:verdict`, `PATCH /verifications/{id}/report`, `GET /suppliers/ranking`
* Despacho: `GET /dispatches`, `GET /dispatches/{id}`, `PATCH /dispatches/{id}`, `POST /dispatches/{id}/arrival`
* Listas de precios: `GET|POST /price-lists`, `GET|PUT /price-lists/{id}`, `POST /price-lists/{id}:publish|:archive`, `GET /lot-alerts`
* Reserva: `GET|POST /harvest/forecasts`, `GET /harvest/forecasts/{id}`, `POST /harvest/forecasts/{id}:publish|:cancel|:harvest`, `POST /harvest/forecasts/{id}/commitments`, `GET /harvest/commitments`, `GET /harvest/commitments/{id}`, `POST /harvest/commitments/{id}:accept|:reject|:withdraw|:desist|:buy`, `POST /harvest/commitments/{id}/confirmation`, `POST /harvest/commitments/{id}/confirmation:revert`
* Maquila: `GET|POST /copack/requests`, `GET /copack/requests/{id}`, `POST /copack/requests/{id}:cancel`, `POST /copack/requests/{id}/offers`, `POST /copack/offers/{id}:accept|:withdraw`, `GET /copack/orders`, `GET /copack/orders/{id}`, `POST /copack/orders/{id}:reception|:packing|:sign|:revert-signature|:reopen|:close|:cancel`, `GET|POST /copack/tariffs`, `GET|PUT /copack/tariffs/{id}`, `POST /copack/tariffs/{id}:publish|:archive`
* Webhooks: `GET /webhooks/events`, `GET|POST /webhooks`, `GET|PATCH|DELETE /webhooks/{id}`, `POST /webhooks/{id}:ping|:rotate-secret`, `GET /webhooks/{id}/deliveries`
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
| Comprar desde la API, registrar socios, gestionar técnicos o subir fotos | Flujos con verificación humana, pagos o archivos; siguen en el portal |

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
* Comprar por API (`POST` de compra con/sin verificación): no se expuso a propósito (pagos y elección de
  verificador con validación humana).
* Subida de archivos (fotos de campo, certificados): pendiente; necesitaría antivirus y control de tamaño.
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
(solo pruebas: permite `http://` y destinos privados).

Crons: «API externa: enviar webhooks pendientes» (cada minuto) y «API externa: contadores de uso e
idempotencia» (cada 10 min).

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
