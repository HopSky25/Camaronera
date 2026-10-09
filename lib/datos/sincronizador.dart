import 'dart:async';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';

import '../modelos/operacion_pendiente.dart';
import '../modelos/verificacion.dart';
import 'almacen_credenciales.dart';
import 'api_cliente.dart';
import 'base_local.dart';

/// Corazón de la app: mantiene la bandeja local, encola lo que el técnico
/// hace sin internet y lo envía al servidor cuando hay señal.
///
/// Regla de oro: NADA de lo que el técnico captura se pierde si no hay
/// internet. Primero se guarda en el teléfono; el envío es aparte.
class Sincronizador extends ChangeNotifier {
  final BaseLocal _bd;
  final AlmacenCredenciales _credenciales;
  final _uuid = const Uuid();

  ApiCliente? _api;
  StreamSubscription<List<ConnectivityResult>>? _escuchaRed;

  List<Verificacion> bandeja = [];
  int pendientes = 0;
  bool sincronizando = false;
  bool hayRed = true;
  String? ultimoMensaje;
  DateTime? ultimaSincronizacion;
  Map<String, dynamic>? quienSoy;

  Sincronizador({BaseLocal? bd, AlmacenCredenciales? credenciales})
      : _bd = bd ?? BaseLocal(),
        _credenciales = credenciales ?? AlmacenCredenciales();

  bool get configurado => _api != null;

  /// Al abrir la app: carga lo guardado y, si hay clave, intenta sincronizar.
  Future<void> iniciar() async {
    final url = await _credenciales.url();
    final clave = await _credenciales.clave();
    if (url != null && clave != null && clave.isNotEmpty) {
      _api = ApiCliente(baseUrl: url, clave: clave);
    }
    final guardado = await _bd.leerConfig('ultima_sincronizacion');
    ultimaSincronizacion = guardado == null ? null : DateTime.tryParse(guardado);
    await recargarLocal();
    _escuchaRed ??= Connectivity().onConnectivityChanged.listen((estados) {
      final antes = hayRed;
      hayRed = !estados.contains(ConnectivityResult.none);
      notifyListeners();
      if (hayRed && !antes) sincronizar();
    });
    if (_api != null) unawaited(sincronizar());
  }

  /// Guarda servidor y clave, y comprueba que la clave sirva (`GET /me`).
  Future<Map<String, dynamic>> conectar({required String url, required String clave}) async {
    await _credenciales.guardar(url: url, clave: clave);
    final api = ApiCliente(baseUrl: (await _credenciales.url())!, clave: clave.trim());
    final yo = await api.quienSoy();
    _api = api;
    quienSoy = yo;
    notifyListeners();
    unawaited(sincronizar());
    return yo;
  }

  Future<void> cerrarSesion() async {
    await _credenciales.borrar();
    await _bd.borrarTodo();
    _api = null;
    bandeja = [];
    pendientes = 0;
    quienSoy = null;
    notifyListeners();
  }

  Future<void> recargarLocal() async {
    bandeja = await _bd.verificaciones();
    pendientes = await _bd.pendientes();
    notifyListeners();
  }

  /// 1) Sube la cola en orden. 2) Baja la bandeja del técnico.
  Future<void> sincronizar() async {
    final api = _api;
    if (api == null || sincronizando) return;
    sincronizando = true;
    ultimoMensaje = null;
    notifyListeners();
    try {
      await _subirCola(api);
      await _bajarBandeja(api);
      ultimaSincronizacion = DateTime.now();
      await _bd.escribirConfig('ultima_sincronizacion', ultimaSincronizacion!.toUtc().toIso8601String());
      hayRed = true;
      ultimoMensaje ??= 'Sincronizado';
    } on SinConexion {
      hayRed = false;
      ultimoMensaje = 'Sin internet: lo capturado queda guardado en el teléfono';
    } on ErrorApi catch (e) {
      ultimoMensaje = e.toString();
    } finally {
      sincronizando = false;
      await recargarLocal();
    }
  }

  Future<void> _subirCola(ApiCliente api) async {
    for (final op in await _bd.cola()) {
      try {
        final resp = op.metodo == 'PATCH'
            ? await api.patch(op.ruta, op.cuerpo, idempotencia: op.claveIdempotencia)
            : await api.post(op.ruta, op.cuerpo, idempotencia: op.claveIdempotencia);
        await _bd.quitarDeCola(op.idLocal!);
        // La respuesta trae la verificación/despacho al día: se guarda.
        if (resp['id'] != null && op.tipo != TipoOperacion.llegadaCamion) {
          await _bd.guardarVerificaciones([resp]);
          await _bd.guardarInformeLocal(op.verificacionId, InformeCampo.desdeServidor(resp), modificado: false);
        }
      } on ErrorApi catch (e) {
        await _bd.anotarError(op.idLocal!, op.intentos + 1, e.toString());
        if (e.esDefinitivo) {
          // El servidor rechazó el dato: se deja en la cola con el error a la
          // vista para que el técnico lo corrija; se sigue con las demás.
          ultimoMensaje = '${op.descripcion}: ${e.detalle}';
          continue;
        }
        rethrow; // 401/403/429/5xx: se para aquí y se reintenta en la próxima pasada
      }
    }
  }

  Future<void> _bajarBandeja(ApiCliente api) async {
    // La clave puede ser de un técnico (ve lo suyo) o de la empresa
    // verificadora (ve todo lo de su empresa y puede actuar). Se piden ambas
    // bandejas y se juntan sin repetir.
    final porId = <String, Map<String, dynamic>>{};
    for (final rol in ['technician', 'verifier']) {
      for (final v in await api.listarTodo('/verifications', params: {'role': rol})) {
        porId[v['id'] as String] = v;
      }
    }
    final lista = porId.values.toList();
    await _bd.guardarVerificaciones(lista);
    // El detalle (pesos, líneas, conteos) solo de las que están abiertas.
    for (final v in lista) {
      final estado = v['state'] as String? ?? '';
      if (const {'received', 'assigned', 'in_field', 'done'}.contains(estado)) {
        final detalle = await api.get('/verifications/${v['id']}');
        await _bd.guardarVerificaciones([detalle]);
        if (await _bd.informeLocal(v['id'] as String) == null) {
          await _bd.guardarInformeLocal(v['id'] as String, InformeCampo.desdeServidor(detalle), modificado: false);
        }
      }
    }
  }

  // ---------------- acciones del técnico (siempre primero en local) ----------------

  Future<void> _encolar(TipoOperacion tipo, String verificacionId, Map<String, dynamic> cuerpo, {String? despachoId}) async {
    await _bd.encolar(OperacionPendiente(
      tipo: tipo,
      verificacionId: verificacionId,
      despachoId: despachoId,
      cuerpo: cuerpo,
      claveIdempotencia: _uuid.v4(),
      creadaEn: DateTime.now(),
    ));
    await recargarLocal();
    unawaited(sincronizar());
  }

  Future<void> iniciarCampo(Verificacion v) async {
    await _bd.cambiarEstadoLocal(v.id, 'in_field');
    await _encolar(TipoOperacion.iniciarCampo, v.id, {});
  }

  Future<void> guardarInforme(Verificacion v, InformeCampo informe, {bool enviar = true}) async {
    await _bd.guardarInformeLocal(v.id, informe, modificado: true);
    if (enviar) await _encolar(TipoOperacion.informe, v.id, informe.aCuerpoApi());
    await recargarLocal();
  }

  Future<void> darVeredicto(Verificacion v, String veredicto, String notas) async {
    final estado = {'approve': 'approved', 'approve_obs': 'approved_obs', 'reject': 'rejected'}[veredicto]!;
    await _bd.cambiarEstadoLocal(v.id, estado);
    await _encolar(TipoOperacion.veredicto, v.id, {'verdict': veredicto, if (notas.trim().isNotEmpty) 'notes': notas.trim()});
  }

  Future<void> registrarLlegadaCamion(Verificacion v, DateTime cuando) async {
    await _encolar(TipoOperacion.llegadaCamion, v.id, {'actual_arrival': cuando.toUtc().toIso8601String()},
        despachoId: v.despachoId);
  }

  Future<InformeCampo> informeDe(Verificacion v) async =>
      await _bd.informeLocal(v.id) ?? InformeCampo.desdeServidor(v.crudo);

  Future<List<OperacionPendiente>> cola() => _bd.cola();
  Future<void> descartarPendiente(int idLocal) async {
    await _bd.quitarDeCola(idLocal);
    await recargarLocal();
  }

  Future<void> guardarFoto(Verificacion v, String ruta) => _bd.guardarFoto(v.id, ruta);
  Future<List<String>> fotosDe(Verificacion v) => _bd.fotos(v.id);

  @override
  void dispose() {
    _escuchaRed?.cancel();
    _api?.cerrar();
    super.dispose();
  }
}
