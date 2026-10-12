import 'api_cliente.dart';
import 'base_local.dart';
import 'sesion.dart';

/// Resultado de pedir una lista: los datos y de dónde salieron.
class ResultadoLista {
  final List<Map<String, dynamic>> datos;

  /// true cuando no hubo señal y se muestra la última respuesta guardada.
  final bool desdeCache;
  final DateTime? guardadoEn;
  final String? siguienteCursor;
  const ResultadoLista(this.datos, {this.desdeCache = false, this.guardadoEn, this.siguienteCursor});
}

/// Acceso a las listas y detalles de los perfiles en línea (camaronera,
/// empacadora, maquilador). Siempre intenta el servidor; si no hay señal,
/// devuelve la última respuesta guardada en SQLite con `desdeCache = true`.
class Repositorio {
  final Sesion sesion;
  final BaseLocal _bd;
  Repositorio({required this.sesion, BaseLocal? bd}) : _bd = bd ?? BaseLocal();

  ApiCliente get _api {
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    return api;
  }

  static String claveDe(String ruta, Map<String, String>? params) {
    if (params == null || params.isEmpty) return ruta;
    final claves = params.keys.toList()..sort();
    return '$ruta?${claves.map((k) => '$k=${params[k]}').join('&')}';
  }

  /// Primera página (o la siguiente con `cursor`). Solo la primera se guarda
  /// en caché; sin señal, solo se puede mostrar la primera.
  Future<ResultadoLista> pagina(String ruta, {Map<String, String>? params, String? cursor, int limite = 50}) async {
    final clave = claveDe(ruta, params);
    try {
      final p = await _api.pagina(ruta, params: params, cursor: cursor, limite: limite);
      if (cursor == null) await _bd.guardarLista(clave, p.datos);
      return ResultadoLista(p.datos, siguienteCursor: p.siguienteCursor);
    } on SinConexion {
      if (cursor != null) rethrow;
      final guardada = await _bd.leerLista(clave);
      if (guardada == null) rethrow;
      return ResultadoLista(guardada.datos, desdeCache: true, guardadoEn: guardada.guardadoEn);
    }
  }

  /// Todas las páginas (para los resúmenes del panel).
  Future<ResultadoLista> todo(String ruta, {Map<String, String>? params}) async {
    final clave = claveDe(ruta, params);
    try {
      final datos = await _api.listarTodo(ruta, params: params);
      await _bd.guardarLista(clave, datos);
      return ResultadoLista(datos);
    } on SinConexion {
      final guardada = await _bd.leerLista(clave);
      if (guardada == null) rethrow;
      return ResultadoLista(guardada.datos, desdeCache: true, guardadoEn: guardada.guardadoEn);
    }
  }

  /// Para los resúmenes del panel: si la API rechaza la lista (p. ej. 403 por
  /// falta de permiso), el panel sigue con el resto en vez de fallar entero.
  Future<ResultadoLista> todoOVacio(String ruta, {Map<String, String>? params}) async {
    try {
      return await todo(ruta, params: params);
    } on ErrorApi catch (e) {
      if (e.esNoAutorizado) rethrow;
      return const ResultadoLista([]);
    }
  }

  /// Un objeto (detalle); se guarda como lista de un elemento.
  Future<ResultadoLista> detalle(String ruta, {Map<String, String>? params}) async {
    final clave = claveDe(ruta, params);
    try {
      final datos = await _api.get(ruta, params: params);
      await _bd.guardarLista(clave, [datos]);
      return ResultadoLista([datos]);
    } on SinConexion {
      final guardada = await _bd.leerLista(clave);
      if (guardada == null || guardada.datos.isEmpty) rethrow;
      return ResultadoLista(guardada.datos, desdeCache: true, guardadoEn: guardada.guardadoEn);
    }
  }
}
