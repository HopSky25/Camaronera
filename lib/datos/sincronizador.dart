import 'dart:async';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';

import '../modelos/operacion_pendiente.dart';
import '../modelos/perfil.dart';
import '../modelos/verificacion.dart';
import 'api_cliente.dart';
import 'base_local.dart';
import 'sesion.dart';

/// Qué pasó con una operación que se mandó a la cola.
class ResultadoEnvio {
  /// Llegó al servidor y se aplicó.
  final bool enviado;

  /// El servidor la rechazó (queda en Pendientes con el error a la vista).
  final String? error;
  const ResultadoEnvio({required this.enviado, this.error});

  /// Guardada en el teléfono, se enviará cuando haya señal.
  bool get enEspera => !enviado && error == null;
}

/// Corazón de la app: mantiene la bandeja local (técnico y verificadora),
/// encola lo que se hace sin internet y lo envía al servidor cuando hay señal.
///
/// Regla de oro: NADA de lo que se captura se pierde si no hay internet.
/// Primero se guarda en el teléfono; el envío es aparte. Los perfiles en
/// línea (camaronera, empacadora, maquilador) usan la misma cola para sus
/// acciones (aceptar informe, programar despacho, ofertar...).
class Sincronizador extends ChangeNotifier {
  final BaseLocal _bd;
  final Sesion sesion;
  final _uuid = const Uuid();

  ApiCliente? _api;
  StreamSubscription<List<ConnectivityResult>>? _escuchaRed;
  Future<void>? _enCurso;

  List<Verificacion> bandeja = [];
  int pendientes = 0;
  bool sincronizando = false;
  bool hayRed = true;
  String? ultimoMensaje;
  DateTime? ultimaSincronizacion;

  Sincronizador({required this.sesion, BaseLocal? bd}) : _bd = bd ?? BaseLocal() {
    sesion.addListener(_alCambiarSesion);
  }

  bool get configurado => _api != null;

  /// La bandeja de verificaciones solo la bajan el técnico y la verificadora.
  bool get _usaBandeja => sesion.perfil == perfilTecnico || sesion.perfil == 'verificador';

  void _alCambiarSesion() {
    final nuevo = sesion.api;
    if (identical(nuevo, _api)) {
      if (_api != null && _usaBandeja && bandeja.isEmpty) unawaited(sincronizar());
      return;
    }
    _api = nuevo;
    ultimoMensaje = null;
    unawaited(recargarLocal().then((_) {
      if (_api != null) unawaited(sincronizar());
    }));
  }

  /// Al abrir la app: carga lo guardado y, si hay sesión, intenta sincronizar.
  Future<void> iniciar() async {
    _api = sesion.api;
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

  Future<void> recargarLocal() async {
    bandeja = await _bd.verificaciones();
    pendientes = await _bd.pendientes();
    notifyListeners();
  }

  /// 1) Sube la cola en orden. 2) Baja la bandeja (técnico / verificadora).
  /// Si ya hay una pasada en curso, se espera a esa.
  Future<void> sincronizar() {
    if (_api == null) return Future.value();
    return _enCurso ??= _sincronizar().whenComplete(() => _enCurso = null);
  }

  Future<void> _sincronizar() async {
    final api = _api!;
    sincronizando = true;
    ultimoMensaje = null;
    notifyListeners();
    try {
      await _subirCola(api);
      if (_usaBandeja) await _bajarBandeja(api);
      ultimaSincronizacion = DateTime.now();
      await _bd.escribirConfig('ultima_sincronizacion', ultimaSincronizacion!.toUtc().toIso8601String());
      hayRed = true;
      ultimoMensaje ??= 'Sincronizado';
    } on SinConexion {
      hayRed = false;
      ultimoMensaje = 'Sin internet: lo capturado queda guardado en el teléfono';
    } on ErrorApi catch (e) {
      ultimoMensaje = e.mensaje;
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
        // La respuesta trae la verificación al día: se guarda en la bandeja.
        if (resp['id'] != null && op.respondeVerificacion && _usaBandeja) {
          await _bd.guardarVerificaciones([resp]);
          if (op.tipo == TipoOperacion.informe) {
            await _bd.guardarInformeLocal(op.recursoId, InformeCampo.desdeServidor(resp), modificado: false);
          }
        }
      } on ErrorApi catch (e) {
        await _bd.anotarError(op.idLocal!, op.intentos + 1, e.mensaje);
        if (e.esDefinitivo) {
          // El servidor rechazó el dato: se deja en la cola con el error a la
          // vista para que el usuario lo corrija; se sigue con las demás.
          ultimoMensaje = '${op.descripcion}: ${e.mensaje}';
          continue;
        }
        rethrow; // 401/429/5xx: se para aquí y se reintenta en la próxima pasada
      }
    }
  }

  Future<void> _bajarBandeja(ApiCliente api) async {
    // La cuenta puede ser de un técnico (ve lo suyo) o de la empresa
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
    await _bd.conservarSoloVerificaciones(porId.keys);
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

  // ---------------- cola (siempre primero en local) ----------------

  Future<int> _encolar(TipoOperacion tipo, String recursoId, Map<String, dynamic> cuerpo, {String? despachoId}) async {
    final idLocal = await _bd.encolar(OperacionPendiente(
      tipo: tipo,
      recursoId: recursoId,
      despachoId: despachoId,
      cuerpo: cuerpo,
      claveIdempotencia: _uuid.v4(),
      creadaEn: DateTime.now(),
    ));
    await recargarLocal();
    return idLocal;
  }

  /// Encola y, si hay señal, envía de inmediato. Devuelve qué pasó para que
  /// la pantalla lo diga con claridad.
  Future<ResultadoEnvio> ejecutar(TipoOperacion tipo, String recursoId, Map<String, dynamic> cuerpo,
      {String? despachoId}) async {
    final idLocal = await _encolar(tipo, recursoId, cuerpo, despachoId: despachoId);
    await sincronizar();
    var op = await _bd.operacion(idLocal);
    if (op != null && op.ultimoError == null && hayRed && _api != null) {
      // La pasada anterior ya iba por la bandeja cuando se encoló: otra pasada.
      await sincronizar();
      op = await _bd.operacion(idLocal);
    }
    if (op == null) return const ResultadoEnvio(enviado: true);
    return ResultadoEnvio(enviado: false, error: op.ultimoError);
  }

  // ---------------- acciones del técnico ----------------

  Future<void> iniciarCampo(Verificacion v) async {
    await _bd.cambiarEstadoLocal(v.id, 'in_field');
    await _encolar(TipoOperacion.iniciarCampo, v.id, {});
    unawaited(sincronizar());
  }

  Future<void> guardarInforme(Verificacion v, InformeCampo informe, {bool enviar = true}) async {
    await _bd.guardarInformeLocal(v.id, informe, modificado: true);
    if (enviar) {
      await _encolar(TipoOperacion.informe, v.id, informe.aCuerpoApi());
      unawaited(sincronizar());
    }
    await recargarLocal();
  }

  Future<void> darVeredicto(Verificacion v, String veredicto, String notas) async {
    final estado = {'approve': 'approved', 'approve_obs': 'approved_obs', 'reject': 'rejected'}[veredicto]!;
    await _bd.cambiarEstadoLocal(v.id, estado);
    await _encolar(TipoOperacion.veredicto, v.id, {'verdict': veredicto, if (notas.trim().isNotEmpty) 'notes': notas.trim()});
    unawaited(sincronizar());
  }

  Future<void> registrarLlegadaCamion(Verificacion v, DateTime cuando) async {
    await _encolar(TipoOperacion.llegadaCamion, v.id, {'actual_arrival': cuando.toUtc().toIso8601String()},
        despachoId: v.despachoId);
    unawaited(sincronizar());
  }

  // ---------------- acciones de la verificadora ----------------

  Future<ResultadoEnvio> asignarTecnico(Verificacion v, String tecnicoId) =>
      ejecutar(TipoOperacion.asignarTecnico, v.id, {'technician': tecnicoId});

  /// Técnicos conocidos: los que aparecen asignados en la bandeja de la
  /// empresa (la API no tiene una lista de técnicos; se gestionan en el portal).
  List<Referencia> tecnicosConocidos() {
    final porId = <String, Referencia>{};
    for (final v in bandeja) {
      final t = v.tecnico;
      if (t != null && t.id != null) porId[t.id!] = t;
    }
    return porId.values.toList()..sort((a, b) => a.nombre.compareTo(b.nombre));
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
    sesion.removeListener(_alCambiarSesion);
    _escuchaRed?.cancel();
    super.dispose();
  }
}
