import 'dart:convert';
import 'package:http/http.dart' as http;

/// Error devuelto por la API (formato RFC 9457: title, detail, status, errors).
class ErrorApi implements Exception {
  final int status;
  final String titulo;
  final String detalle;
  final String tipo;
  const ErrorApi(this.status, this.titulo, this.detalle, this.tipo);

  /// 4xx distintos de 401/403/429 son errores del dato: reintentar no sirve.
  bool get esDefinitivo => status >= 400 && status < 500 && status != 401 && status != 403 && status != 429;

  @override
  String toString() => '$titulo ($status): $detalle';
}

class SinConexion implements Exception {
  @override
  String toString() => 'Sin conexión con el servidor';
}

/// Cliente mínimo de la API de CamaronMarket (`/api/v1`).
class ApiCliente {
  final String baseUrl;
  final String clave;
  final http.Client _http;
  ApiCliente({required this.baseUrl, required this.clave, http.Client? cliente}) : _http = cliente ?? http.Client();

  Map<String, String> _cabeceras({String? idempotencia}) => {
        'Authorization': 'Bearer $clave',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': ?idempotencia,
      };

  Uri _uri(String ruta, [Map<String, String>? params]) =>
      Uri.parse('$baseUrl/api/v1$ruta').replace(queryParameters: params);

  Future<Map<String, dynamic>> _enviar(Future<http.Response> Function() llamada) async {
    http.Response resp;
    try {
      resp = await llamada().timeout(const Duration(seconds: 40));
    } catch (_) {
      throw SinConexion();
    }
    final cuerpo = resp.body.isEmpty ? <String, dynamic>{} : jsonDecode(resp.body);
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

  Future<Map<String, dynamic>> get(String ruta, {Map<String, String>? params}) =>
      _enviar(() => _http.get(_uri(ruta, params), headers: _cabeceras()));

  Future<Map<String, dynamic>> post(String ruta, Map<String, dynamic> cuerpo, {required String idempotencia}) =>
      _enviar(() => _http.post(_uri(ruta), headers: _cabeceras(idempotencia: idempotencia), body: jsonEncode(cuerpo)));

  Future<Map<String, dynamic>> patch(String ruta, Map<String, dynamic> cuerpo, {required String idempotencia}) =>
      _enviar(() => _http.patch(_uri(ruta), headers: _cabeceras(idempotencia: idempotencia), body: jsonEncode(cuerpo)));

  /// `GET /me`: comprueba la clave y devuelve quién soy.
  Future<Map<String, dynamic>> quienSoy() => get('/me');

  /// Recorre todas las páginas de una lista (cursor opaco de la API).
  Future<List<Map<String, dynamic>>> listarTodo(String ruta, {Map<String, String>? params}) async {
    final todo = <Map<String, dynamic>>[];
    String? cursor;
    while (true) {
      final pagina = await get(ruta, params: {...?params, 'limit': '200', 'cursor': ?cursor});
      todo.addAll(((pagina['data'] ?? []) as List).cast<Map<String, dynamic>>());
      final meta = (pagina['meta'] ?? {}) as Map;
      cursor = meta['next_cursor'] as String?;
      if (cursor == null || cursor.isEmpty) break;
    }
    return todo;
  }

  void cerrar() => _http.close();
}
