import 'dart:convert';
import 'package:http/http.dart' as http;

import '../seguridad/firmador.dart';
import 'version_app.dart';

/// Error devuelto por la API (formato RFC 9457: title, detail, status, errors).
/// `type` llega como `https://.../docs#problem-<slug>`; [tipo] es el slug.
class ErrorApi implements Exception {
  final int status;
  final String titulo;
  final String detalle;

  /// Sufijo del `type`: invalid-credentials, account-not-approved, rate-limited...
  final String tipo;

  /// Llegó por una clave de solo lectura (modo supervisor, "actuar como").
  final bool soloLectura;

  /// Cuerpo completo del problema (p. ej. `download_url` y `min_version` del
  /// 426 app-update-required).
  final Map<String, dynamic> datos;
  const ErrorApi(this.status, this.titulo, this.detalle, this.tipo, {this.soloLectura = false, this.datos = const {}});

  ErrorApi _deSoloLectura() => ErrorApi(status, titulo, detalle, tipo, soloLectura: true, datos: datos);

  /// 426: el servidor exige una versión más nueva de la app (contrato C1).
  bool get exigeActualizar => status == 426 || tipo == 'app-update-required';

  /// 4xx distintos de 401/429 son errores del dato o de permiso: reintentar
  /// no sirve (la cola lo deja a la vista para corregir o descartar).
  bool get esDefinitivo => status >= 400 && status < 500 && status != 401 && status != 429;
  bool get esNoAutorizado => status == 401;
  bool get esProhibido => status == 403;

  /// El servidor ya no acepta este teléfono: hay que volver a entrar.
  bool get dispositivoRevocado => tipo == 'device-revoked';

  /// La clave es de app pero no tiene dispositivo activo: hay que registrarlo.
  bool get dispositivoNoRegistrado => tipo == 'device-not-registered';

  /// La operación tiene que volver a firmarse (botón en Pendientes).
  bool get pideFirmarDeNuevo => const {'signature-expired', 'signature-required', 'invalid-signature'}.contains(tipo);

  /// Hay versiones nuevas de documentos legales que la persona tiene que
  /// aceptar en el portal antes de seguir (403 legal-acceptance-required).
  bool get pideAceptarDocumentos => tipo == 'legal-acceptance-required';

  /// Texto para la gente de campo, sin jerga.
  String get mensaje => exigeActualizar
      ? 'Esta versión de la app ya no se admite. Actualízala para seguir.'
      : switch (tipo) {
        'invalid-credentials' => 'Correo o contraseña incorrectos.',
        'user-not-allowed' => 'Tu usuario no puede entrar a la app. Escribe a soporte de CamaronMarket.',
        'account-not-approved' => 'Tu cuenta todavía no está aprobada. Te avisaremos por correo cuando lo esté.',
        'account-pending' => detalle.isEmpty
            ? 'Tu cuenta está pendiente de aprobación: mientras tanto solo puedes consultar.'
            : detalle,
        'account-rejected' => detalle.isEmpty ? 'La plataforma rechazó tu cuenta.' : detalle,
        'account-suspended' => detalle.isEmpty ? 'Tu cuenta está suspendida.' : detalle,
        'legal-acceptance-required' => 'Hay documentos legales actualizados que debes aceptar en el portal antes de seguir.',
        'permission-denied' => detalle.isEmpty ? 'El administrador de tu cuenta no te dio permiso para esto.' : detalle,
        'rate-limited' => 'Demasiados intentos seguidos. Espera un momento y vuelve a probar.',
        'missing-api-key' || 'invalid-api-key' => 'La clave de API no es válida.',
        'expired-api-key' => 'La sesión caducó. Vuelve a entrar.',
        'revoked-api-key' => 'La sesión fue cerrada desde el portal. Vuelve a entrar.',
        'insufficient-scope' => soloLectura
            ? 'Solo lectura en modo supervisor: el servidor no permite hacer cambios en nombre del socio.'
            : 'Tu clave no tiene permiso para esta operación.',
        'ip-not-allowed' => 'Esta clave no admite conexiones desde tu red.',
        'invalid-state' => detalle.isEmpty ? 'El registro ya no está en el estado necesario.' : detalle,
        // Firma del dispositivo (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md, sección 5)
        'device-not-registered' => 'Este teléfono no está registrado para confirmar operaciones. Vuelve a protegerlo con tu huella.',
        'signature-required' => 'Falta confirmar esta operación con tu huella o PIN. Hazlo desde Pendientes.',
        'device-revoked' => 'Este teléfono fue desconectado de tu cuenta (desde el portal). Vuelve a entrar.',
        'invalid-signature' =>
          'El servidor no reconoció la confirmación de este teléfono. Vuelve a confirmarla en Pendientes o registra de nuevo el teléfono en Seguridad.',
        'signature-expired' =>
          'La confirmación caducó (pasaron más de 7 días o la hora del teléfono está mal). Vuelve a confirmarla en Pendientes.',
        'not-an-app-key' => 'Esta clave de API no es de la app. Entra con tu correo y contraseña para proteger tu cuenta.',
        'signature-replayed' => 'Esta confirmación ya se usó para otra operación. Descarta este envío y vuelve a hacerlo.',
        // Modo supervisor (Anexo B)
        'supervisor-key' => 'Con la sesión de supervisor solo se puede elegir un socio. Elige uno para ver su app.',
        'supervisor-only' => 'Esta opción es solo para supervisores de la app.',
        'act-as-internal-user' => 'Ese usuario es interno de CamaronMarket: solo se puede ver la app de socios.',
        'partner-not-approved' => 'Ese socio todavía no está aprobado: no se puede ver su app.',
        'partner-without-portal-user' => 'Ese socio no tiene usuario del portal activo: no se puede ver su app.',
        'not-found' => detalle.isNotEmpty ? detalle : 'No se encontró lo que buscabas (puede que ya no exista).',
        'invalid-parameter' => detalle.isNotEmpty ? 'Filtro no válido: $detalle' : 'Filtro no válido.',
        _ => detalle.isNotEmpty ? detalle : titulo,
      };

  @override
  String toString() => '$titulo ($status): $detalle';
}

/// Acción de escritura intentada en modo supervisor (solo lectura): la app
/// la corta antes de encolar o firmar nada.
class ErrorSoloLectura implements Exception {
  const ErrorSoloLectura();
  String get mensaje => 'Solo lectura en modo supervisor: no se puede hacer cambios en nombre del socio.';
  @override
  String toString() => mensaje;
}

class SinConexion implements Exception {
  @override
  String toString() => 'Sin conexión con el servidor';
}

/// Texto claro para cualquier fallo de red o de la API.
String mensajeDeError(Object e) {
  if (e is ErrorApi) return e.mensaje;
  if (e is ErrorSeguridad) return e.mensaje;
  if (e is ErrorSoloLectura) return e.mensaje;
  if (e is SinConexion) return 'Sin internet. Revisa la señal e inténtalo otra vez.';
  if (e is FormatException || e is ArgumentError) return 'La dirección del servidor no es válida.';
  return 'Algo falló. Inténtalo otra vez y, si sigue, avisa a soporte.';
}

/// Texto de un valor JSON que debería ser cadena. Odoo manda `false` en los
/// campos de texto vacíos: aquí eso vale como "sin valor".
String? _texto(dynamic v) => v is String ? v : null;

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

  /// Se llama con 403 device-revoked / device-not-registered.
  void Function(ErrorApi error)? alErrorDispositivo;

  /// Perfil con el que actúa la app ("actuar como"). Va en la cabecera
  /// `X-Shrimp-Role` de cada petición; NO se cambia el perfil activo de la
  /// cuenta en el servidor (eso movería los menús del portal web).
  String? rolActivo;

  /// Clave de solo lectura (modo supervisor): un 403 insufficient-scope se
  /// explica como "solo lectura en modo supervisor".
  final bool soloLectura;

  ApiCliente({required this.baseUrl, required this.clave, http.Client? cliente, this.soloLectura = false})
      : _http = cliente ?? http.Client();

  /// `rol` fuerza el perfil de esta petición (las operaciones de la cola
  /// viajan con el perfil con el que se hicieron, no con el actual).
  Map<String, String> _cabeceras({String? idempotencia, String? rol}) => {
        ...cabecerasApp(),
        'Authorization': 'Bearer $clave',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': ?idempotencia,
        'X-Shrimp-Role': ?(rol ?? rolActivo),
      };

  Uri _uri(String ruta, [Map<String, String>? params]) =>
      Uri.parse('$baseUrl/api/v1$ruta').replace(queryParameters: params);

  /// Se llama con cualquier 426 `app-update-required` (también del login y
  /// de las rutas públicas): la app muestra "Actualiza la app" (C1).
  static void Function(ErrorApi error)? alActualizacionObligatoria;

  /// Se llama con cualquier 403 `legal-acceptance-required` de una petición
  /// con sesión: la app muestra «Acepta los documentos actualizados».
  static void Function(ErrorApi error)? alDocumentosPendientes;

  static Future<Map<String, dynamic>> _enviarSinSesion(Future<http.Response> Function() llamada) async {
    http.Response resp;
    try {
      resp = await llamada().timeout(const Duration(seconds: 40));
    } on FormatException {
      rethrow; // URL mal escrita: no es un problema de señal
    } on ArgumentError {
      rethrow;
    } catch (_) {
      throw SinConexion();
    }
    try {
      return _interpretar(resp);
    } on ErrorApi catch (e) {
      if (e.exigeActualizar) alActualizacionObligatoria?.call(e);
      rethrow;
    }
  }

  /// Petición a una ruta pública (`/public/...`), sin clave de API ni perfil,
  /// con las cabeceras de la app.
  static Future<Map<String, dynamic>> publica(
    String baseUrl,
    String metodo,
    String ruta, {
    Map<String, String>? params,
    Map<String, dynamic>? cuerpo,
    http.Client? cliente,
  }) {
    final uri = Uri.parse('$baseUrl/api/v1$ruta').replace(queryParameters: params);
    final c = cliente ?? http.Client();
    final cabeceras = {...cabecerasApp(), 'Accept': 'application/json', if (cuerpo != null) 'Content-Type': 'application/json'};
    return _enviarSinSesion(() => metodo.toUpperCase() == 'POST'
        ? c.post(uri, headers: cabeceras, body: jsonEncode(cuerpo ?? const {}))
        : c.get(uri, headers: cabeceras));
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
    final tipo = _texto(m['type']) ?? '';
    final errores = (m['errors'] is List ? m['errors'] as List : const [])
        .whereType<Map>()
        .map((e) => '${e['field'] ?? ''}: ${e['message'] ?? ''}')
        .join('; ');
    throw ErrorApi(
      resp.statusCode,
      _texto(m['title']) ?? 'Error',
      [?_texto(m['detail']), if (errores.isNotEmpty) errores].join(' '),
      tipo.split('#').last.replaceFirst(RegExp(r'^problem-'), ''),
      datos: m is Map<String, dynamic> ? m : const {},
    );
  }

  Future<Map<String, dynamic>> _enviar(Future<http.Response> Function() llamada) async {
    try {
      return await _enviarSinSesion(llamada);
    } on ErrorApi catch (e) {
      if (e.esNoAutorizado) alNoAutorizado?.call(e);
      if (e.dispositivoRevocado || e.dispositivoNoRegistrado) alErrorDispositivo?.call(e);
      if (e.pideAceptarDocumentos) alDocumentosPendientes?.call(e);
      if (soloLectura && e.tipo == 'insufficient-scope') throw e._deSoloLectura();
      rethrow;
    }
  }

  Future<Map<String, dynamic>> get(String ruta, {Map<String, String>? params}) =>
      _enviar(() => _http.get(_uri(ruta, params), headers: _cabeceras()));

  Future<Map<String, dynamic>> post(String ruta, Map<String, dynamic> cuerpo, {required String idempotencia, String? rol}) =>
      _enviar(() => _http.post(_uri(ruta), headers: _cabeceras(idempotencia: idempotencia, rol: rol), body: jsonEncode(cuerpo)));

  Future<Map<String, dynamic>> patch(String ruta, Map<String, dynamic> cuerpo, {required String idempotencia, String? rol}) =>
      _enviar(() => _http.patch(_uri(ruta), headers: _cabeceras(idempotencia: idempotencia, rol: rol), body: jsonEncode(cuerpo)));

  /// Envía una operación de la cola con su cuerpo YA SERIALIZADO: los bytes
  /// UTF-8 de [cuerpoSerializado] son exactamente los que se firmaron (no se
  /// vuelve a serializar nada). [firma] son las cabeceras X-Device-Id,
  /// X-Signature-Timestamp y X-Signature (nulas si no hay que firmar).
  Future<Map<String, dynamic>> enviarSerializado(
    String metodo,
    String ruta,
    String cuerpoSerializado, {
    required String idempotencia,
    String? rol,
    Map<String, String>? firma,
  }) {
    final cabeceras = {..._cabeceras(idempotencia: idempotencia, rol: rol), ...?firma};
    final bytes = utf8.encode(cuerpoSerializado);
    return _enviar(() => metodo.toUpperCase() == 'PATCH'
        ? _http.patch(_uri(ruta), headers: cabeceras, body: bytes)
        : _http.post(_uri(ruta), headers: cabeceras, body: bytes));
  }

  /// `POST /me/devices` (no se firma): registra la clave pública del teléfono.
  Future<Map<String, dynamic>> registrarDispositivo({
    required String nombre,
    required String clavePublica,
    required String idempotencia,
  }) =>
      post('/me/devices', {'name': nombre, 'platform': plataformaApp, 'public_key': clavePublica, 'alg': 'ES256'},
          idempotencia: idempotencia);

  /// `GET /me`: comprueba la clave y devuelve quién soy.
  Future<Map<String, dynamic>> quienSoy() => get('/me');

  /// `POST /auth/login` (sin clave, sin `X-Shrimp-Role`, sin
  /// `Idempotency-Key`): correo + contraseña → clave de API y `me`.
  static Future<Map<String, dynamic>> iniciarSesion({
    required String baseUrl,
    required String correo,
    required String contrasena,
    required String dispositivo,
    http.Client? cliente,
  }) =>
      _enviarSinSesion(() => (cliente ?? http.Client()).post(
            Uri.parse('$baseUrl/api/v1/auth/login'),
            headers: {...cabecerasApp(), 'Accept': 'application/json', 'Content-Type': 'application/json'},
            body: jsonEncode({'login': correo, 'password': contrasena, 'device': dispositivo}),
          ));

  /// `POST /auth/logout`: revoca esta clave en el servidor.
  Future<void> cerrarSesion({required String idempotencia}) => post('/auth/logout', {}, idempotencia: idempotencia);

  /// Una página de una lista (cursor opaco de la API).
  Future<PaginaApi> pagina(String ruta, {Map<String, String>? params, String? cursor, int limite = 50}) async {
    final resp = await get(ruta, params: {...?params, 'limit': '$limite', 'cursor': ?cursor});
    final datos = (resp['data'] is List ? resp['data'] as List : const []).whereType<Map<String, dynamic>>().toList();
    final meta = resp['meta'] is Map ? resp['meta'] as Map : const {};
    final siguiente = _texto(meta['next_cursor']);
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
