/// Firma de operaciones sensibles (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md,
/// secciones 3 y 4). Funciones puras para poder probarlas sin teléfono.
library;

import 'dart:convert';

import 'package:crypto/crypto.dart';

/// Prefijo de la API: la ruta firmada lo incluye (`/api/v1/verifications/…`).
const prefijoApi = '/api/v1';

/// Ventana del servidor: 7 días hacia atrás. La app considera caducada una
/// firma una hora antes para no enviar algo que el servidor va a rechazar.
const vigenciaFirma = Duration(days: 7);
const margenVigencia = Duration(hours: 1);

/// Ruta tal como se firma: path exacto, sin host ni query, con `/api/v1`.
String rutaFirmada(String ruta) => ruta.startsWith(prefijoApi) ? ruta : '$prefijoApi$ruta';

/// SHA-256 en hex minúsculas de los bytes exactos del cuerpo.
String sha256Hex(List<int> bytes) => sha256.convert(bytes).toString();

/// Bytes que viajan en la petición: UTF-8 del cuerpo serializado guardado.
List<int> bytesDelCuerpo(String cuerpoSerializado) => utf8.encode(cuerpoSerializado);

/// Cadena firmada: METODO\nRUTA\nTIMESTAMP\nIDEMPOTENCY-KEY\nSHA256_CUERPO
/// (UTF-8, sin salto final).
String cadenaCanonica({
  required String metodo,
  required String ruta,
  required int timestamp,
  required String idempotencia,
  required List<int> cuerpo,
}) =>
    [metodo.toUpperCase(), rutaFirmada(ruta), '$timestamp', idempotencia, sha256Hex(cuerpo)].join('\n');

/// Segundos Unix (entero) de un instante.
int segundosUnix(DateTime t) => t.toUtc().millisecondsSinceEpoch ~/ 1000;

/// Las rutas firmadas del servidor (sección 4 del contrato de firma, C4/C5
/// del de mejoras y publicar un lote), con `{id}`
/// como un segmento. La acción final se acepta como `:accion` o `/accion`
/// (el servidor admite las dos formas).
final _rutasSensibles = <String, List<RegExp>>{
  'POST': [
    for (final p in const [
      r'/verifications',
      r'/verifications/[^/:]+/acceptance',
      r'/verifications/[^/:]+/acceptance[:/]revert',
      r'/verifications/[^/:]+[:/](assign|start|verdict)',
      // Fotos de campo y compra directa (CONTRATO-MEJORAS-APP.md, C4 y C5).
      r'/verifications/[^/:]+/photos',
      r'/purchases',
      // Publicar un lote (shrimp_api 19.0.1.16.0).
      r'/products/[^/:]+[:/]publish',
      // Precio, cantidad o unidad de un lote publicado y reactivar uno
      // retirado (shrimp_api 19.0.1.17.0).
      r'/products/[^/:]+[:/](update-terms|unarchive)',
      r'/dispatches/[^/:]+/arrival',
      r'/transactions/[^/:]+[:/]complete',
      r'/harvest/commitments/[^/:]+[:/]buy',
      r'/copack/requests/[^/:]+/offers',
      r'/copack/orders/[^/:]+[:/](reception|packing|sign|revert-signature|reopen|close)',
      r'/me/active-role',
    ])
      RegExp('^$p\$'),
  ],
  'PATCH': [
    for (final p in const [r'/verifications/[^/:]+/report', r'/dispatches/[^/:]+']) RegExp('^$p\$'),
  ],
};

/// ¿La ruta exige firma para las claves de app? (sección 4 del contrato).
/// [ruta] puede llevar o no el prefijo `/api/v1`. Las lecturas, el login,
/// el logout y el primer registro del dispositivo no se firman (sustituir el
/// dispositivo sí: lo hace Sesion.registrarDeNuevo aparte).
bool esRutaSensible(String metodo, String ruta) {
  final r = ruta.startsWith(prefijoApi) ? ruta.substring(prefijoApi.length) : ruta;
  return (_rutasSensibles[metodo.toUpperCase()] ?? const []).any((e) => e.hasMatch(r));
}

/// La firma de una operación, hecha en el momento de la acción.
class FirmaOperacion {
  /// X-Signature-Timestamp (segundos Unix).
  final int timestamp;

  /// X-Signature: firma ES256 DER en base64 estándar.
  final String firma;

  /// Huella de la clave pública con la que se firmó ([huellaDeClave]).
  final String huellaClave;

  /// Dispositivo registrado cuando se firmó (puede ser nulo si el registro
  /// quedó pendiente por falta de señal; se completa al enviar).
  final String? dispositivoId;

  const FirmaOperacion({required this.timestamp, required this.firma, required this.huellaClave, this.dispositivoId});

  /// Cabeceras del contrato (sección 3).
  Map<String, String> cabeceras(String dispositivo) => {
        'X-Device-Id': dispositivo,
        'X-Signature-Timestamp': '$timestamp',
        'X-Signature': firma,
      };

  /// ¿Sigue dentro de la ventana del servidor (con margen)?
  bool vigente(DateTime ahora) {
    final edad = segundosUnix(ahora) - timestamp;
    return edad < (vigenciaFirma - margenVigencia).inSeconds && edad > -const Duration(minutes: 10).inSeconds;
  }
}
