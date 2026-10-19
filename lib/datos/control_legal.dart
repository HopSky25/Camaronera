import 'package:flutter/foundation.dart';

import 'api_cliente.dart';

String? _texto(dynamic v) => v is String && v.trim().isNotEmpty ? v.trim() : null;

List<String> _lista(dynamic v) => v is List ? v.whereType<String>().where((x) => x.trim().isNotEmpty).toList() : const [];

/// Documentos legales por aceptar (versiones MAYORES de la política de
/// privacidad, los términos…). Mientras falten, la API responde 403
/// `legal-acceptance-required` a todo salvo `GET /me` y cerrar sesión, así
/// que la app muestra una pantalla que lo explica y abre la página del portal
/// para aceptarlos.
class ControlLegal extends ChangeNotifier {
  /// `GET /me` de la sesión actual (o null sin sesión).
  final Future<Map<String, dynamic>> Function()? Function() consultarMe;

  /// En modo supervisor no se muestra: no le toca al supervisor aceptar por
  /// el socio (el error se ve en la pantalla que lo provocó).
  final bool Function() esSupervisor;

  ControlLegal({required this.consultarMe, required this.esSupervisor});

  bool pendiente = false;
  List<String> documentos = const [];
  String? urlAceptar;

  Future<void>? _enCurso;

  void _poner({required bool pendiente, List<String>? documentos, String? url}) {
    final cambia = pendiente != this.pendiente ||
        !listEquals(documentos ?? this.documentos, this.documentos) ||
        (url ?? urlAceptar) != urlAceptar;
    this.pendiente = pendiente;
    if (documentos != null) this.documentos = documentos;
    if (url != null) urlAceptar = url;
    if (cambia) notifyListeners();
  }

  /// Un 403 `legal-acceptance-required` de cualquier petición con sesión.
  void alRecibir(ErrorApi e) {
    if (esSupervisor()) return;
    _poner(
      pendiente: true,
      documentos: _lista(e.datos['pending_documents']),
      url: _texto(e.datos['accept_url']),
    );
  }

  /// Lee el bloque `legal` de `GET /me` (acceptance_required,
  /// pending_documents, accept_url).
  void aplicarMe(Map<String, dynamic> me) {
    final legal = me['legal'];
    if (legal is! Map) return;
    if (esSupervisor()) {
      _poner(pendiente: false);
      return;
    }
    _poner(
      pendiente: legal['acceptance_required'] == true,
      documentos: _lista(legal['pending_documents']),
      url: _texto(legal['accept_url']),
    );
  }

  /// Pregunta al servidor si ya se aceptaron. Nunca lanza.
  Future<void> comprobar() => _enCurso ??= _consultar().whenComplete(() => _enCurso = null);

  Future<void> _consultar() async {
    final consulta = consultarMe();
    if (consulta == null) {
      _poner(pendiente: false);
      return;
    }
    try {
      aplicarMe(await consulta());
    } on SinConexion {
      // Sin señal: se queda como estaba.
    } catch (e) {
      debugPrint('No se pudo comprobar los documentos legales: $e');
    }
  }

  /// Al cerrar sesión (o cambiar de cuenta) no queda nada pendiente.
  void limpiar() => _poner(pendiente: false, documentos: const []);
}
