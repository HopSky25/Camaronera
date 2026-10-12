// Servidor falso que aplica el contrato de firma como shrimp_api: registro
// del dispositivo (primer registro sin firma, sustitución firmada por el
// actual), verificación ES256 de la cadena canónica reconstruida con lo que
// LLEGA (método, path, cabeceras y bytes del cuerpo), ventana de 7 días,
// device-revoked / device-not-registered y not-an-app-key.
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';

import 'datos_api.dart';
import 'firmador_falso.dart';

http.Response respuestaJson(int status, Object cuerpo) =>
    http.Response(jsonEncode(cuerpo), status, headers: {'content-type': 'application/json'});

class DispositivoServidor {
  final String id;
  final String spki;
  bool activo = true;
  DispositivoServidor(this.id, this.spki);
}

/// Una petición firmada que el servidor aceptó.
class Aceptada {
  final http.Request req;
  final String cadena;
  Aceptada(this.req, this.cadena);
}

class ServidorFirmas {
  /// La clave que emite el login es de la app (si no, /me/devices → 409).
  bool claveDeApp = true;
  bool enLinea = true;
  DateTime ahora = DateTime.now();
  Map<String, dynamic> Function() me = meUnPerfil;

  final peticiones = <http.Request>[];
  final dispositivos = <DispositivoServidor>[];
  final aceptadas = <Aceptada>[];
  final firmasUsadas = <String>{};

  /// Respuesta forzada para una ruta sensible (antes de verificar nada).
  http.Response Function(http.Request)? forzar;

  /// Respuesta de las rutas sensibles aceptadas.
  Map<String, dynamic> Function(http.Request) respuesta = (_) => {'ok': true};

  DispositivoServidor? get activo => dispositivos.where((d) => d.activo).lastOrNull;

  late final MockClient cliente = MockClient((req) async {
    if (!enLinea) throw http.ClientException('Failed host lookup');
    peticiones.add(req);
    final ruta = req.url.path;
    final metodo = req.method;
    if (ruta == '/api/v1/auth/login') {
      return respuestaJson(200, {'api_key': 'trz_app', 'expires_at': '2027-10-11T10:00:00Z', 'me': me(), 'device_required': true});
    }
    if (req.headers['Authorization'] != 'Bearer trz_app') {
      return respuestaJson(401, problema(401, 'revoked-api-key', 'Revocada.'));
    }
    if (ruta == '/api/v1/auth/logout') {
      for (final d in dispositivos) {
        d.activo = false;
      }
      return respuestaJson(200, {'revoked': true});
    }
    if (ruta == '/api/v1/me' && metodo == 'GET') return respuestaJson(200, me());
    if (ruta == '/api/v1/me/devices' && metodo == 'GET') {
      return respuestaJson(200, {
        'data': [
          for (final d in dispositivos.reversed)
            {'id': d.id, 'name': 'Samsung', 'active': d.activo, 'current': true, 'last_used_at': '2026-10-12T15:00:00Z'},
        ]
      });
    }
    if (ruta == '/api/v1/me/devices' && metodo == 'POST') {
      if ((req.headers['Idempotency-Key'] ?? '').isEmpty) return respuestaJson(400, problema(400, 'idempotency-key-required', ''));
      if (!claveDeApp) return respuestaJson(409, problema(409, 'not-an-app-key', 'No es de app.'));
      final previo = activo;
      if (previo != null) {
        final error = _verificar(req, exigirDe: previo);
        if (error != null) return error;
      }
      final cuerpo = jsonDecode(req.body) as Map<String, dynamic>;
      if (cuerpo['alg'] != 'ES256' || base64.decode(cuerpo['public_key'] as String).length != 91) {
        return respuestaJson(422, problema(422, 'validation-error', 'Clave pública inválida.'));
      }
      previo?.activo = false;
      final nuevo = DispositivoServidor('dev-${dispositivos.length + 1}', cuerpo['public_key'] as String);
      dispositivos.add(nuevo);
      return respuestaJson(201, {
        'id': nuevo.id,
        'name': cuerpo['name'],
        'platform': cuerpo['platform'],
        'alg': 'ES256',
        'created_at': '2026-10-12T15:00:00Z',
        'active': true,
        'current': true,
      });
    }
    if (esRutaSensible(metodo, ruta)) {
      final f = forzar;
      if (f != null) return f(req);
      final error = _verificar(req);
      if (error != null) return error;
      return respuestaJson(200, respuesta(req));
    }
    if (metodo == 'GET') return respuestaJson(200, {'data': [], 'meta': {}});
    return respuestaJson(404, problema(404, 'not-found', 'No existe.'));
  });

  http.Response _error(String tipo) => respuestaJson(tipo == 'signature-replayed' ? 409 : 403, problema(403, tipo, tipo));

  /// Lo mismo que _check_signature del servidor. Nulo si la firma vale.
  http.Response? _verificar(http.Request req, {DispositivoServidor? exigirDe}) {
    final devId = req.headers['X-Device-Id'];
    final ts = req.headers['X-Signature-Timestamp'];
    final firma = req.headers['X-Signature'];
    if (devId == null && ts == null && firma == null) {
      return _error(activo == null ? 'device-not-registered' : 'signature-required');
    }
    if (devId == null || ts == null || firma == null) return _error('signature-required');
    final d = dispositivos.where((x) => x.id == devId).firstOrNull;
    if (d == null || (exigirDe != null && d != exigirDe)) return _error('device-not-registered');
    if (!d.activo) return _error('device-revoked');
    if (!RegExp(r'^[0-9]{1,12}$').hasMatch(ts)) return _error('invalid-signature');
    final edad = segundosUnix(ahora) - int.parse(ts);
    if (edad > const Duration(days: 7).inSeconds || edad < -600) return _error('signature-expired');
    final cadena = [
      req.method.toUpperCase(),
      req.url.path,
      ts,
      req.headers['Idempotency-Key'] ?? '',
      sha256.convert(req.bodyBytes).toString(),
    ].join('\n');
    if (!verificarEs256(d.spki, utf8.encode(cadena), firma)) return _error('invalid-signature');
    final idem = req.headers['Idempotency-Key'] ?? '';
    final huella = firma;
    if (firmasUsadas.contains(huella) && !aceptadas.any((a) => a.req.headers['Idempotency-Key'] == idem)) {
      return _error('signature-replayed');
    }
    firmasUsadas.add(huella);
    aceptadas.add(Aceptada(req, cadena));
    return null;
  }

  Iterable<http.Request> a(String rutaApi) => peticiones.where((r) => r.url.path == '/api/v1$rutaApi');
}
