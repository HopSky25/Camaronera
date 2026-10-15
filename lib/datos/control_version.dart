import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'api_cliente.dart';
import 'version_app.dart';

/// Compara dos versiones `x.y.z` (semántica, solo números; lo que vaya tras
/// `-` o `+` no cuenta y las partes que falten valen 0). Negativo si [a] es
/// menor, 0 si son iguales, positivo si [a] es mayor. Lo que no se pueda
/// leer cuenta como 0.
int compararVersiones(String a, String b) {
  List<int> partes(String v) {
    final limpia = v.trim().replaceFirst(RegExp(r'^[vV]'), '').split(RegExp(r'[-+]')).first;
    final p = limpia.split('.').map((x) => int.tryParse(RegExp(r'^\d+').stringMatch(x) ?? '') ?? 0).toList();
    while (p.length < 3) {
      p.add(0);
    }
    return p;
  }

  final pa = partes(a);
  final pb = partes(b);
  for (var i = 0; i < 3; i++) {
    if (pa[i] != pb[i]) return pa[i].compareTo(pb[i]);
  }
  return 0;
}

/// `true` si [version] es estrictamente menor que [otra].
bool esVersionMenor(String version, String otra) => compararVersiones(version, otra) < 0;

String? _texto(dynamic v) => v is String && v.trim().isNotEmpty ? v.trim() : null;

/// Respuesta de `GET /public/app-version` (contrato C1).
class InfoVersion {
  final String? minVersion;
  final String? latestVersion;
  final String? urlDescarga;
  final String? mensaje;
  const InfoVersion({this.minVersion, this.latestVersion, this.urlDescarga, this.mensaje});

  factory InfoVersion.desde(Map<String, dynamic> m) => InfoVersion(
        minVersion: _texto(m['min_version']),
        latestVersion: _texto(m['latest_version']),
        urlDescarga: _texto(m['download_url']),
        mensaje: _texto(m['message']),
      );
}

/// Versión mínima obligatoria (C1): se consulta al abrir la app y al volver
/// del segundo plano (como mucho una vez por hora) y se activa también con
/// cualquier 426 `app-update-required` de la API.
class ControlVersion extends ChangeNotifier {
  /// Servidor al que se pregunta (el de la sesión).
  final String Function() servidor;
  final http.Client? _http;
  final DateTime Function() _reloj;

  /// Versión de esta app (en pruebas se puede cambiar).
  final String version;

  static const intervalo = Duration(hours: 1);

  ControlVersion({required this.servidor, http.Client? cliente, DateTime Function()? reloj, this.version = versionApp})
      : _http = cliente,
        _reloj = reloj ?? DateTime.now;

  DateTime? _ultimaConsulta;
  Future<void>? _enCurso;

  /// La versión instalada ya no se admite: pantalla bloqueante.
  bool obligatoria = false;
  String? minVersion;
  String? urlDescarga;
  String? mensaje;

  /// Versión más nueva publicada (no obligatoria).
  String? versionNueva;
  String? _descartada;

  /// Aviso discreto: hay una versión más nueva y no se ha descartado.
  bool get hayNueva => !obligatoria && versionNueva != null && versionNueva != _descartada;

  /// Cuántas veces se preguntó al servidor (para las pruebas).
  int consultas = 0;

  /// `GET /public/app-version?platform=…`. Sin [forzar], no pregunta si ya
  /// lo hizo hace menos de [intervalo]. Nunca lanza.
  Future<void> comprobar({bool forzar = false}) {
    final ultima = _ultimaConsulta;
    if (!forzar && ultima != null && _reloj().difference(ultima) < intervalo) return Future.value();
    return _enCurso ??= _consultar().whenComplete(() => _enCurso = null);
  }

  Future<void> _consultar() async {
    try {
      consultas++;
      final r = await ApiCliente.publica(servidor(), 'GET', '/public/app-version',
          params: {'platform': plataformaApp}, cliente: _http);
      _ultimaConsulta = _reloj();
      aplicar(InfoVersion.desde(r));
    } on SinConexion {
      // Sin señal: se vuelve a intentar al volver a la app.
    } on ErrorApi catch (e) {
      // 426 ya lo trató alActualizacionObligatoria; otro error (p. ej. un
      // servidor sin esta ruta) no bloquea a nadie.
      if (!e.exigeActualizar) _ultimaConsulta = _reloj();
    } catch (e) {
      debugPrint('No se pudo comprobar la versión: $e');
    }
  }

  /// Aplica lo que dijo el servidor (manda sobre un 426 anterior: si ya no
  /// hay mínimo, se desbloquea).
  void aplicar(InfoVersion info) {
    final min = info.minVersion;
    obligatoria = min != null && esVersionMenor(version, min);
    minVersion = min;
    urlDescarga = info.urlDescarga ?? urlDescarga;
    mensaje = info.mensaje;
    final ultima = info.latestVersion;
    versionNueva = ultima != null && esVersionMenor(version, ultima) ? ultima : null;
    notifyListeners();
  }

  /// Un 426 `app-update-required` de cualquier petición.
  void alRecibir426(ErrorApi e) {
    obligatoria = true;
    minVersion = _texto(e.datos['min_version']) ?? minVersion;
    urlDescarga = _texto(e.datos['download_url']) ?? urlDescarga;
    mensaje = _texto(e.datos['message']) ?? (e.detalle.isNotEmpty ? e.detalle : mensaje);
    notifyListeners();
  }

  /// "Ahora no" en el aviso de versión nueva (hasta que salga otra).
  void descartar() {
    _descartada = versionNueva;
    notifyListeners();
  }
}
