import 'dart:async';
import 'dart:convert';
import 'dart:io' show Platform;

import 'package:crypto/crypto.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:http/http.dart' as http;

import 'api_cliente.dart';
import 'base_local.dart';
import 'version_app.dart';

/// Límites del contrato C2.
const largoMaximoError = 4000;
const largoMaximoStack = 20000;

/// Máximo de reportes enviados por día y por instalación.
const reportesPorDia = 20;

/// Quita de un texto lo que pueda identificar a una persona o dar acceso a
/// su cuenta: correos, claves de API (`trz_…`), cabeceras `Bearer`,
/// contraseñas, números largos (cédula, RUC, teléfono) y los parámetros de
/// las direcciones. El reporte no lleva usuario, correo, clave ni contenido
/// de formularios (contrato C2).
String limpiarDatosPersonales(String texto) {
  var t = texto;
  // Cuantificadores acotados: con un texto enorme sin espacios no se
  // dispara el tiempo (sin retroceso cuadrático).
  t = t.replaceAll(RegExp(r'[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}'), '<correo>');
  t = t.replaceAll(RegExp(r'trz_[A-Za-z0-9_\-]+'), '<clave>');
  t = t.replaceAll(RegExp(r'Bearer\s+[^\s,;"]+', caseSensitive: false), 'Bearer <clave>');
  t = t.replaceAllMapped(
      RegExp(r'''("?(?:password|contrasena|contraseña|api_key|token|secret|login)"?\s*[:=]\s*)("[^"]*"|[^\s,;}&]+)''', caseSensitive: false),
      (m) => '${m[1]}<oculto>');
  // Parámetros de una URL (?q=nombre, ?login=...): fuera.
  t = t.replaceAllMapped(RegExp(r'(https?://[^\s?#]+)\?[^\s#]*'), (m) => '${m[1]}?<parametros>');
  // Cédula (10), RUC (13), teléfonos: 7 o más dígitos seguidos.
  t = t.replaceAll(RegExp(r'(?<![\d.])\+?\d{7,}(?![\d.])'), '<numero>');
  return t;
}

String _recortar(String s, int maximo) => s.length <= maximo ? s : s.substring(0, maximo);

/// Texto del error sin datos personales. De un [ErrorApi] solo viaja el
/// estado y el tipo (el `detail` del servidor puede repetir lo que se
/// escribió en un formulario).
String textoDelError(Object error) {
  final crudo = switch (error) {
    ErrorApi e => 'ErrorApi(${e.status}, ${e.tipo.isEmpty ? 'sin-tipo' : e.tipo})',
    SinConexion _ => 'SinConexion',
    _ => '${error.runtimeType}: $error',
  };
  return _recortar(limpiarDatosPersonales(_recortar(crudo, largoMaximoError)), largoMaximoError);
}

/// Primera línea "útil" de la pila: la primera de código de la app.
String? primeraLineaUtil(String stack) {
  for (final l in const LineSplitter().convert(stack)) {
    if (l.contains('package:verificador_campo/')) return l.trim();
  }
  for (final l in const LineSplitter().convert(stack)) {
    if (l.trim().isNotEmpty) return l.trim();
  }
  return null;
}

/// Pantalla en la que pasó: el primer archivo de `lib/pantallas/` de la pila
/// (p. ej. `empacadora/pantalla_catalogo`) o la última ruta con nombre.
String pantallaDelError(String stack, String? rutaActual) {
  final m = RegExp(r'package:verificador_campo/pantallas/([A-Za-z0-9_/]+)\.dart').firstMatch(stack);
  if (m != null) return m[1]!;
  return rutaActual ?? 'desconocida';
}

/// Lo que se sabe del teléfono sin datos de la persona.
class InfoEquipo {
  final String sistema;
  final String modelo;
  const InfoEquipo(this.sistema, this.modelo);
}

Future<InfoEquipo> infoEquipoReal() async {
  try {
    if (Platform.isAndroid) {
      final a = await DeviceInfoPlugin().androidInfo;
      return InfoEquipo('Android ${a.version.release} (SDK ${a.version.sdkInt})', '${a.manufacturer} ${a.model}'.trim());
    }
    if (Platform.isIOS) {
      final i = await DeviceInfoPlugin().iosInfo;
      return InfoEquipo('${i.systemName} ${i.systemVersion}', i.utsname.machine);
    }
  } catch (_) {
    // Sin información del equipo: se manda lo genérico.
  }
  return InfoEquipo(Platform.operatingSystem, 'desconocido');
}

/// Guarda la última ruta con nombre (para [pantallaDelError]).
class ObservadorPantallas extends NavigatorObserver {
  static String? rutaActual;

  void _anotar(Route<dynamic>? r) {
    final nombre = r?.settings.name;
    if (nombre != null && nombre.isNotEmpty) rutaActual = nombre;
  }

  @override
  void didPush(Route<dynamic> route, Route<dynamic>? previousRoute) => _anotar(route);
  @override
  void didPop(Route<dynamic> route, Route<dynamic>? previousRoute) => _anotar(previousRoute);
  @override
  void didReplace({Route<dynamic>? newRoute, Route<dynamic>? oldRoute}) => _anotar(newRoute);
}

/// Reporte de errores de la app sin terceros (C2): captura los errores no
/// controlados, los guarda en una cola local (SQLite) y los envía a
/// `POST /public/app-errors` cuando hay señal, como mucho [reportesPorDia]
/// al día. Cada reporte lleva SOLO los campos del contrato.
class ReporteErrores {
  final BaseLocal _bd;
  final http.Client? _http;
  final DateTime Function() _reloj;
  final Future<InfoEquipo> Function() _info;

  /// Servidor al que se envía (el de la sesión; se fija al abrir la app).
  String Function() servidor;

  /// En depuración no se reporta nada (los errores del desarrollo no son
  /// del campo).
  final bool habilitado;

  ReporteErrores({
    BaseLocal? bd,
    http.Client? cliente,
    DateTime Function()? reloj,
    Future<InfoEquipo> Function()? info,
    String Function()? servidor,
    this.habilitado = true,
  })  : _bd = bd ?? BaseLocal(),
        _http = cliente,
        _reloj = reloj ?? DateTime.now,
        _info = info ?? infoEquipoReal,
        servidor = servidor ?? (() => 'https://demo.camaronmarket.com');

  InfoEquipo? _equipo;
  Future<void>? _enviando;

  /// Arma el cuerpo del contrato (exactamente estos 8 campos).
  Future<Map<String, dynamic>> armar(Object error, StackTrace? stack, {DateTime? cuando}) async {
    final equipo = _equipo ??= await _info();
    final pila = _recortar(limpiarDatosPersonales(_recortar(stack?.toString() ?? '', largoMaximoStack)), largoMaximoStack);
    return {
      'app_version': versionApp,
      'platform': plataformaApp,
      'os_version': _recortar(equipo.sistema, 120),
      'device_model': _recortar(equipo.modelo, 120),
      'screen': pantallaDelError(pila, ObservadorPantallas.rutaActual),
      'error': textoDelError(error),
      'stack': pila,
      'occurred_at': (cuando ?? _reloj()).toUtc().toIso8601String(),
    };
  }

  /// Huella del error para no encolar dos veces lo mismo (error + primera
  /// línea útil de la pila, como agrupa el servidor).
  static String huellaDe(Map<String, dynamic> cuerpo) =>
      sha256.convert(utf8.encode('${cuerpo['error']}\n${primeraLineaUtil('${cuerpo['stack']}') ?? ''}')).toString();

  /// Captura un error no controlado. Nunca lanza (un fallo aquí no puede
  /// tumbar la app ni volver a reportarse a sí mismo).
  Future<void> capturar(Object error, StackTrace? stack) async {
    if (!habilitado) return;
    try {
      final cuerpo = await armar(error, stack);
      await _bd.encolarError(huellaDe(cuerpo), jsonEncode(cuerpo));
    } catch (e) {
      debugPrint('No se pudo guardar el reporte de error: $e');
    }
    unawaited(enviar());
  }

  static String _dia(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  /// Envía lo pendiente (con señal), sin pasar de [reportesPorDia] al día.
  /// Nunca lanza.
  Future<void> enviar() {
    if (!habilitado) return Future.value();
    return _enviando ??= _enviar().whenComplete(() => _enviando = null);
  }

  Future<void> _enviar() async {
    try {
      for (final (id, texto) in await _bd.erroresPendientes()) {
        final dia = _dia(_reloj());
        if (await _bd.erroresEnviados(dia) >= reportesPorDia) return;
        Map<String, dynamic> cuerpo;
        try {
          cuerpo = jsonDecode(texto) as Map<String, dynamic>;
        } catch (_) {
          await _bd.quitarError(id);
          continue;
        }
        try {
          await ApiCliente.publica(servidor(), 'POST', '/public/app-errors', cuerpo: cuerpo, cliente: _http);
          await _bd.quitarError(id);
          await _bd.contarErrorEnviado(dia);
        } on ErrorApi catch (e) {
          // 400/422: el servidor nunca lo va a aceptar; se descarta.
          if (e.status == 400 || e.status == 413 || e.status == 422) {
            await _bd.quitarError(id);
            continue;
          }
          return; // 429, 5xx, 426...: se reintenta más tarde
        }
      }
    } on SinConexion {
      // Sin señal: queda en la cola.
    } catch (e) {
      debugPrint('No se pudieron enviar los reportes de error: $e');
    }
  }

  /// Engancha la captura global: errores de Flutter (build, layout,
  /// pintura) y de la plataforma. Los de las zonas asíncronas llegan por
  /// `runZonedGuarded(…, capturar)` en main.dart.
  void instalar() {
    final anterior = FlutterError.onError;
    FlutterError.onError = (detalles) {
      anterior?.call(detalles);
      capturar(detalles.exception, detalles.stack);
    };
    PlatformDispatcher.instance.onError = (error, stack) {
      capturar(error, stack);
      return true;
    };
  }
}
