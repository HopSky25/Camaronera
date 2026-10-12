# Alcance de la app CamaronMarket por perfil

Una sola app para todos los perfiles de la plataforma. Cada cosa que se ve o se hace en la app está
respaldada por un endpoint real de `shrimp_api` (`/api/v1`). Lo que la API no permite (comprar sin
verificación, registrar socios, subir fotos o archivos, publicar listas de precios, gestionar técnicos...)
NO va en la app: en su lugar se muestra el aviso "Esto se hace desde el portal web".

## Entrada y perfiles

| Paso | Qué hace la app | Endpoint |
|---|---|---|
| Entrar por cuenta | Servidor (por defecto `https://demo.camaronmarket.com`), correo y contraseña. Guarda la clave cifrada (flutter_secure_storage) y `me` en SQLite para abrir sin señal. `device` = marca y modelo del teléfono. | `POST /auth/login {"login","password","device"}` → `{api_key, expires_at, me}` |
| Entrar con clave de API (opción avanzada) | Para técnicos con clave propia o integraciones. | `GET /me` |
| Elegir perfil | Lee `me.roles`. Un perfil aprobado → entra directo. Dos o más → pantalla "¿Con qué perfil quieres trabajar?" (los pendientes se ven deshabilitados). El técnico de campo (`is_field_technician` y `company`) entra directo al flujo del técnico. | `me.roles`, `me.active_role` |
| "Actuar como" | El perfil elegido se guarda en el teléfono y va en la cabecera `X-Shrimp-Role` de **cada** petición. No se llama a `POST /me/active-role` (cambiaría los menús del portal de la misma cuenta). Valor inicial: `me.active_role`. | cabecera `X-Shrimp-Role` |
| Cambiar de perfil | Desde el menú de la cuenta o el botón "Cambiar" de la cabecera. | local |
| Mis datos y privacidad | Abre `https://<servidor>/my/datos` en el navegador. | portal |
| Cerrar sesión | Revoca la clave y borra todo lo guardado en el teléfono. | `POST /auth/logout` |
| 401 (clave caducada o revocada) | Vuelve a la pantalla de entrada con el motivo; NO se borra lo capturado (la cola se envía al volver a entrar). | — |
| 403 | Mensaje claro ("Tu clave no tiene permiso...", o el `detail` del servidor); en la cola queda a la vista para corregir o descartar. | — |

## Datos y red

* Técnico y verificadora: bandeja local en SQLite (`verificaciones`), funciona sin internet.
* Camaronera / semillero / laboratorio / empacadora / maquilador: trabajan en línea; la última respuesta de
  cada lista y detalle queda en SQLite (`cache_listas`) y se muestra sin señal con el aviso
  "Datos de la última sincronización".
* Todas las acciones (aceptar informe, programar despacho, ofertar, comprar...) van por la misma cola
  (`cola`) con `Idempotency-Key` (UUID v4) y se envían en orden cuando hay señal.
* Paginación con `limit` (100) y `cursor` de la API ("Cargar más").

## Tabla por perfil

| Perfil | Qué ve en el panel | Qué puede hacer en la app | Qué se hace solo en la web | Endpoints usados |
|---|---|---|---|---|
| **Camaronera** (y **semillero**, **laboratorio**: mismo panel de vendedor) | Lotes publicados con unidades disponibles · ventas en curso (por verificar / por aceptar / confirmadas) · informes por aceptar · próximos despachos | Ver mis lotes (publicados, borrador, vendidos) y su detalle · ver mis ventas con estado y trazabilidad (texto + enlace público) · ver informes de verificación y **aceptar / rechazar** · ver despachos y **programar / editar** la cita (cosecha, salida, ETA, transportista, placa, teléfono) | Publicar o editar lotes, precios, certificados, fotos · contraofertar el informe · declarar cosechas, reservas y confirmaciones · fincas y piscinas · evoluciones | `GET /products`, `GET /products/{id}`, `GET /transactions?role=seller`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `GET /verifications?role=seller`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance {decision, reason}`, `GET /dispatches`, `GET /dispatches/{id}`, `PATCH /dispatches/{id}` |
| **Empacadora** | Compras en curso · informes por aceptar · llegadas previstas hoy · proveedores en el ranking y **top 3** | Catálogo de lotes publicados con búsqueda y **comprar con verificación** (cantidad, modo plataforma/declarada, verificadora del directorio) · mis compras con estado y trazabilidad y **concluir la compra** · informes (aceptar / rechazar) · despachos (solo ver) · reservas de cosecha (ver) y **comprar el lote reservado** · ranking completo con orden · listas de precios (lectura) | Comprar lotes sin verificación · contraofertar · publicar o editar listas de precios · nuevas reservas, retirar o desistir · pedir maquila · facturas, certificado PDF y pagos | `GET /public/products`, `GET /public/products/{id}`, `GET /public/directory/verifiers`, `POST /verifications {product, qty, verification_mode, verifier}`, `GET /transactions?role=buyer`, `GET /transactions/{id}`, `GET /transactions/{id}/traceability`, `POST /transactions/{id}:complete`, `GET /verifications?role=buyer`, `GET /verifications/{id}`, `POST /verifications/{id}/acceptance`, `GET /dispatches`, `GET /dispatches/{id}`, `GET /harvest/commitments`, `GET /harvest/commitments/{id}`, `POST /harvest/commitments/{id}:buy {verifier, verification_mode}`, `GET /suppliers/ranking?order=`, `GET /price-lists?role=issuer`, `GET /price-lists/{id}` |
| **Verificadora (administradora)** | Bandeja de la empresa (abiertas / en campo / cerradas), cuántas están sin técnico | **Asignar o cambiar técnico** (lista de `GET /me/technicians`; si falla, los técnicos ya vistos en la bandeja o pegar el identificador) · iniciar campo, informe de campo, llegada del camión, fotos y veredicto reutilizando las pantallas del técnico · todo funciona sin internet | Dar de alta o quitar técnicos (Mi equipo) · acreditación · subir fotos al servidor | `GET /verifications?role=verifier`, `GET /verifications/{id}`, `GET /me/technicians`, `POST /verifications/{id}:assign {technician}`, `POST /verifications/{id}:start`, `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `POST /verifications/{id}:verdict` |
| **Técnico de campo** | Lo mismo que antes, sin regresiones: bandeja, informe sin internet, cola de envío, fotos, veredicto | Iniciar campo · informe de campo (pesos, clasificación, conteos, metabisulfito, sabor, gramajes, GPS) · llegada del camión · fotos (solo en el teléfono) · veredicto | Subir fotos (la API no recibe archivos) | `GET /verifications?role=technician`, `POST /verifications/{id}:start`, `PATCH /verifications/{id}/report`, `POST /dispatches/{id}/arrival`, `POST /verifications/{id}:verdict` |
| **Maquilador** | Solicitudes abiertas (y cuántas sin tu oferta) · órdenes en curso (por recibir / por empacar) · actas por firmar · tarifas vigentes | Ver solicitudes y **ofertar / cambiar la oferta** (tarifa, capacidad, fechas, notas) · órdenes: **registrar recepción** (libras, insumos), **registrar empaque** (libras, cajas), **firmar el acta** (conforme / no conforme con motivo) · tarifas (lectura) | Retirar una oferta · deshacer firma, reabrir acta, cancelar orden · crear, publicar o archivar tarifas · empaque propio (`POST /copack/orders`) | `GET /copack/requests?state=`, `POST /copack/requests/{id}/offers`, `GET /copack/orders`, `GET /copack/orders/{id}`, `POST /copack/orders/{id}:reception`, `POST /copack/orders/{id}:packing`, `POST /copack/orders/{id}:sign`, `GET /copack/tariffs`, `GET /copack/tariffs/{id}` |

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
