import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';

import '../modelos/cuerpos.dart';
import '../modelos/foto_campo.dart';
import '../modelos/operacion_pendiente.dart';
import '../modelos/perfil.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firmador.dart';
import 'api_cliente.dart';
import 'base_local.dart';
import 'sesion.dart';

/// Qué pasó con una operación que se mandó a la cola.
class ResultadoEnvio {
  /// Llegó al servidor y se aplicó.
  final bool enviado;

  /// El servidor la rechazó (queda en Pendientes con el error a la vista).
  final String? error;

  /// La persona no confirmó con la huella / PIN: no se guardó nada.
  final bool sinConfirmar;

  /// Modo supervisor: no se guardó ni se envió nada (solo lectura).
  final bool soloLectura;
  const ResultadoEnvio({required this.enviado, this.error, this.sinConfirmar = false, this.soloLectura = false});

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
///
/// Firma: cada operación sensible se firma con la huella / PIN en el momento
/// de hacerla ([_encolar]) y se guarda con su cuerpo YA SERIALIZADO; al
/// enviarla se mandan esos mismos bytes con las cabeceras de la firma. Una
/// operación sensible sin firma válida NO se envía: queda en Pendientes con
/// el botón "Confirmar con huella" ([volverAFirmar]).
///
/// Modo supervisor (solo lectura): no se encola, no se firma y no se sube
/// nada; solo se baja la bandeja del socio que se está viendo.
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
    final nuevo = sesion.apiOperativa;
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
    _api = sesion.apiOperativa;
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
      // En modo supervisor no se sube nada (ni lo que hubiera quedado).
      if (!sesion.soloLectura) await _subirCola(api);
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
    } on ErrorSeguridad catch (e) {
      ultimoMensaje = e.mensaje;
    } catch (e) {
      debugPrint('Fallo al sincronizar: $e');
      ultimoMensaje = 'No se pudo sincronizar. Inténtalo otra vez.';
    } finally {
      sincronizando = false;
      await recargarLocal();
    }
  }

  Future<void> _subirCola(ApiCliente api) async {
    // Registro del teléfono que quedó pendiente por falta de señal.
    await sesion.reintentarRegistro();
    // Recursos con una operación rechazada (o sin firma) en esta pasada: las
    // que vienen después sobre el mismo recurso esperan.
    final bloqueados = <String>{};
    for (final op in await _bd.cola()) {
      if (bloqueados.contains(op.recursoId)) continue;
      // Una foto que el servidor ya rechazó (p. ej. 409 invalid-state: la
      // verificación se cerró) no se vuelve a subir en cada pasada: queda en
      // Pendientes con el motivo hasta que se descarte.
      if (op.tipo == TipoOperacion.fotoCampo && op.ultimoErrorTipo == fotoRechazada) continue;
      if (op.tipo == TipoOperacion.llegadaCamion && (op.despachoId ?? '').isEmpty) {
        await _bd.anotarError(op.idLocal!, op.intentos + 1, 'Esta verificación no tiene despacho asociado.');
        continue;
      }
      Map<String, String>? cabecerasFirma;
      final estado = sesion.estadoFirma(op);
      switch (estado) {
        case EstadoFirma.noHaceFalta:
          break;
        case EstadoFirma.lista:
          cabecerasFirma = op.firma!.cabeceras(sesion.dispositivo!.id!);
        case EstadoFirma.esperaRegistro:
          // Firmada, pero el teléfono aún no está registrado (sin señal).
          bloqueados.add(op.recursoId);
          continue;
        case EstadoFirma.falta || EstadoFirma.otraClave || EstadoFirma.caducada || EstadoFirma.rechazada:
          // Sin firma válida no se envía: se marca para confirmarla.
          if (estado != EstadoFirma.rechazada) {
            await _bd.anotarError(op.idLocal!, op.intentos, mensajeFirmaPendiente(estado), tipo: 'firma-pendiente');
          }
          bloqueados.add(op.recursoId);
          continue;
      }
      try {
        final resp = await api.enviarSerializado(op.metodo, op.ruta, op.cuerpoSerializado,
            idempotencia: op.claveIdempotencia, rol: op.rol, firma: cabecerasFirma);
        await _bd.quitarDeCola(op.idLocal!);
        if (op.tipo == TipoOperacion.fotoCampo) await _bd.marcarFotoSubida(op.idLocal!);
        // La respuesta trae la verificación al día: se guarda en la bandeja.
        if (op.respondeVerificacion && _usaBandeja && Verificacion.tieneId(resp)) {
          await _bd.guardarVerificaciones([resp]);
          if (op.tipo == TipoOperacion.informe) {
            await _bd.guardarInformeLocal(op.recursoId, InformeCampo.desdeServidor(resp), modificado: false);
          }
        }
      } on ErrorApi catch (e) {
        // Teléfono revocado o sin registrar: la sesión ya reaccionó
        // (vuelta a la entrada / a "Protege tu cuenta"); la operación sigue
        // intacta en la cola.
        if (e.dispositivoRevocado || e.dispositivoNoRegistrado) rethrow;
        final definitivoFoto = op.tipo == TipoOperacion.fotoCampo && e.esDefinitivo && !e.pideFirmarDeNuevo;
        await _bd.anotarError(op.idLocal!, op.intentos + 1, mensajeDeRechazo(op, e), tipo: definitivoFoto ? fotoRechazada : e.tipo);
        if (e.esDefinitivo) {
          // El servidor rechazó el dato: se deja en la cola con el error a la
          // vista para que el usuario lo corrija o lo descarte. Lo que dependa
          // de este recurso espera; lo demás sigue.
          bloqueados.add(op.recursoId);
          if (op.tipo == TipoOperacion.iniciarCampo) {
            // El estado local se había adelantado a "en campo": se revierte.
            await _bd.cambiarEstadoLocal(op.recursoId, 'assigned');
          }
          ultimoMensaje = '${op.descripcion}: ${mensajeDeRechazo(op, e)}';
          continue;
        }
        rethrow; // 401/429/5xx: se para aquí y se reintenta en la próxima pasada
      }
    }
  }

  /// Tipo de error con el que se anota una foto que el servidor rechazó del
  /// todo (no se vuelve a enviar sola).
  static const fotoRechazada = 'foto-rechazada';

  /// Motivo de un rechazo del servidor, dicho para la operación concreta.
  static String mensajeDeRechazo(OperacionPendiente op, ErrorApi e) {
    if (op.tipo == TipoOperacion.fotoCampo) {
      if (e.tipo == 'invalid-state' || e.status == 409) {
        return 'La verificación ya no admite fotos (solo en campo o con el informe sin cerrar). '
            'Descarta esta foto; sigue guardada en el teléfono.';
      }
      if (e.status == 413) return 'La foto pesa demasiado para el servidor (máximo 5 MB). Descártala y toma otra.';
    }
    return e.mensaje;
  }

  /// Texto para Pendientes de una operación que hay que (volver a) confirmar.
  static String mensajeFirmaPendiente(EstadoFirma estado) => switch (estado) {
        EstadoFirma.falta => 'Falta confirmarla con tu huella o PIN (se hizo con una versión anterior de la app).',
        EstadoFirma.otraClave => 'Se confirmó antes de registrar de nuevo este teléfono: vuelve a confirmarla.',
        EstadoFirma.caducada => 'La confirmación tiene más de 7 días: vuelve a confirmarla.',
        _ => 'Vuelve a confirmarla con tu huella o PIN.',
      };

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

  /// Serializa el cuerpo UNA vez, lo firma (huella / PIN) si la operación es
  /// sensible y lo guarda en la cola. Lanza [ErrorSeguridad] si la persona no
  /// confirma: en ese caso no se guarda nada en la cola.
  Future<int> _encolar(TipoOperacion tipo, String recursoId, Map<String, dynamic> cuerpo,
      {String? despachoId, bool reemplazarTipo = false}) async {
    _exigirEscritura();
    final sinFirma = OperacionPendiente(
      tipo: tipo,
      recursoId: recursoId,
      despachoId: despachoId,
      cuerpoSerializado: jsonEncode(cuerpo),
      claveIdempotencia: _uuid.v4(),
      creadaEn: DateTime.now(),
      rol: sesion.rolParaApi,
    );
    final firma = await sesion.firmarOperacion(sinFirma);
    final idLocal = await _bd.encolar(sinFirma.conFirma(firma));
    // Lo anterior del mismo tipo se quita solo cuando lo nuevo ya está
    // guardado (si la persona no confirma, lo anterior sigue en la cola).
    if (reemplazarTipo) await _bd.quitarDeColaPorTipo(recursoId, tipo, excepto: idLocal);
    await recargarLocal();
    return idLocal;
  }

  /// Vuelve a firmar una operación de la cola (sin firma, caducada o
  /// rechazada por la firma): mismo cuerpo, misma Idempotency-Key, hora
  /// nueva. Lanza [ErrorSeguridad] si no se confirma.
  Future<void> volverAFirmar(OperacionPendiente op, {bool conPin = false}) async {
    _exigirEscritura();
    final firma = await sesion.firmarOperacion(op, conPin: conPin);
    if (firma != null) await _bd.guardarFirma(op.idLocal!, firma);
    await recargarLocal();
    unawaited(sincronizar());
  }

  /// Operaciones de la cola que esperan una confirmación con huella / PIN.
  List<OperacionPendiente> get porConfirmar => cola.where(necesitaConfirmar).toList();

  bool necesitaConfirmar(OperacionPendiente op) => switch (sesion.estadoFirma(op)) {
        EstadoFirma.falta || EstadoFirma.otraClave || EstadoFirma.caducada || EstadoFirma.rechazada => true,
        _ => false,
      };

  /// Encola y, si hay señal, envía de inmediato. Devuelve qué pasó para que
  /// la pantalla lo diga con claridad.
  Future<ResultadoEnvio> ejecutar(TipoOperacion tipo, String recursoId, Map<String, dynamic> cuerpo,
      {String? despachoId}) async {
    final int idLocal;
    try {
      idLocal = await _encolar(tipo, recursoId, cuerpo, despachoId: despachoId);
    } on ErrorSeguridad catch (e) {
      return ResultadoEnvio(enviado: false, error: e.mensaje, sinConfirmar: true);
    } on ErrorSoloLectura catch (e) {
      return ResultadoEnvio(enviado: false, error: e.mensaje, soloLectura: true);
    }
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

  /// Lanza [ErrorSeguridad] si no se confirma (el estado no cambia).
  Future<void> iniciarCampo(Verificacion v) async {
    await _encolar(TipoOperacion.iniciarCampo, v.id, {});
    await _bd.cambiarEstadoLocal(v.id, 'in_field');
    await recargarLocal();
    unawaited(sincronizar());
  }

  /// Guarda el informe en el teléfono y, si `enviar`, lo pone en la cola.
  /// Un informe anterior del mismo lote que todavía no salió se reemplaza:
  /// vale el último guardado. Si no se confirma con la huella / PIN lanza
  /// [ErrorSeguridad]; el informe queda guardado en el teléfono.
  Future<void> guardarInforme(Verificacion v, InformeCampo informe, {bool enviar = true}) async {
    _exigirEscritura();
    await _bd.guardarInformeLocal(v.id, informe, modificado: true);
    if (enviar) {
      await _encolar(TipoOperacion.informe, v.id, informe.aCuerpoApi(), reemplazarTipo: true);
      unawaited(sincronizar());
    }
    await recargarLocal();
  }

  /// Veredicto. Antes de encolarlo se comprueba con el informe local lo
  /// mismo que exige el servidor; devuelve lo que falta (vacío = encolado).
  /// El estado local NO se adelanta: la bandeja cambia cuando el servidor
  /// responde.
  Future<List<String>> darVeredicto(Verificacion v, String veredicto, String notas) async {
    _exigirEscritura();
    final informe = await informeDe(v);
    final faltan = informe.faltantesParaVeredicto(larva: v.esLarva);
    if (faltan.isNotEmpty) return faltan;
    await _encolar(TipoOperacion.veredicto, v.id, cuerpoVeredicto(veredicto, notas));
    unawaited(sincronizar());
    return const [];
  }

  /// Llegada del camión. Sin despacho asociado no hay a qué registrarla.
  Future<bool> registrarLlegadaCamion(Verificacion v, DateTime cuando) async {
    _exigirEscritura();
    final despacho = v.despachoId;
    if (despacho == null || despacho.isEmpty) return false;
    await _encolar(TipoOperacion.llegadaCamion, v.id, cuerpoLlegadaCamion(cuando), despachoId: despacho);
    unawaited(sincronizar());
    return true;
  }

  // ---------------- compra directa (C5) ----------------

  /// Compra directa de un lote sin verificación (larva, nauplio): arma el
  /// cuerpo de `POST /purchases` y lo firma con la huella / PIN. NO va a la
  /// cola: una compra sin señal no se deja para después (el stock cambia).
  /// La operación firmada se guarda en la pantalla para reintentar con la
  /// misma Idempotency-Key si se corta la señal (sin comprar dos veces).
  Future<OperacionPendiente> firmarCompraDirecta(
      {required String productoId, required double cantidad, String? notas, bool conPin = false}) async {
    _exigirEscritura();
    final op = OperacionPendiente(
      tipo: TipoOperacion.compraDirecta,
      recursoId: productoId,
      cuerpoSerializado: jsonEncode(cuerpoCompraDirecta(productoId: productoId, cantidad: cantidad, notas: notas)),
      claveIdempotencia: _uuid.v4(),
      creadaEn: DateTime.now(),
      rol: sesion.rolParaApi,
    );
    return op.conFirma(await sesion.firmarOperacion(op, conPin: conPin));
  }

  /// Envía la compra firmada. Devuelve la transacción (201). Lanza
  /// [SinConexion] (reintentar con la MISMA operación) o [ErrorApi].
  Future<Map<String, dynamic>> enviarCompraDirecta(OperacionPendiente op) async {
    _exigirEscritura();
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    await sesion.reintentarRegistro();
    final d = sesion.dispositivo;
    Map<String, String>? cabeceras;
    final firma = op.firma;
    if (firma != null && d != null) {
      if (!d.listo) throw const ErrorApi(403, 'Teléfono sin registrar', '', 'device-not-registered');
      cabeceras = firma.cabeceras(d.id!);
    }
    return api.enviarSerializado(op.metodo, op.ruta, op.cuerpoSerializado,
        idempotencia: op.claveIdempotencia, rol: op.rol, firma: cabeceras);
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

  /// Guarda en el teléfono una foto tomada en campo (sin subirla todavía).
  Future<int> guardarFoto(Verificacion v, String ruta, {DateTime? tomadaEn, double? latitud, double? longitud}) async {
    _exigirEscritura();
    return _bd.guardarFoto(v.id, ruta, tomadaEn: tomadaEn, latitud: latitud, longitud: longitud);
  }

  /// Sube una foto guardada (C4): la comprime (lado mayor ≤ 1600 px, JPEG
  /// 75) fuera del hilo de la pantalla, la firma con la huella / PIN como el
  /// resto de operaciones y la deja en la cola (`POST
  /// /verifications/{id}/photos`). Lanza [ErrorSeguridad] si no se confirma
  /// (la foto sigue guardada, "sin confirmar").
  Future<void> subirFoto(FotoCampo f, {String? nota, Future<Uint8List> Function(String ruta)? leer}) async {
    _exigirEscritura();
    final original = await (leer ?? _leerArchivo)(f.ruta);
    final cuerpo = await Isolate.run(() => prepararFoto(
          nombreArchivo: f.nombreArchivo,
          original: original,
          tomadaEn: f.tomadaEn,
          latitud: f.latitud,
          longitud: f.longitud,
          nota: nota,
        ));
    final idLocal = await _encolar(TipoOperacion.fotoCampo, f.verificacionId, cuerpo);
    await _bd.vincularFoto(f.id, idLocal);
    await recargarLocal();
    unawaited(sincronizar());
  }

  static Future<Uint8List> _leerArchivo(String ruta) => File(ruta).readAsBytes();

  /// Las fotos de una verificación con su estado (sin confirmar, pendiente,
  /// subida o con error) según la cola.
  Future<List<FotoCampo>> fotosDe(String verificacionId) async {
    final fotos = await _bd.fotos(verificacionId);
    final porId = {for (final op in await _bd.cola()) op.idLocal: op};
    return [
      for (final f in fotos)
        if (f.subida)
          f.conEstado(EstadoFoto.subida)
        else if (f.operacionId == null || !porId.containsKey(f.operacionId))
          // Sin firmar, o se descartó en Pendientes: hay que volver a confirmarla.
          f.conEstado(EstadoFoto.sinConfirmar)
        else if (porId[f.operacionId]!.conError)
          f.conEstado(EstadoFoto.error, porId[f.operacionId]!.ultimoError)
        else
          f.conEstado(EstadoFoto.pendiente),
    ];
  }

  /// En modo supervisor (solo lectura) ninguna acción llega a la cola.
  void _exigirEscritura() {
    if (sesion.soloLectura) throw const ErrorSoloLectura();
  }

  @override
  void dispose() {
    sesion.removeListener(_alCambiarSesion);
    _escuchaRed?.cancel();
    super.dispose();
  }
}
