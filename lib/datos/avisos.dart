import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:ui' show IsolateNameServer;

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:uuid/uuid.dart';

import '../modelos/aviso.dart';
import 'api_cliente.dart';
import 'sesion.dart';

/// Lo poco que la tarea en segundo plano puede saber SIN la clave de API
/// (que está cifrada bajo la huella): cuántos avisos sin leer vio la app la
/// última vez y si falta recordarlos. No es un secreto: ni títulos, ni
/// clave, ni datos del socio.
class EstadoAvisos {
  /// Hay una sesión normal abierta (no supervisor): la tarea puede avisar.
  bool activo;
  int noLeidos;

  /// Ids ya vistos (en la app o por notificación): no se vuelven a notificar.
  List<String> conocidos;

  /// Llegaron avisos nuevos que todavía nadie recordó con una notificación.
  bool recordatorioPendiente;

  EstadoAvisos({this.activo = false, this.noLeidos = 0, List<String>? conocidos, this.recordatorioPendiente = false})
      : conocidos = conocidos ?? [];

  static const maximoConocidos = 300;

  Map<String, dynamic> aJson() =>
      {'activo': activo, 'no_leidos': noLeidos, 'conocidos': conocidos, 'recordatorio_pendiente': recordatorioPendiente};

  factory EstadoAvisos.desde(Map<String, dynamic> m) => EstadoAvisos(
        activo: m['activo'] == true,
        noLeidos: m['no_leidos'] is int ? m['no_leidos'] as int : 0,
        conocidos: (m['conocidos'] is List ? m['conocidos'] as List : const []).whereType<String>().toList(),
        recordatorioPendiente: m['recordatorio_pendiente'] == true,
      );

  /// Anota ids como conocidos; devuelve los que no lo eran.
  List<String> conocer(Iterable<String> ids) {
    final nuevos = ids.where((id) => !conocidos.contains(id)).toList();
    conocidos = [...conocidos, ...nuevos];
    if (conocidos.length > maximoConocidos) conocidos = conocidos.sublist(conocidos.length - maximoConocidos);
    return nuevos;
  }
}

/// Dónde se guarda [EstadoAvisos] (un JSON en la carpeta privada de la app,
/// legible también desde la tarea en segundo plano).
class AlmacenEstadoAvisos {
  Future<File> _archivo() async => File(p.join((await getApplicationSupportDirectory()).path, 'avisos_estado.json'));

  Future<EstadoAvisos> leer() async {
    try {
      final f = await _archivo();
      if (!await f.exists()) return EstadoAvisos();
      return EstadoAvisos.desde(jsonDecode(await f.readAsString()) as Map<String, dynamic>);
    } catch (_) {
      return EstadoAvisos();
    }
  }

  Future<void> guardar(EstadoAvisos e) async {
    try {
      await (await _archivo()).writeAsString(jsonEncode(e.aJson()), flush: true);
    } catch (err) {
      debugPrint('No se pudo guardar el estado de los avisos: $err');
    }
  }

  Future<void> borrar() async {
    try {
      final f = await _archivo();
      if (await f.exists()) await f.delete();
    } catch (_) {}
  }
}

/// Notificaciones del sistema y la tarea periódica (en pruebas, uno falso).
abstract class Notificador {
  /// Una notificación por aviso (título y cuerpo cortos del servidor).
  Future<void> mostrar(Aviso a);

  /// "Tienes avisos nuevos en CamaronMarket" (sin detalles).
  Future<void> mostrarGenerico();

  /// Programa la revisión periódica (~15 min) en segundo plano.
  Future<void> programarRevision();
  Future<void> cancelarRevision();
}

/// Nombre con el que la app viva atiende a la tarea en segundo plano.
const nombrePuertoAvisos = 'camaronmarket_avisos';

/// Bandeja de avisos (C3): contador para la campana, lista paginada,
/// marcar leído / todo leído y la revisión periódica que muestra
/// notificaciones locales cuando la app está en segundo plano.
///
/// Modo supervisor: se ve la bandeja del socio, pero NO se marca nada como
/// leído (la clave de act-as recibiría 403) y no hay notificaciones ni tarea
/// en segundo plano.
class ServicioAvisos extends ChangeNotifier {
  final Sesion sesion;
  final AlmacenEstadoAvisos _almacen;
  final Notificador? notificador;
  final _uuid = const Uuid();

  /// La instancia viva en este isolate (la tarea en segundo plano la usa si
  /// WorkManager la ejecuta en el motor principal).
  static ServicioAvisos? vivo;

  ServicioAvisos({required this.sesion, AlmacenEstadoAvisos? almacen, this.notificador, bool atenderFondo = false})
      : _almacen = almacen ?? AlmacenEstadoAvisos() {
    sesion.addListener(_alCambiarSesion);
    vivo = this;
    if (atenderFondo) _abrirPuerto();
    _alCambiarSesion();
  }

  int noLeidos = 0;

  /// El servidor tiene más sin leer de los que se contaron (se muestra "50+").
  bool masDeLosContados = false;

  /// La app está a la vista (las notificaciones del sistema sobran).
  bool enPrimerPlano = true;

  ApiCliente? _api;
  String? _modoActual;
  Timer? _temporizador;
  ReceivePort? _puerto;

  static const limiteContador = 50;
  static const cadaCuanto = Duration(minutes: 15);

  bool get soloLectura => sesion.soloLectura;

  void _abrirPuerto() {
    final puerto = ReceivePort();
    IsolateNameServer.removePortNameMapping(nombrePuertoAvisos);
    IsolateNameServer.registerPortWithName(puerto.sendPort, nombrePuertoAvisos);
    puerto.listen((_) => revisarDesdeFondo());
    _puerto = puerto;
  }

  /// normal (sesión propia abierta), supervisor, fuera (sin sesión) o nulo
  /// mientras no se sabe (cargando, o bloqueada al abrir la app: la tarea
  /// periódica sigue como estaba).
  String? get _modo {
    if (sesion.soloLectura && (sesion.conectado || sesion.bloqueada)) return 'supervisor';
    if (sesion.apiOperativa != null) return 'normal';
    if (!sesion.cargando && !sesion.conectado && !sesion.bloqueada) return 'fuera';
    return null;
  }

  void _alCambiarSesion() {
    final api = sesion.apiOperativa;
    if (!identical(api, _api)) {
      _api = api;
      noLeidos = 0;
      masDeLosContados = false;
      notifyListeners();
      if (api != null) unawaited(refrescar());
    }
    final modo = _modo;
    if (modo != null && modo != _modoActual) {
      _modoActual = modo;
      unawaited(_ajustarFondo(modo));
    }
  }

  /// Sesión normal → tarea periódica y temporizador; supervisor → nada;
  /// sin sesión → nada y se olvida el estado guardado.
  Future<void> _ajustarFondo(String modo) async {
    _temporizador?.cancel();
    _temporizador = null;
    try {
      if (modo == 'normal') {
        _temporizador = Timer.periodic(cadaCuanto, (_) => refrescar(notificarNuevos: !enPrimerPlano));
        final e = await _almacen.leer();
        e.activo = true;
        await _almacen.guardar(e);
        await notificador?.programarRevision();
      } else {
        await notificador?.cancelarRevision();
        if (modo == 'fuera') {
          await _almacen.borrar();
        } else {
          final e = await _almacen.leer();
          e.activo = false;
          await _almacen.guardar(e);
        }
      }
    } catch (e) {
      debugPrint('No se pudo ajustar la revisión de avisos: $e');
    }
  }

  static int? _total(Map meta) {
    for (final k in const ['unread_count', 'unread', 'total', 'count', 'total_count']) {
      if (meta[k] is int) return meta[k] as int;
    }
    return null;
  }

  /// Una página de avisos (todos o solo los no leídos).
  Future<PaginaAvisos> pagina({String? cursor, bool soloNoLeidos = false, int limite = 30}) async {
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    final r = await api.get('/me/notifications',
        params: {if (soloNoLeidos) 'unread': 'true', 'limit': '$limite', 'cursor': ?cursor});
    final datos = (r['data'] is List ? r['data'] as List : const []).whereType<Map<String, dynamic>>();
    final meta = r['meta'] is Map ? r['meta'] as Map : const {};
    final siguiente = meta['next_cursor'] is String && (meta['next_cursor'] as String).isNotEmpty ? meta['next_cursor'] as String : null;
    return PaginaAvisos(datos.map(Aviso.desde).whereType<Aviso>().toList(), siguiente, total: _total(meta));
  }

  /// Cuenta los no leídos (`unread=true`). Con [notificarNuevos] (app en
  /// segundo plano) muestra una notificación por cada aviso que no se
  /// conocía. Nunca lanza.
  Future<void> refrescar({bool notificarNuevos = false}) async {
    if (sesion.apiOperativa == null) return;
    try {
      final p = await pagina(soloNoLeidos: true, limite: limiteContador);
      final sinLeer = p.avisos.where((a) => !a.leido).toList();
      noLeidos = p.total ?? sinLeer.length;
      masDeLosContados = p.total == null && p.siguienteCursor != null;
      notifyListeners();
      if (soloLectura) return; // supervisor: ni estado, ni notificaciones
      final e = await _almacen.leer();
      e.activo = true;
      e.noLeidos = noLeidos;
      final nuevos = e.conocer(sinLeer.map((a) => a.id));
      if (nuevos.isNotEmpty) {
        final n = notificador;
        if (notificarNuevos && n != null) {
          for (final a in sinLeer.where((a) => nuevos.contains(a.id)).take(5)) {
            await n.mostrar(a);
          }
          e.recordatorioPendiente = false;
        } else {
          // Vistos en la campana; si la app se cierra sin abrirlos, la tarea
          // en segundo plano los recuerda una vez.
          e.recordatorioPendiente = true;
        }
      }
      if (noLeidos == 0) e.recordatorioPendiente = false;
      await _almacen.guardar(e);
    } catch (err) {
      debugPrint('No se pudieron revisar los avisos: $err');
    }
  }

  /// Llamada de la tarea en segundo plano (WorkManager) con la app viva.
  Future<void> revisarDesdeFondo() => refrescar(notificarNuevos: !enPrimerPlano);

  /// `POST /me/notifications/{id}:read` (no firmada, con Idempotency-Key).
  /// En modo supervisor no se marca (devuelve false).
  Future<bool> marcarLeido(Aviso a) async {
    if (a.leido || soloLectura) return false;
    final api = sesion.apiOperativa;
    if (api == null) return false;
    await api.post('/me/notifications/${a.id}:read', const {}, idempotencia: _uuid.v4());
    if (noLeidos > 0) noLeidos--;
    notifyListeners();
    await _guardarContador();
    return true;
  }

  /// `POST /me/notifications:read-all`. En modo supervisor lanza
  /// [ErrorSoloLectura] sin llamar al servidor.
  Future<void> marcarTodo() async {
    if (soloLectura) throw const ErrorSoloLectura();
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    final r = await api.post('/me/notifications:read-all', const {}, idempotencia: _uuid.v4());
    noLeidos = r['unread_count'] is int ? r['unread_count'] as int : 0;
    masDeLosContados = false;
    notifyListeners();
    await _guardarContador();
  }

  Future<void> _guardarContador() async {
    final e = await _almacen.leer();
    e.noLeidos = noLeidos;
    if (noLeidos == 0) e.recordatorioPendiente = false;
    await _almacen.guardar(e);
  }

  /// Texto del contador de la campana.
  String get textoContador => masDeLosContados ? '$limiteContador+' : '$noLeidos';

  @override
  void dispose() {
    sesion.removeListener(_alCambiarSesion);
    _temporizador?.cancel();
    if (_puerto != null) {
      IsolateNameServer.removePortNameMapping(nombrePuertoAvisos);
      _puerto!.close();
    }
    if (identical(vivo, this)) vivo = null;
    super.dispose();
  }
}

/// Lo que hace la tarea periódica (WorkManager, ~15 min).
///
/// La clave de API está cifrada con una clave del Keystore que exige la
/// huella o el PIN: en segundo plano NO se puede descifrar (y no se guarda
/// otra copia legible). Por eso:
///  1. Si la app sigue viva en este mismo isolate o en otro del proceso (la
///     clave está en memoria desde que se desbloqueó), se le pide a ella que
///     consulte `unread=true` y notifique cada aviso nuevo con su título.
///  2. Si la app está cerrada, no se consulta nada: con el contador guardado
///     sin clave ([EstadoAvisos]) se muestra, una sola vez, el aviso genérico
///     "Tienes avisos nuevos en CamaronMarket".
Future<bool> revisarAvisosEnSegundoPlano({required AlmacenEstadoAvisos almacen, required Notificador notificador}) async {
  final vivo = ServicioAvisos.vivo;
  if (vivo != null) {
    await vivo.revisarDesdeFondo();
    return true;
  }
  final puerto = IsolateNameServer.lookupPortByName(nombrePuertoAvisos);
  if (puerto != null) {
    puerto.send('revisar');
    return true;
  }
  final e = await almacen.leer();
  if (e.activo && e.noLeidos > 0 && e.recordatorioPendiente) {
    await notificador.mostrarGenerico();
    e.recordatorioPendiente = false;
    await almacen.guardar(e);
  }
  return true;
}
