import 'dart:async';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';

import '../modelos/cuerpos.dart';
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
///
/// Orden de envío: el de la cola (el orden en que se hizo cada cosa). Si una
/// operación es rechazada por el servidor, las siguientes sobre el MISMO
/// recurso se quedan esperando (asignar → iniciar → informe → veredicto no
/// se mandan fuera de orden); las de otros recursos siguen.
class Sincronizador extends ChangeNotifier {
  final BaseLocal _bd;
  final Sesion sesion;
  final _uuid = const Uuid();

  ApiCliente? _api;
  String? _perfil;
  StreamSubscription<List<ConnectivityResult>>? _escuchaRed;
  Future<void>? _enCurso;

  List<Verificacion> bandeja = [];

  /// La cola tal como está en el teléfono, en orden de envío.
  List<OperacionPendiente> cola = [];
  int get pendientes => cola.length;
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
    final cambioPerfil = sesion.perfil != _perfil;
    _perfil = sesion.perfil;
    if (identical(nuevo, _api)) {
      // Misma sesión: si cambió el perfil (o acaba de fijarse) se refresca.
      if (_api != null && cambioPerfil && sesion.perfil != null) {
        unawaited(recargarLocal().then((_) => sincronizar()));
      }
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
    _perfil = sesion.perfil;
    await recargarLocal();
    _escuchaRed ??= Connectivity().onConnectivityChanged.listen((estados) {
      final antes = hayRed;
      hayRed = !estados.contains(ConnectivityResult.none);
      notifyListeners();
      if (hayRed && !antes) unawaited(sincronizar());
    });
    if (_api != null) unawaited(sincronizar());
  }

  Future<void> recargarLocal() async {
    try {
      bandeja = await _bd.verificaciones();
      cola = await _bd.cola();
      final guardado = await _bd.leerConfig('ultima_sincronizacion');
      ultimaSincronizacion = guardado == null ? null : DateTime.tryParse(guardado)?.toLocal();
    } catch (e) {
      debugPrint('No se pudo leer la base local: $e');
    }
    notifyListeners();
  }

  /// 1) Sube la cola en orden. 2) Baja la bandeja (técnico / verificadora).
  /// Si ya hay una pasada en curso, se espera a esa. Nunca lanza: lo que
  /// pase queda en [ultimoMensaje].
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
      ultimoMensaje = e.esNoAutorizado ? 'La sesión caducó. Vuelve a entrar para enviar lo pendiente.' : e.mensaje;
    } catch (e) {
      debugPrint('Fallo al sincronizar: $e');
      ultimoMensaje = 'No se pudo sincronizar. Inténtalo otra vez.';
    } finally {
      sincronizando = false;
      await recargarLocal();
    }
  }

  Future<void> _subirCola(ApiCliente api) async {
    // Recursos con una operación rechazada en esta pasada: las que vienen
    // después sobre el mismo recurso esperan a que se corrija o descarte.
    final bloqueados = <String>{};
    for (final op in await _bd.cola()) {
      if (bloqueados.contains(op.recursoId)) continue;
      if (op.tipo == TipoOperacion.llegadaCamion && (op.despachoId ?? '').isEmpty) {
        await _bd.anotarError(op.idLocal!, op.intentos + 1, 'Esta verificación no tiene despacho asociado.');
        continue;
      }
      try {
        final resp = op.metodo == 'PATCH'
            ? await api.patch(op.ruta, op.cuerpo, idempotencia: op.claveIdempotencia, rol: op.rol)
            : await api.post(op.ruta, op.cuerpo, idempotencia: op.claveIdempotencia, rol: op.rol);
        await _bd.quitarDeCola(op.idLocal!);
        // La respuesta trae la verificación al día: se guarda en la bandeja.
        if (op.respondeVerificacion && _usaBandeja && Verificacion.tieneId(resp)) {
          await _bd.guardarVerificaciones([resp]);
          if (op.tipo == TipoOperacion.informe) {
            await _bd.guardarInformeLocal(op.recursoId, InformeCampo.desdeServidor(resp), modificado: false);
          }
        }
      } on ErrorApi catch (e) {
        await _bd.anotarError(op.idLocal!, op.intentos + 1, e.mensaje);
        if (e.esDefinitivo) {
          // El servidor rechazó el dato: se deja en la cola con el error a la
          // vista para que el usuario lo corrija o lo descarte. Lo que dependa
          // de este recurso espera; lo demás sigue.
          bloqueados.add(op.recursoId);
          if (op.tipo == TipoOperacion.iniciarCampo) {
            // El estado local se había adelantado a "en campo": se revierte.
            await _bd.cambiarEstadoLocal(op.recursoId, 'assigned');
          }
          ultimoMensaje = '${op.descripcion}: ${e.mensaje}';
          continue;
        }
        rethrow; // 401/429/5xx: se para aquí y se reintenta en la próxima pasada
      }
    }
  }

  Future<void> _bajarBandeja(ApiCliente api) async {
    // El técnico ve lo suyo (`role=technician`). La empresa verificadora ve
    // todo lo de su empresa (`role=verifier`) y, por si además figura como
    // técnico, también lo asignado a ella. Se juntan sin repetir.
    final roles = sesion.esTecnico ? const ['technician'] : const ['verifier', 'technician'];
    final porId = <String, Map<String, dynamic>>{};
    for (final rol in roles) {
      for (final v in await api.listarTodo('/verifications', params: {'role': rol})) {
        if (Verificacion.tieneId(v)) porId[v['id'] as String] = v;
      }
    }
    final lista = porId.values.toList();
    await _bd.guardarVerificaciones(lista);
    await _bd.conservarSoloVerificaciones(porId.keys);
    // El detalle (pesos, líneas, conteos) solo de las que están abiertas.
    for (final v in lista) {
      final id = v['id'] as String;
      final estado = jsonTexto(v['state']) ?? '';
      if (const {'received', 'assigned', 'in_field', 'done'}.contains(estado)) {
        final detalle = await api.get('/verifications/$id');
        if (!Verificacion.tieneId(detalle)) continue;
        await _bd.guardarVerificaciones([detalle]);
        if (await _bd.informeLocal(id) == null) {
          await _bd.guardarInformeLocal(id, InformeCampo.desdeServidor(detalle), modificado: false);
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
      rol: sesion.rolParaApi,
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

  /// Guarda el informe en el teléfono y, si `enviar`, lo pone en la cola.
  /// Un informe anterior del mismo lote que todavía no salió se reemplaza:
  /// vale el último guardado.
  Future<void> guardarInforme(Verificacion v, InformeCampo informe, {bool enviar = true}) async {
    await _bd.guardarInformeLocal(v.id, informe, modificado: true);
    if (enviar) {
      await _bd.quitarDeColaPorTipo(v.id, TipoOperacion.informe);
      await _encolar(TipoOperacion.informe, v.id, informe.aCuerpoApi());
      unawaited(sincronizar());
    }
    await recargarLocal();
  }

  /// Veredicto. Antes de encolarlo se comprueba con el informe local lo
  /// mismo que exige el servidor; devuelve lo que falta (vacío = encolado).
  /// El estado local NO se adelanta: la bandeja cambia cuando el servidor
  /// responde.
  Future<List<String>> darVeredicto(Verificacion v, String veredicto, String notas) async {
    final informe = await informeDe(v);
    final faltan = informe.faltantesParaVeredicto(larva: v.esLarva);
    if (faltan.isNotEmpty) return faltan;
    await _encolar(TipoOperacion.veredicto, v.id, cuerpoVeredicto(veredicto, notas));
    unawaited(sincronizar());
    return const [];
  }

  /// Llegada del camión. Sin despacho asociado no hay a qué registrarla.
  Future<bool> registrarLlegadaCamion(Verificacion v, DateTime cuando) async {
    final despacho = v.despachoId;
    if (despacho == null || despacho.isEmpty) return false;
    await _encolar(TipoOperacion.llegadaCamion, v.id, cuerpoLlegadaCamion(cuando), despachoId: despacho);
    unawaited(sincronizar());
    return true;
  }

  // ---------------- acciones de la verificadora ----------------

  Future<ResultadoEnvio> asignarTecnico(Verificacion v, String tecnicoId) =>
      ejecutar(TipoOperacion.asignarTecnico, v.id, cuerpoAsignarTecnico(tecnicoId));

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
