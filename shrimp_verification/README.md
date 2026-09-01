# shrimp_verification — Verificación de camarón en campo

Introduce la figura del **verificador**: un tercero acreditado que inspecciona el
camarón antes de que la compra se concrete.

## El flujo

    COMPRADOR                 VERIFICADOR                  COMPRADOR
    compra + elige       →    bandeja → campo →       →    alerta →
    verificador               informe → veredicto          concluye compra
       ↓                           ↓                            ↓
    tx: pending_verification   verificación: approved       tx: confirmed
    stock RESERVADO            (o rejected)                 stock CONSUMIDO

La clave está en la reserva: al comprar **no se consumen lotes**, solo se
reserva la cantidad. Así el producto no se vende dos veces mientras el
verificador está en campo, y si rechaza, la reserva se libera sin haber tocado
la trazabilidad.

## El verificador en el registro

En `/registro` el rol **Verificador** tiene su propio bloque en el paso 3
"Campos por tipo": razón social, responsable técnico, teléfono, base de
operaciones, zona de cobertura y n.º de registro o licencia.

Ojo con dos cosas del formulario base:

- Las opciones del combo del paso 1 están **escritas a mano** en la plantilla
  (`shrimp_user_registry/views/templates.xml`), no leídas del selection del
  campo. Añadir un rol al modelo no basta: hay que añadir la opción también ahí.
- El JS del registro muestra y oculta los bloques por id, y no conoce esta
  sección, así que este módulo aporta su propio script para el rol verificador.
  El bloque de certificados sí se integra solo, porque `refreshCertTables()`
  itera por `data-role`.

Para no duplicar las ~200 líneas de `registro_submit`, se añadió al módulo base
un hook `_extra_partner_vals(user_type, post)` que este módulo sobrescribe.

### Acreditación obligatoria

Un verificador **no puede registrarse sin adjuntar la acreditación** que lo
autoriza: el hook lo rechaza si en el formulario no viene un certificado cuyo
`role` sea `verificador`. El catálogo trae dos de fábrica ("Acreditación de
Verificador" y "Habilitación de laboratorio de análisis").

La acreditación llega en estado **pendiente**; alguien del backend debe
aprobarla. Solo entonces `verifier_is_accredited` pasa a verdadero.

## Dónde se aprueba un verificador

Menú **Verificación → Acreditaciones por aprobar**. Es la bandeja del
administrador: lista las acreditaciones de rol verificador, filtrada por
defecto a las pendientes, con tres botones por fila — **ver documento**,
**aprobar** y **rechazar**.

Existe además **Verificación → Verificadores**, con la ficha completa: datos de
la empresa, aviso en rojo o verde según esté acreditado, pestaña de
acreditaciones (con los mismos botones) y pestaña con sus verificaciones.

Al aprobar, tres cosas ocurren de golpe: el verificador pasa a `acreditado`,
aparece con el ojo en el selector del comprador, y recibe un correo avisándole
de que ya está disponible.

`shrimp_marketplace` ya tenía una pantalla genérica de aprobación de
certificados (**Aprobaciones → Certificados de usuario**) que también sirve;
esta bandeja es la misma tabla filtrada al rol verificador, para no obligar a
buscar entre los certificados de semilleros y camaroneras.

## El "ojito" del comprador

Al elegir verificador, el comprador ve una **lista** (no un `<select>`: un
`<option>` no admite enlaces) con nombre, calificación, zona y un distintivo de
acreditado o no acreditado. Los acreditados llevan un icono de ojo que abre el
documento:

    /marketplace/verificador/<uuid_ref>/acreditacion

Requiere sesión iniciada, y responde 404 si el verificador no tiene una
acreditación aprobada y vigente. Con `?download=1` fuerza la descarga.

**Solo se ofrecen verificadores acreditados.** `available_verifiers()` filtra por
`verifier_is_accredited`, y `start_verified_purchase()` lo revalida en el
servidor, porque el POST del formulario es falsificable. Si no hay ninguno
acreditado, la página avisa de que el administrador debe aprobar acreditaciones.

## Alcance

Solo aplica al **camarón adulto** (vendedor de tipo camaronera). Las larvas
(semillero → laboratorio → camaronera) siguen el flujo directo de siempre:
a una larva no se le mide metabisulfito ni sabor.

Lo decide `shrimp.product.requires_verification`, calculado a partir del tipo
del vendedor.

## Los cinco análisis

| # | Análisis | Campos |
|---|---|---|
| 1 | **Peso** | enviado, en planta, basura → sobrepeso, factor y peso neto (calculados) |
| 2 | **Cuerpo o cola** | presentación, con aviso si no coincide con lo publicado |
| 3 | **Metabisulfito** | ppm medido contra el límite; conforme / no conforme |
| 4 | **Clasificación** | líneas clase (A/B/C) + talla + libras → rendimientos calculados |
| 5 | **Sabor** | resultado, olor y color, notas de cata |

Más gramajes, conteos, fotos de campo, GPS e incidencias.

## Fórmulas (verificadas contra partes reales)

    peso neto        = peso en planta − basura
    rendimiento %    = total procesado / peso neto × 100
    rend. clase X %  = libras de la clase X / total procesado × 100
    sobrepeso        = peso en planta − peso enviado
    factor           = peso en planta / peso enviado

Dos cosas que no son obvias y que hay que respetar:

- El rendimiento va sobre el **peso neto**, no sobre el peso de planta. Con dos
  partes reales da 66,70 % y 67,22 % frente a los 66,71 % y 67,21 % declarados;
  calculado sobre el peso de planta daría 66,67 % y 66,95 %, que no cuadran.
- El **factor de sobrepeso es un ratio, no un porcentaje**. En los partes se
  escribe "1.05%" pero significa 1,05 = 5 % de sobrepeso. Además se **trunca**,
  no se redondea: 1,0575 se escribe 1,05.

## El portal del verificador

Un verificador no publica lotes, no compra, no tiene piscinas ni inventario, así
que **no ve el panel de vendedor/comprador**. Este módulo lo sustituye por uno
propio, tanto en `/my` como en el desplegable "Mi panel" de la barra superior:

| Tarjeta | Va a |
|---|---|
| Compras por verificar | `/verificador/bandeja?state=assigned` |
| En campo | `/verificador/bandeja?state=in_field` |
| Por dictaminar | `/verificador/bandeja?state=done` |
| Ya verificadas | `/verificador/bandeja?state=approved` |
| Rechazadas | `/verificador/bandeja?state=rejected` |
| Todas mis verificaciones | `/verificador/bandeja` |
| Mi acreditación | `/marketplace/mis-certificados` |
| Mi cuenta | `/marketplace/mi-cuenta` |

Cada tarjeta lleva su contador real. Si la acreditación aún no está aprobada,
el panel lo avisa arriba: sin ella no recibirá asignaciones.

El formulario donde se hace el trabajo (los cinco análisis) está dentro de cada
verificación, al abrirla desde la bandeja.

Detalle al heredar el desplegable de `shrimp_marketplace`: su `<ul>` usa
`t-attf-class`, no `class`, así que `hasclass('mp-menu')` **no lo encuentra**;
hay que buscarlo con `contains(@t-attf-class, 'mp-menu')`.

## Qué se puede ver según el estado de la compra

| Acción | Pendiente de verificación | Cancelada | Confirmada / completada |
|---|---|---|---|
| Ver comprobante | ✗ | ✗ | ✓ |
| Ver trazabilidad | ✓ | ✓ | ✓ |
| Certificado PDF | ✗ | ✗ | ✓ |
| Ver verificación | ✓ | — | — |

El **certificado PDF** es el de trazabilidad, no un comprobante de pago. Mientras
la compra está pendiente no se han consumido lotes ni se han creado movimientos
de stock, así que ese certificado saldría sin cadena de custodia: estaría
certificando una trazabilidad que todavía no existe. Por eso se oculta hasta que
la compra se concrete.

La **pantalla de trazabilidad sí se muestra siempre**, e incluye un bloque
«Verificación en campo» con el estado, el verificador y los resultados —los cinco
análisis si es camarón adulto, o cantidad, supervivencia, tamaño y sanidad si es
larva—, además de las incidencias y la conclusión del verificador.

## La verificación dentro de la trazabilidad

El verificador **no sube un PDF**: llena el informe en pantalla y adjunta fotos.
Esos datos aparecen en dos sitios:

**Pantalla de trazabilidad** — bloque «Verificación en campo» con el estado, el
verificador, los resultados según el alcance, la tabla completa de clasificación
por tallas (camarón adulto), la evidencia fotográfica y un enlace a la
acreditación del verificador.

**Certificado PDF** — sección «8. Verificación en campo» con verificador y su
acreditación vigente, resultado, lote y planta, pesos y rendimientos, tallas,
metabisulfito, sabor, incidencias y conclusión. Es el documento que el comprador
enseña a terceros: si el lote pasó por un verificador acreditado, tiene que
constar ahí.

Las fotos se sirven por `/marketplace/verificacion/<uuid>/foto/<id>`, restringido
a las partes de la operación (comprador, vendedor, verificador) y a usuarios
internos, y solo para adjuntos de esa verificación.

En el PDF van **embebidas** como data-URI (sección 9), porque wkhtmltopdf no
puede pedirlas por URL: esa ruta exige sesión iniciada. Se redimensionan a 900 px
y se recomprimen a JPEG antes de incrustarlas —una foto de móvil son varios MB y
el certificado se volvería inmanejable—, con un tope de 6 por documento; si hay
más, el PDF lo indica y remite a la versión en línea.

## Honorario del verificador

Se configura en **Ajustes › Marketplace Camarón › Honorario de verificación**
(parámetro `shrimp_verification.fee`, arranca en 500). Se copia a cada
verificación al crearla, así que cambiar el valor no altera las ya emitidas.

Todavía **no genera pedido de venta ni factura**, a diferencia de la comisión del
marketplace. Queda pendiente si se quiere cerrar el circuito económico.

## Estados

    assigned → in_field → done → approved
                                ↘ approved_obs   (aprobada con observaciones)
                                ↘ rejected

`approved_obs` existe porque la realidad no es binaria: un lote puede llegar con
problemas de calidad y aun así aceptarse. Sin esa opción el verificador se ve
forzado a mentir en una dirección u otra.

## Parte para WhatsApp

`verification.whatsapp_report()` genera el parte en el **mismo formato** que el
equipo ya envía, con los mismos asteriscos y el mismo orden. La idea es que
sigan mandando su mensaje de siempre, pero calculado y sin errores de dedo,
mientras el módulo se queda con el dato estructurado.

Está en el portal (botón Copiar) y en el backend (botón "Parte para WhatsApp").

## Rutas del portal

| Ruta | Quién | Para qué |
|---|---|---|
| `/verificador/bandeja` | verificador | Órdenes asignadas, con contadores |
| `/verificador/verificacion/<uuid>` | verificador | Informe de campo y veredicto |
| `/marketplace/buy/<uuid>/verificar` | comprador | Compra eligiendo verificador |
| `/marketplace/verificacion/pendiente/<uuid>` | comprador | Confirmación de compra pendiente |
| `/marketplace/compras/<uuid>/concluir` | comprador | Concluir tras la aprobación |

El informe **se puede guardar por partes**: una verificación no se completa de
una sentada.

## Dos trampas de Odoo 19 que este módulo evita

**1. `_sql_constraints` ya no hace nada.** Odoo 19 lo ignora (solo avisa por
log), así que las restricciones únicas declaradas así **no existen en la base de
datos**. Aquí se usa `models.Constraint`, que sí las crea:

    _uniq_verification_per_tx = models.Constraint(
        "unique(transaction_id)",
        "Esa compra ya tiene una verificación asignada.",
    )

**2. Los dominios booleanos llegan al método `search=` como operadores de
conjunto.** Un `("mi_booleano", "=", True)` no llega como `operator='='`, sino
como `operator='in'` con `value=OrderedSet([True])`. Si el método asume `'='`,
devuelve **exactamente el complemento** y el filtro queda invertido en silencio.
Hay un helper `_bool_search_domain()` en `models/res_partner.py` que lo traduce
bien; úsalo para cualquier booleano calculado con búsqueda.

**3. No uses formato `%` en las plantillas QWeb ni pases textos con `%` por el
qcontext.** QWeb reinyecta la expresión en el código que genera usando formato
de cadena, así que el literal se procesa dos veces: `'%.2f %%'` se convierte en
`'%.2f %'` y revienta con `ValueError: incomplete format`. Usa `str.format()`:

    <!-- MAL  --> <t t-esc="'%.2f %%' % v.yield_pct"/>
    <!-- BIEN --> <t t-esc="'{:.2f} %'.format(v.yield_pct)"/>

Por la misma razón el parte de WhatsApp (que contiene "67,22%") **no se pasa por
el contexto** del `render`, sino que la plantilla llama a `v.whatsapp_report()`.
