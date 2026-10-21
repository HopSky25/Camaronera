# Revisión de `l10n_ec_sri_community`: seguridad y fallos

Revisión del commit `6546ae6` ("Enhance SRI Community module…"), hecha el
2026-09-22/23 sobre la imagen oficial `odoo:19`: Python 3.12, `cryptography`
**41.0.7** (el paquete de Ubuntu noble), OpenSSL 3.0.13. La base de prueba es
una copia neutralizada de `postnexus`.

Cada punto indica cómo se comprobó:

- **Reproducido**: ejecutado contra el módulo sin tocar, con el resultado copiado tal cual.
- **Verificado en el código**: leído en el módulo o en el código de Odoo 19, sin ejecutarlo.

El módulo no se modificó durante la revisión. Las correcciones aplicadas después
están en [Correcciones aplicadas](#correcciones-aplicadas-rama-feat-sri-2026-10-20-versión-19014).
La propuesta que corrige casi todo esto está guardada aparte (ver
[Propuesta guardada](#propuesta-guardada)).

---

## Resumen

Tres cosas impiden hoy usar el módulo en `odoo:19`, en este orden:

1. **No se puede subir el certificado.** Desde Ajustes falla siempre (F1) y por
   cualquier otro camino falla por la versión de `cryptography` (F2).
2. **No se puede firmar ningún comprobante** en esta imagen, por la misma
   versión de `cryptography` (F2). Los tests del propio módulo lo detectan:
   fallan 5 de 6.
3. **Un cliente ecuatoriano puede salir como extranjero**, código 08, sin ningún
   aviso, si su tipo de identificación es el genérico que Odoo pone por defecto
   (F4).

En seguridad, el hallazgo concreto es S2: **crear una compañía con el
certificado lo deja en claro** (la contraseña en una columna, el `.p12` como
adjunto). Lo demás son mejoras del diseño del cifrado.

---

## Fallos

### F1. Subir el certificado desde Ajustes falla siempre — Reproducido

Aunque se escriba la contraseña, la respuesta es:

    UserError: Ingrese la contraseña del certificado para poder cifrarlo.

**Causa.** Los campos de Ajustes son `related` a la compañía
(`models/res_config_settings.py:50-55`), y Odoo 19 escribe cada campo `related`
**por separado**: `fields.py:724-733`, `_inverse_related`, hace
`target[field.name] = valor` campo a campo. `Company.write()`
(`models/config.py:38-48`) nunca recibe el archivo y la contraseña juntos: en
la escritura del archivo, `vals.pop('ec_sri_cert_password')` devuelve `None`.
El orden da igual; falla en los dos casos.

**Arreglo.** En `res.config.settings`, sustituir los `related` del archivo, el
nombre y la contraseña por campos no almacenados (`compute` + `inverse`) con un
**único** `inverse` que haga un solo `company.write({...})` con los tres
valores. Odoo agrupa los `inverse` por método y lo llama una vez con todos los
valores.

### F2. `cryptography` 41: no se puede subir ni firmar — Reproducido

    AttributeError: 'cryptography.hazmat.bindings._rust.x509.Certificate'
    object has no attribute 'not_valid_after_utc'

`not_valid_before_utc` y `not_valid_after_utc` aparecieron en `cryptography` 42,
y la imagen oficial `odoo:19` trae la 41.0.7. Se usan en:

- `models/config.py:68`: al guardar el certificado. Impide subirlo.
- `services/signature.py:37`: en **cada firma**. Impide emitir, sea cual sea el `.p12`.

La batería del propio módulo lo detecta en esta imagen:
`0 failed, 5 error(s) of 6 tests`, las cinco con este `AttributeError`.

**Arreglo.** Una función que use el atributo nuevo si existe y, si no, el antiguo
con zona UTC (en la 41 ya viene en UTC, solo le falta la zona):

    before = getattr(cert, 'not_valid_before_utc', None) or cert.not_valid_before.replace(tzinfo=timezone.utc)

Actualizar `cryptography` por `pip` no se recomienda: choca con el `pyOpenSSL`
de la imagen y afecta a todo el servidor.

### F3. Un fallo inesperado al firmar sale como error de servidor — Verificado en el código

`models/document.py:181` solo captura `(ValueError, etree.LxmlError)`. El
`AttributeError` de F2, o cualquier otro que no sea `ValueError`, llega al
usuario como un error 500 con traza, en vez de como un aviso. Conviene capturar
de forma más amplia y registrar el detalle en el log.

### F4. Un contacto con el tipo genérico "NIF/VAT" se envía como extranjero (08) — Reproducido

El módulo ya no guarda su propio tipo de identificación. Lo deriva del campo
nativo con `_l10n_ec_get_identification_type()` de `l10n_ec`, que traduce el
tipo genérico `l10n_latam_base.it_vat` a `'foreign'`. Ese es **el tipo por
defecto de cualquier contacto nuevo**:

    tipo por defecto de un contacto nuevo: NIF | codigo SRI enviado: 08

Es decir, un cliente con RUC ecuatoriano de 13 dígitos, creado sin cambiar el
tipo, sale en el XML como "identificación del exterior", y **no hay ningún
error** que lo delate.

**Arreglo.** Para el tipo genérico, deducir por el número (13 dígitos → RUC 04,
10 → cédula 05), como hacía antes el `post_init_hook`, o negarse a emitir
pidiendo que se elija el tipo.

### F5. Actualizar desde la versión anterior pierde los puntos de emisión — Verificado en el código y en una base real

No hay carpeta `migrations/`, y la versión sigue en `19.0.1.0.0`, la misma que
el commit anterior (`89907c7`), así que Odoo ni siquiera propone actualizar.
Forzando un `-u`:

- Los campos `document_type`, `address` y `sequence_id` salen de `ec.sri.point`.
  **Odoo 19 borra la columna de un campo que desaparece del modelo**
  (`ir.model.fields._drop_column`): comprobado en `postnexus`, donde
  `ec_sri_secret_name` y `ec_sri_identification_type` desaparecieron al
  actualizar.
- Los puntos existentes se quedan sin `establishment_id`, que ahora es
  obligatorio, y sin líneas. Pierden el enlace con su secuencia, y con él la
  **continuidad del secuencial**: el SRI rechaza un número repetido.

**Arreglo.** Subir la versión y añadir un `pre-migrate` que copie los datos
viejos antes de que Odoo borre las columnas, y un `post-migrate` que cree el
establecimiento, agrupe los puntos que ahora son uno (la nueva unicidad es
establecimiento + punto + ambiente) y cree una línea por documento
**reutilizando la secuencia existente**.

### F6. El tipo de identificación que había deducido el hook se pierde — Verificado en una base real

Mismo mecanismo que F5: el campo `ec_sri_identification_type` desaparece y Odoo
borra su columna. Lo que la versión anterior había deducido (RUC o cédula por
la longitud) se pierde, y esos contactos pasan a depender del tipo nativo, que
a menudo es el genérico: se cae en F4.

### F7. Un punto de emisión sin documentos se puede guardar, y el error no lo dice — Reproducido

    punto creado con 0 tipos de documento

Nada impide guardar un punto sin líneas en "Documentos que emite". Al emitir,
el error es genérico:

    Seleccione el punto correcto para empresa, documento y ambiente.   (document.py:163)

Ese mensaje cubre cuatro condiciones distintas (compañía, documento, ambiente,
punto vacío), y no dice cuál falla. Pasó en esta prueba: se creó un punto sin
líneas y el mensaje no apuntaba a la causa.

**Arreglo.** Un mensaje por condición ("El punto X no tiene habilitada la
Factura: agréguela en Documentos que emite") y, opcionalmente, exigir al menos
una línea al guardar.

### F8. Un comprobante manual se puede ligar a cualquier asiento — Verificado en el código

`move_id` (`models/document.py:30`, vista `document_views.xml:40`) no tiene
dominio, así que acepta un asiento contable que no es una factura. Pasó en esta
prueba: un comprobante "Factura" quedó ligado a un asiento en borrador sin
número, que no se puede emitir. Conviene un dominio por `move_type` según el
tipo de documento, y la misma comprobación en el servidor.

### F9. `scripts/configurar_sri.py` quedó roto — Verificado en el código

Sigue escribiendo `ec_sri_secret_name` (línea 79), que ya no existe, y crea
puntos con `document_type` y `address` (líneas 118-119), que ahora son de otras
tablas. También comprueba la variable `*_P12_PATH` del esquema anterior (135).
Falla en la primera escritura.

### F10. El README describe cosas que ya no son así — Verificado en el código

- La línea 44 dice que el hook deduce el tipo de identificación; ya no lo hace.
- La línea 88 pone como ejemplo de llave `"una-frase-larga-secreta-y-ESTABLE"`: ver S1.

### F11. Los datos del contribuyente no se validan antes de ir al XML — Verificado en el código

`services/builders.py:20-34` copia tal cual contribuyente especial, agente de
retención y gran contribuyente. En esta prueba se había escrito **"NO"** en los
tres, y eso produce `<agenteRetencion>NO</agenteRetencion>` y
`<contribuyenteEspecial>NO</contribuyenteEspecial>`, donde el SRI espera un
número de resolución. Conviene validar el formato al guardar y explicar en la
ayuda del campo que va vacío si no aplica.

### F12. Los tests no cubren el certificado — Verificado en el código

Los 6 tests sustituyen `_ec_sri_certificate` por una función de prueba, así que
ni la subida (F1), ni el cifrado, ni el caso de S2 tienen cobertura. F2 sí
aparece, pero solo porque la firma se ejecuta de verdad.

### F13. Un comprobante que falla el primer día no se puede emitir nunca en producción — Reproducido en test

Si la firma fallaba (certificado, datos del cliente), el savepoint deshacía el
`ec.sri.document` con su clave. El reintento lo creaba otra vez con la fecha de
la factura (`account_move.py`, `_ec_sri_open_document`) y la regla de
producción «fecha de emisión = hoy» (`document.py`, `action_queue`) lo
rechazaba para siempre desde el día siguiente.

### F14. El cliente nunca recibe la factura electrónica — Verificado en el código

Al autorizarse el comprobante no se enviaba nada al cliente: ni el RIDE ni el
XML autorizado, que es el que tiene validez tributaria.

### F15. Un fallo del webhook deja «Requiere revisión» un comprobante autorizado — Verificado en el código

`shrimp_api_sri` emite `invoice.authorized` dentro de `_set()`, sin savepoint.
Si fallaba, la excepción llegaba a `_cron_process` y el comprobante, ya
AUTORIZADO por el SRI, quedaba en `error` («Error interno»).

### F16. El RIDE usa formato `%` en QWeb — Verificado en el código

`report/ride.xml` armaba la URL del código de barras con `'...%s...' % clave`.
Odoo 19 vuelve a formatear las expresiones al compilar la plantilla y el `%`
revienta con «incomplete format».

### F17. Un punto de producción salta los secuenciales usados en pruebas — Verificado en el código

`_ec_sri_skip_used` (`config.py`) contaba las facturas de cualquier ambiente:
al crear el punto de producción 001-001 empezaba después del último número de
pruebas, que el SRI de producción no conoce.

---

## Correcciones aplicadas (rama `feat-sri`, 2026-10-20, versión 19.0.1.4.0)

Cambios mínimos y comentados en el código. Tests nuevos en
`tests/test_emission.py` y `tests/test_configurar_sri.py` (base común en
`tests/common.py`, SRI simulado, sin red).

- **F1 — corregido.** `models/res_config_settings.py`: el archivo, su nombre y
  la contraseña dejan de ser `related`; son campos no almacenados con un único
  `inverse` que llama una vez a `res.company.write()` con archivo y contraseña
  juntos. No se guardan en la tabla de Ajustes. Test:
  `test_upload_certificate_from_settings`.
- **F9 — corregido.** `scripts/configurar_sri.py` reescrito para los modelos
  1.3/1.4: establecimiento + punto + líneas 01/04, diario y punto de la
  plataforma, SRI apagado salvo `--activar`, pruebas por defecto, lista de
  pendientes (incluye el .p12 por Ajustes y `EC_SRI_MASTER_KEY`). Corre en
  `odoo-bin shell` (comando en el docstring).
- **F13 — corregido.** La clave de acceso (con su fecha de emisión) se asigna y
  graba antes de firmar (`document.py`, `_ec_sri_reserve_key`), y los procesos
  automáticos usan `account.move._ec_sri_emit_keep()`, que prepara y firma en
  savepoints separados: si la firma falla, el comprobante queda en Borrador con
  su clave y el motivo, y el reintento lo reutiliza. Para un comprobante que
  nunca obtuvo clave ni se transmitió y cuya fecha ya no es la de hoy, el
  formulario (comprobante y factura) muestra «Fecha vencida: emitir de nuevo
  con fecha de hoy» y el botón **Emitir con fecha de hoy**
  (`action_redate_today`): la factura vuelve a borrador, toma la fecha de hoy
  (conserva número y conciliaciones), se contabiliza y el comprobante se encola.
  Nunca si algo se transmitió (`was_sent` o cualquier evento no local).
- **F14 — corregido.** Al autorizarse una factura o nota de crédito se encola
  (`mail.mail`, sin envío inmediato) un correo al cliente y a su contacto de
  facturación con el RIDE y el XML autorizado (`data/mail_template_data.xml`).
  Una sola vez (`customer_mail_sent`), en savepoint propio: un fallo queda en el
  historial y no cambia el estado. Botón **Enviar al cliente** para reenviar.
- **F15 — corregido** en `shrimp_api_sri` (savepoint y log).
- **F16 — corregido** (`str.format()`); test que renderiza el RIDE.
- **F17 — corregido.** Solo cuentan las facturas del mismo ambiente (o de
  ambiente desconocido) y, por la unicidad de nombre de Odoo, las de cualquier
  ambiente en el mismo diario; al numerar se saltan también los números
  ocupados en el diario de la factura. El aviso de número repetido aplica el
  mismo criterio.

Siguen abiertos: F4 (parcial), F5–F8, F10, F11 y S1, S3–S8.

---

## Seguridad

### Lo que está bien

- Fernet es cifrado autenticado (AES-128-CBC + HMAC-SHA256): si se altera el valor cifrado, falla.
- Hay un salt aleatorio por compañía, y la llave maestra vive fuera de la base.
- Los campos llevan `groups='base.group_system'` y `copy=False`.
- Contra la amenaza principal —**un dump de la base que se filtra**— funciona,
  *siempre que la llave sea fuerte y no viaje con el dump*.

### S1. La llave es una frase, y eso la hace adivinable — Verificado en el código

`services/crypto.py:25` acepta cualquier texto, y el README anima a usar una
frase. Con un dump, quien lo tenga tiene el salt y el texto cifrado, y prueba
candidatos **sin límite y sin que nadie lo note**. Con 390 000 iteraciones de
PBKDF2, una GPU de gama alta prueba del orden de diez mil por segundo: mil
millones de frases "largas" caen en horas o pocos días.

**Recomendación.** Exigir 32 bytes aleatorios en base64 y rechazar lo demás al
usarla:

    openssl rand -base64 32

Con 256 bits aleatorios no hay nada que adivinar, y el KDF lento sobra: HKDF
basta, y ahorra las dos derivaciones de 390 000 iteraciones que hoy se hacen en
cada firma.

### S2. Crear una compañía con el certificado lo deja en claro — Reproducido

`write()` saca el archivo y la contraseña de `vals` antes de guardar
(`models/config.py:42-43`), pero `create()` no está sobrescrito. Los campos son
almacenados: la contraseña es un `Char` con columna (línea 30) y el archivo un
`Binary`, que Odoo guarda en `ir.attachment` (línea 27). Resultado de crear
una compañía con ellos:

    contrasena en la columna: 'ClaveSecreta123' | .p12 como adjunto: True (2450 bytes, igual al original: True)

No es el camino de la interfaz, pero sí el de una importación, XML-RPC o un
script.

**Recomendación.** Que el archivo y la contraseña sean campos no almacenados
(`compute` + `inverse`) en la compañía también, y una migración que borre la
columna `ec_sri_cert_password` y los adjuntos que ya existan. Odoo no borra solo
la columna de un campo que sigue existiendo aunque deje de almacenarse.

### S3. Sin datos asociados: el cifrado de una compañía se puede pegar en otra — Verificado en el código

El valor cifrado no está atado a la compañía ni al campo. Con acceso de
escritura a la base, copiar `ec_sri_cert_data`, `ec_sri_cert_password_enc` y
`ec_sri_cert_salt` de la compañía A a la B hace que **B firme con el certificado
de A**, sin ningún error.

**Recomendación.** AES-256-GCM con datos asociados (AAD) que incluyan el id de
compañía y el campo (`p12` / `password`). Así el descifrado falla si se mueven.

### S4. Sin identificador de llave: no hay forma de rotarla — Verificado en el código

No se guarda con qué llave se cifró cada valor. Cambiarla obliga a volver a
subir todos los certificados a mano, y el error no dice qué llave falta.

**Recomendación.** Prefijo de versión y de llave en cada valor
(`v1:<id>:<datos>`), y una variable `EC_SRI_MASTER_KEY_OLD` para descifrar con
la anterior y recifrar con la nueva al usar el certificado.

### S5. La llave solo se acepta como valor en el entorno — Verificado en el código

Un valor en el entorno queda en claro en `docker inspect` y en
`/proc/<pid>/environ`. Para no exponerla, en esta instancia hizo falta un
envoltorio del entrypoint (`instancias/sri/cargar_llave.sh` en `odoo_template`).

**Recomendación.** Admitir la convención `EC_SRI_MASTER_KEY_FILE` de Docker:
una ruta a un archivo, que se monta como `secret` de Compose.

### S6. El certificado viaja en los backups, y `--neutralizar` no lo quita — Verificado en el código

Con este diseño, el certificado cifrado va dentro de cada dump. Al restaurar una
copia de producción en otra máquina con la **misma** llave, esa copia puede
firmar comprobantes reales.

**Recomendación.** Una llave distinta por entorno (producción, pruebas,
portátil) y un `data/neutralize.sql` en el módulo que vacíe los campos del
certificado. Odoo ejecuta ese archivo al neutralizar una base.

### S7. La subida no aplica las exigencias de la firma — Verificado en el código

`_ec_sri_store_certificate` solo comprueba que el `.p12` se abra. No mira que la
clave sea RSA de al menos 2048 bits, que el certificado esté vigente ni que
permita firmar, cosas que sí exige `sign_xml`. Se acepta un certificado que
luego no firma, y el fallo aparece en la primera factura.

**Recomendación.** Una sola función de carga y validación del `.p12`, usada por
la subida y por la firma.

### S8. Detalles menores — Verificado en el código

- **Sin límite de tamaño en la subida.** La versión anterior limitaba a 1 MB.
- **Errores sin capturar al descifrar.** `crypto.decrypt` solo captura
  `InvalidToken` (línea 42): un valor en base64 dañado da `binascii.Error` y
  sale como error 500.

### Lo que ningún cifrado resuelve

Quien pueda ejecutar código como el proceso de Odoo (un módulo malicioso, el
`odoo shell`, acceso al servidor) puede descifrar. Quien sea administrador
contable puede firmar, aunque no pueda extraer el `.p12`. El cifrado protege
los datos **en reposo**, no un servidor comprometido.

---

## Nota de operación (no es del módulo)

Al rehacer una instancia local con el mismo puerto y el mismo nombre de base,
el navegador sigue usando las vistas guardadas de la instancia anterior. Se ve
como `Invalid field 'document_type' on 'ec.sri.point'` o
`KeyError: 'ec_sri_secret_name'`, campos que ya no existen en el servidor.
Solución: `F12` → Application → Storage → **Clear site data**. Un `Ctrl+F5` no
basta.

---

## Propuesta guardada

Se implementó una corrección de F1, F2, F9, F10, F12 y S1 a S8, con 27 tests
que pasaron en `odoo:19` con `cryptography` 41. Añade además un botón
*Verificar certificado* en Ajustes, que firma un comprobante de prueba sin
enviar nada al SRI. Se descartó del árbol de trabajo para probar el módulo sin tocar, pero sigue
guardada en el stash de este repositorio:

    git stash list                    # stash@{0}: propuesta: cifrado AES-256-GCM...
    git stash show -p stash@{0}       # ver el diff
    git stash pop                     # recuperarla
    git stash drop stash@{0}          # tirarla

No cubre F3, F4, F5, F6, F7, F8 ni F11.
