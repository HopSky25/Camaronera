import 'dart:convert';
import 'package:http/http.dart' as http;

/// Error devuelto por la API (formato RFC 9457: title, detail, status, errors).
class ErrorApi implements Exception {
  final int status;
  final String titulo;
  final String detalle;

  /// Sufijo del `type`: invalid-credentials, account-not-approved, rate-limited...
  final String tipo;
  const ErrorApi(this.status, this.titulo, this.detalle, this.tipo);

  /// 4xx distintos de 401/429 son errores del dato o de permiso: reintentar
  /// no sirve (la cola lo deja a la vista para corregir o descartar).
  bool get esDefinitivo => status >= 400 && status < 500 && status != 401 && status != 429;
  bool get esNoAutorizado => status == 401;
  bool get esProhibido => status == 403;

  /// Texto para la gente de campo, sin jerga.
  String get mensaje => switch (tipo) {
        'invalid-credentials' => 'Correo o contraseña incorrectos.',
        'user-not-allowed' => 'Tu usuario no puede entrar a la app. Escribe a soporte de CamaronMarket.',
        'account-not-approved' => 'Tu cuenta todavía no está aprobada. Te avisaremos por correo cuando lo esté.',
        'rate-limited' => 'Demasiados intentos seguidos. Espera un momento y vuelve a probar.',
        'missing-api-key' || 'invalid-api-key' => 'La clave de API no es válida.',
        'expired-api-key' => 'La sesión caducó. Vuelve a entrar.',
        'revoked-api-key' => 'La sesión fue cerrada desde el portal. Vuelve a entrar.',
        'insufficient-scope' => 'Tu clave no tiene permiso para esta operación.',
        'ip-not-allowed' => 'Esta clave no admite conexiones desde tu red.',
        'invalid-state' => detalle.isEmpty ? 'El registro ya no está en el estado necesario.' : detalle,
        _ => detalle.isNotEmpty ? detalle : titulo,
      };

  @override
  String toString() => '$titulo ($status): $detalle';
}

class SinConexion implements Exception {
  @override
  String toString() => 'Sin conexión con el servidor';
}

/// Texto claro para cualquier fallo de red o de la API.
String mensajeDeError(Object e) {
  if (e is ErrorApi) return e.mensaje;
  if (e is SinConexion) return 'Sin internet. Revisa la señal e inténtalo otra vez.';
  return 'Algo falló: $e';
}

/// Una página de una lista de la API (`data` + `meta.next_cursor`).
class PaginaApi {
  final List<Map<String, dynamic>> datos;
  final String? siguienteCursor;
  const PaginaApi(this.datos, this.siguienteCursor);
}

/// Cliente mínimo de la API de CamaronMarket (`/api/v1`).
class ApiCliente {
  final String baseUrl;
  final String clave;
  final http.Client _http;

  /// Se llama con un 401: la clave caducó o fue revocada y hay que volver a entrar.
  void Function(ErrorApi error)? alNoAutorizado;

  /// Perfil con el que actúa la app ("actuar como"). Va en la cabecera
  /// `X-Shrimp-Role` de cada petición; NO se cambia el perfil activo de la
  /// cuenta en el servidor (eso movería los menús del portal web).
  String? rolActivo;

  ApiCliente({required this.baseUrl, required this.clave, http.Client? cliente}) : _http = cliente ?? http.Client();

  Map<String, String> _cabeceras({String? idempotencia}) => {
        'Authorization': 'Bearer $clave',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': ?idempotencia,
        'X-Shrimp-Role': ?rolActivo,
      };

  Uri _uri(String ruta, [Map<String, String>? params]) =>
      Uri.parse('$baseUrl/api/v1$ruta').replace(queryParameters: params);

  static Future<Map<String, dynamic>> _enviarSinSesion(Future<http.Response> Function() llamada) async {
    http.Response resp;
    try {
      resp = await llamada().timeout(const Duration(seconds: 40));
    } catch (_) {
      throw SinConexion();
    }
    return _interpretar(resp);
  }

  static Map<String, dynamic> _interpretar(http.Response resp) {
    dynamic cuerpo;
    try {
      cuerpo = resp.body.isEmpty ? <String, dynamic>{} : jsonDecode(resp.body);
    } catch (_) {
      cuerpo = <String, dynamic>{};
    }
    if (resp.statusCode >= 200 && resp.statusCode < 300) {
      return cuerpo is Map<String, dynamic> ? cuerpo : {'data': cuerpo};
    }
    final m = cuerpo is Map ? cuerpo : {};
    final tipo = (m['type'] ?? '') as String;
    final errores = (m['errors'] as List?)?.map((e) => '${e['field']}: ${e['message']}').join('; ');
    throw ErrorApi(
      resp.statusCode,
      (m['title'] ?? 'Error') as String,
      [?m['detail'] as String?, if (errores != null && errores.isNotEmpty) errores].join(' '),
      tipo.split('#').last,
    );
  }

  Future<Map<String, dynamic>> _enviar(Future<http.Response> Function() llamada) async {
    try {
      return await _enviarSinSesion(llamada);
    } on ErrorApi catch (e) {
      if (e.esNoAutorizado) alNoAutorizado?.call(e);
      rethrow;
    }
  }

  Future<Map<String, dynamic>> get(String ruta, {Map<String, String>? params}) =>
      _enviar(() => _http.get(_uri(ruta, params), headers: _cabeceras()));

  Future<Map<String, dynamic>> post(String ruta, Map<String, dynamic> cuerpo, {required String idempotencia}) =>
      _enviar(() => _http.post(_uri(ruta), headers: _cabeceras(idempotencia: idempotencia), body: jsonEncode(cuerpo)));

  Future<Map<String, dynamic>> patch(String ruta, Map<String, dynamic> cuerpo, {required String idempotencia}) =>
      _enviar(() => _http.patch(_uri(ruta), headers: _cabeceras(idempotencia: idempotencia), body: jsonEncode(cuerpo)));

  /// `GET /me`: comprueba la clave y devuelve quién soy.
  Future<Map<String, dynamic>> quienSoy() => get('/me');

  /// `POST /auth/login` (sin clave): correo + contraseña → clave de API y `me`.
  static Future<Map<String, dynamic>> iniciarSesion({
    required String baseUrl,
    required String correo,
    required String contrasena,
    required String dispositivo,
    required String idempotencia,
  }) =>
      _enviarSinSesion(() => http.post(
            Uri.parse('$baseUrl/api/v1/auth/login'),
            headers: {'Accept': 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': idempotencia},
            body: jsonEncode({'login': correo, 'password': contrasena, 'device': dispositivo}),
          ));

  /// `POST /auth/logout`: revoca esta clave en el servidor.
  Future<void> cerrarSesion({required String idempotencia}) => post('/auth/logout', {}, idempotencia: idempotencia);

  /// Una página de una lista (cursor opaco de la API).
  Future<PaginaApi> pagina(String ruta, {Map<String, String>? params, String? cursor, int limite = 50}) async {
    final resp = await get(ruta, params: {...?params, 'limit': '$limite', 'cursor': ?cursor});
    final datos = ((resp['data'] ?? []) as List).cast<Map<String, dynamic>>();
    final meta = (resp['meta'] ?? {}) as Map;
    final siguiente = meta['next_cursor'] as String?;
    return PaginaApi(datos, siguiente == null || siguiente.isEmpty ? null : siguiente);
  }

  /// Recorre todas las páginas de una lista.
  Future<List<Map<String, dynamic>>> listarTodo(String ruta, {Map<String, String>? params}) async {
    final todo = <Map<String, dynamic>>[];
    String? cursor;
    while (true) {
      final p = await pagina(ruta, params: params, cursor: cursor, limite: 200);
      todo.addAll(p.datos);
      cursor = p.siguienteCursor;
      if (cursor == null) break;
    }
    return todo;
  }

  void cerrar() => _http.close();
}
