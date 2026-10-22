# CamaronMarket - Firma electrónica del informe de verificación

La empresa verificadora firma electrónicamente, desde su portal, el informe de
verificación en PDF. La firma tiene valor legal en Ecuador si se hace con el
certificado de una entidad de certificación acreditada.

## Qué hace

1. **Informe en PDF propio** (`Informe de verificación (PDF)`, también en el menú
   Imprimir del backoffice). Incluye partes, lote, verificadora y técnico,
   fechas, pesos, presentación, metabisulfito, clasificación por clase y talla,
   rendimiento, sabor, gramaje, conteos, incidencias, GPS, veredicto y
   conclusión. El PDF firmado lleva un QR que apunta a la página pública de
   comprobación.
2. **Firma PAdES** con pyHanko: PAdES-B-B; PAdES-B-T si se configura un servidor
   de sellado de tiempo (`shrimp_firma.tsa_url`). Firma visible al pie de la
   última página: titular, cédula/RUC, emisor, fecha y hora, y serie del
   certificado.
3. **Flujo en el portal del verificador.** Cuando la verificación ya tiene
   veredicto (aprobada, aprobada con observaciones o rechazada), aparece la
   sección «Firma electrónica del informe» con:
   - «Ver el informe en PDF (sin firmar)», para revisarlo antes;
   - «Firmar informe con firma electrónica», que abre `/verifier/verifications/<ref>/firmar`.
   Ahí se sube el .p12/.pfx, se escribe la contraseña y se marca la casilla
   «la firma es mía». Pueden firmar el técnico asignado o el administrador de
   la empresa verificadora (el mismo criterio que `_puede_dictaminar`).
   La cédula o el RUC del certificado tiene que coincidir con el RUC/cédula
   registrado (`vat_or_id` o `vat`) del técnico que firma, o con el de la
   empresa verificadora o la cédula de su representante legal. Se comparan
   solo los dígitos, y un RUC de persona natural (cédula + `001`) vale por
   su cédula. Si no hay identificación registrada o no coincide, no se firma.
   El formulario es POST con CSRF, exige HTTPS (salvo desde localhost) y
   admite 10 intentos por usuario cada 15 minutos.
4. **Comprobación pública** en `/signature/<token>`. El token es
   aleatorio (43 caracteres) y no el id de la verificación. La página muestra si
   el informe está firmado, la verificadora, el firmante, el emisor, la fecha,
   el nivel de firma y la huella SHA-256 del PDF, y permite descargar el PDF.
   No publica la cédula (ya consta en el propio PDF).
5. **Dónde se ve:**
   - en la aceptación del comprador y del vendedor, en la trazabilidad en
     pantalla y en el certificado de trazabilidad en PDF: «Informe firmado
     electrónicamente por … (…) el …» con la descarga, o «Pendiente de firma
     electrónica de la verificadora»;
   - en el backoffice, la pestaña «Firma electrónica» de la verificación
     (solo lectura), con el historial de firmas.
6. **Firma obligatoria** (`shrimp_firma.obligatoria`, por defecto False). Si
   está activa, las partes no pueden aceptar ni contraofertar hasta que el
   informe esté firmado; rechazar sí pueden. El cron no da por aceptado por
   vencimiento un informe sin firmar, y el plazo de aceptación vuelve a correr
   completo desde la firma.

Una verificación firmada no se vuelve a firmar. Si sale de los estados con
veredicto (por ejemplo, se reabre o se cancela), la firma vigente pasa a
**archivada**. Sigue existiendo, con su PDF y su página pública marcada como
«Firma archivada: no es la versión vigente», y el informe se puede volver a
firmar cuando tenga un nuevo veredicto.

### El certificado no se guarda

- El .p12 llega en la petición multipart y werkzeug lo mantiene en memoria
  (es pequeño). El servidor lo abre con `cryptography`, firma y suelta todas
  las referencias (`CertificadoCargado.olvidar`).
- No se escribe en ningún campo, adjunto, mensaje ni log. Los errores al
  usuario son textos en español sin detalle técnico.
- Del certificado se guarda solo lo público: titular (CN), identificación,
  emisor, serie, huella SHA-256 y fecha de vencimiento.
- Python no garantiza que se sobrescriba la memoria liberada. Ningún objeto
  que dure más que la petición conserva la clave.
- Los tests comprueban que el .p12 y la contraseña no quedan en
  `ir_attachment`, `mail_message`, `ir_config_parameter`, en los registros de
  firma ni en el log.

## Parámetros del sistema (Ajustes › Técnico › Parámetros del sistema)

| Clave | Por defecto | Qué hace |
|---|---|---|
| `shrimp_firma.solo_emisores_acreditados` | `True` | Solo se firma si la cadena del certificado se comprueba hasta una raíz de `trust_roots_dir`. Sin raíces configuradas, con un autofirmado o con una cadena que no valida, no se firma. Solo con `False` (base de pruebas y demo) se firma con certificados autofirmados, que quedan rotulados «sin verificar» |
| `shrimp_firma.trust_roots_dir` | (no existe = sin raíces) | Directorio del servidor con las raíces e intermedias de las entidades acreditadas (ver abajo) |
| `shrimp_firma.allow_fetching` | `False` | `True`: al firmar se consulta la revocación (CRL/OCSP) por red, en modo «soft-fail» |
| `shrimp_firma.emisores_aceptados` | lista de entidades acreditadas en Ecuador | Filtro adicional, solo después de comprobar la cadena: subcadenas del DN del emisor, una por línea, sin distinguir mayúsculas ni tildes. Por sí sola no acredita nada |
| `shrimp_firma.obligatoria` | `False` | Exige la firma antes de que las partes acepten |
| `shrimp_firma.tsa_url` | (no existe = vacío) | URL RFC 3161 del servidor de sellado de tiempo; con valor la firma es PAdES-B-T |
| `shrimp_firma.exigir_https` | `True` | Solo acepta el formulario por HTTPS (localhost siempre se admite) |
| `shrimp_firma.intentos_max` / `shrimp_firma.intentos_ventana_s` | 10 / 900 | Límite de intentos de firma por usuario |

### Raíces de confianza (`shrimp_firma.trust_roots_dir`)

«Emisor acreditado» quiere decir que la cadena del certificado, comprobada
con pyhanko-certvalidator (firmas, vigencias, restricciones de CA y uso de
clave para firmar), llega a una raíz que el administrador puso en este
directorio. El nombre del emisor escrito en el certificado no basta:
cualquiera genera un autofirmado cuyo emisor diga «Banco Central del
Ecuador».

1. Descarga de la web oficial de cada entidad acreditada su certificado
   raíz y sus certificados intermedios (subordinados). Comprueba la huella
   SHA-256 con la que publica la entidad (o FirmaEC):
   `openssl x509 -in raiz.crt -noout -fingerprint -sha256`.
2. Cópialos a un directorio del servidor que el usuario de Odoo pueda leer,
   por ejemplo `/opt/odoo/firma_raices/`. Formatos: PEM (`.pem`, `.crt`,
   uno o varios certificados por archivo) o DER (`.cer`, `.der`). Las
   subcarpetas se ignoran.
3. Pon la ruta en `shrimp_firma.trust_roots_dir`.

Los certificados autofirmados del directorio son las raíces de confianza;
los demás se usan como intermedias para armar la cadena, pero no dan
confianza por sí solos. También se usan las intermedias que trae el propio
.p12; la raíz que pudiera traer el .p12 nunca se toma como de confianza.
Se lee el directorio en cada firma, así que un cambio no necesita reiniciar.

La lista sembrada en `emisores_aceptados` contiene: Banco Central del Ecuador, Security Data,
Uanataca, ANF (AC, Autoridad de Certificación, High Assurance), Datil, Consejo
de la Judicatura, ECIBCE, Lazzate, Eclipsoft, Firmasegura y ArgosData.
**Hay que revisarla contra la lista vigente de entidades de certificación
acreditadas que publica ARCOTEL**, porque cambia con los años. La coincidencia
es por subcadena del DN del emisor (`O=`, `CN=`…). Para comprobar el DN real
de una entidad:
`openssl pkcs12 -in firma.p12 -nokeys | openssl x509 -noout -issuer`.

## Valor legal

**Lo que sí tiene:**
- La Ley de Comercio Electrónico, Firmas Electrónicas y Mensajes de Datos da a
  la firma electrónica el mismo valor que a la manuscrita (arts. 13 y 14) si
  cumple sus requisitos: que identifique al titular, que esté bajo su control
  exclusivo y que permita detectar cualquier modificación posterior del
  documento.
- Para eso el certificado tiene que ser emitido por una entidad de
  certificación acreditada (hoy, ante ARCOTEL) y estar vigente al firmar.
- La firma PAdES cubre el PDF completo: cualquier cambio posterior la invalida.

**Lo que no tiene, o no garantiza este módulo:**
- La revocación (CRL/OCSP) solo se consulta si `shrimp_firma.allow_fetching`
  es `True`, y en «soft-fail»: si el servidor de la entidad no responde, se
  firma igual. Por defecto no se consulta: un certificado revocado pero no
  vencido se acepta, y lo detectará quien valide el PDF.
- Comprueba que la cédula/RUC del certificado coincida con la registrada del
  técnico o de la empresa verificadora (o de su representante legal), no que
  el técnico sea quien de verdad tecleó la contraseña. La responsabilidad del
  uso es del titular.
- Con un certificado autofirmado o de una entidad no acreditada (solo
  posible con `solo_emisores_acreditados = False`) la firma es técnicamente
  correcta, pero **no tiene valor legal**. Solo sirve para pruebas. Esas
  firmas quedan con `signer_chain_verified = False` y el sello del PDF, la
  página pública, el chatter y las pantallas de las partes muestran
  «Firmante declarado en el certificado (sin verificar)». Con la firma
  obligatoria y `solo_emisores_acreditados = True`, una firma sin la cadena
  comprobada no desbloquea la aceptación.
- Sin sello de tiempo (B-B), la fecha de la firma la declara el servidor y no
  un tercero de confianza. Para validación a largo plazo conviene B-T (TSA).
- La firma acredita el informe del verificador, no la aceptación de las
  partes, que sigue registrándose en la plataforma.

## Cómo obtener una firma electrónica en Ecuador

Se solicita a una entidad de certificación acreditada: Banco Central del
Ecuador, Security Data, Uanataca, ANF, Datil, Lazzate, Eclipsoft, Firmasegura,
etc. Hay certificados de persona natural, de miembro de empresa y de
representante legal. Normalmente se piden en línea con la cédula (y el RUC y el
nombramiento si es para la empresa), se paga la tarifa y se valida la
identidad. Para este uso hay que pedir el formato **archivo (.p12/.pfx)**, no
token USB: el token no se puede subir a una web. El certificado dura de 1 a 5
años y hay que renovarlo antes del vencimiento.

## Cómo verificar un PDF firmado

- **FirmaEC** (aplicativo oficial del Gobierno del Ecuador, firmaec.gob.ec):
  opción «Validar documento». Reconoce las entidades acreditadas.
- **Adobe Acrobat Reader**: panel «Firmas». Muestra si el documento no se
  modificó; para que diga «válida» con certificados ecuatorianos puede hacer
  falta agregar la raíz de la entidad como certificado de confianza.
- **Página pública**: escanea el QR del PDF o abre `/signature/<token>`
  y compara la huella SHA-256 (`sha256sum informe.pdf`).

## Lo que falta para producción

1. **Instalar pyHanko en el servidor**, con las mismas restricciones que en
   local para que pip no suba `cryptography` ni `lxml` (Odoo 19 usa
   cryptography 42 y lxml 5; pyHanko ≥ 0.26 exige versiones más nuevas):
   ```
   /opt/odoo/venv/bin/pip install -c constraints.txt -r requirements.txt
   ```
   (`requirements.txt` fija `pyHanko==0.25.1`; `constraints.txt` fija
   `cryptography==42.0.8` y `lxml==5.2.1`. Antes de instalar, comprueba que
   coinciden con las versiones del venv del servidor: `pip list`.) Sin
   pyHanko el módulo no se instala (`external_dependencies`). También necesita
   `wkhtmltopdf` con Qt parcheado, el mismo de los demás informes.
2. **Configurar las raíces de confianza** (`shrimp_firma.trust_roots_dir`,
   ver arriba) con las raíces e intermedias de cada entidad acreditada que
   vayan a usar las verificadoras. Sin ellas, con
   `solo_emisores_acreditados = True`, nadie puede firmar. Revisar también
   la lista de emisores con la lista vigente de ARCOTEL y con el DN real de
   esos certificados.
3. **Registrar la cédula de cada técnico** (y el RUC de la verificadora) que
   vaya a firmar: si no coincide con la del certificado, no se firma.
4. **TSA**: decidir si se firma B-T y con qué servidor (uno de una entidad
   acreditada en Ecuador, si ofrece el servicio). Configurar
   `shrimp_firma.tsa_url`. Si la TSA no responde, la firma falla con un
   mensaje claro y no se guarda nada.
5. Comprobar que el proxy envía `X-Forwarded-Proto` y que Odoo corre con
   `proxy_mode = True`. Si no, el formulario no reconoce la conexión HTTPS.
6. Decidir si se activa `shrimp_firma.obligatoria`.
7. Probar con un certificado real de cada entidad que vayan a usar las
   verificadoras. En particular, los .p12 antiguos del BCE con cifrado RC2
   necesitan el proveedor «legacy» de OpenSSL, que `cryptography` 42 carga
   por defecto.

## Pruebas

```
cd /home/ccarballo/odoo19 && venv/bin/python src/odoo-bin -c odoo.conf -d demo_prueba \
  -u shrimp_firma_electronica --test-enable --test-tags /shrimp_firma_electronica \
  --stop-after-init --http-port=8097 --gevent-port=8098 --max-cron-threads=0
```

`tests/test_firma_informe.py` genera con `cryptography` una AC de pruebas
(raíz e intermedia, escritas en un directorio temporal que hace de
`trust_roots_dir`), certificados de persona emitidos por ella y autofirmados,
y los empaqueta en PKCS#12 en memoria. Comprueba:

- la firma correcta, validada con pyHanko (íntegra, del certificado esperado,
  `/ETSI.CAdES.detached`, que cubre el archivo entero y es visible);
- que el hash guardado coincide con el del PDF;
- los rechazos: contraseña incorrecta, certificado vencido, aún no vigente o
  sin uso de firma, autofirmado (aunque su emisor diga «Banco Central del
  Ecuador» o «Security Data»), sin raíces configuradas, de otra AC, y
  cédula que no es la del firmante;
- con `solo_emisores_acreditados = False`, que un autofirmado firma y se
  muestra «sin verificar» (sello, página pública, chatter, resumen);
- que nadie ajeno a la verificadora puede firmar (403);
- que no se firma dos veces y que la reapertura archiva la firma;
- la página pública por token y el 404 con un token inventado;
- la firma obligatoria;
- el sello de tiempo B-T (con una TSA simulada) y una TSA caída;
- el flujo HTTP completo con CSRF;
- que el .p12 no queda en la base de datos ni en el log.
