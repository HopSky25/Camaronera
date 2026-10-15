// C3 (CONTRATO-MEJORAS-APP.md): bandeja de avisos. Parseo, contador, marcar
// leído / todo leído (sin firma, con Idempotency-Key), modo supervisor (no se
// marca nada) y la tarea en segundo plano sin la clave de API.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/avisos.dart';
import 'package:verificador_campo/modelos/aviso.dart';
import 'package:verificador_campo/navegacion/destinos.dart';

import 'sesion_fija.dart';

http.Response _json(int status, Object cuerpo) =>
    http.Response(jsonEncode(cuerpo), status, headers: {'content-type': 'application/json'});

Map<String, dynamic> aviso(String id, {bool leido = false, String? link, Map<String, dynamic>? target}) => {
      'id': id,
      'kind': 'report_ready',
      'title': 'Informe listo para aceptar',
      'body': 'VER-00$id: revisa y acepta o rechaza.',
      'target': target ?? {'type': 'verification', 'id': '6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c'},
      'link': link,
      'created_at': '2026-10-15T14:00:00Z',
      'read': leido,
    };

/// Almacén del estado en memoria (en el teléfono es un archivo JSON).
class AlmacenEstadoFalso extends AlmacenEstadoAvisos {
  EstadoAvisos? estado;
  @override
  Future<EstadoAvisos> leer() async => EstadoAvisos.desde((estado ?? EstadoAvisos()).aJson());
  @override
  Future<void> guardar(EstadoAvisos e) async => estado = EstadoAvisos.desde(e.aJson());
  @override
  Future<void> borrar() async => estado = null;
}

class NotificadorFalso implements Notificador {
  final mostrados = <Aviso>[];
  int genericos = 0;
  int programadas = 0;
  int canceladas = 0;
  @override
  Future<void> mostrar(Aviso a) async => mostrados.add(a);
  @override
  Future<void> mostrarGenerico() async => genericos++;
  @override
  Future<void> programarRevision() async => programadas++;
  @override
  Future<void> cancelarRevision() async => canceladas++;
}

/// Servidor de avisos: lista, read y read-all; como shrimp_api, la clave de
/// act-as del supervisor NO puede marcar (403 insufficient-scope).
class ServidorAvisos {
  final peticiones = <http.Request>[];
  List<Map<String, dynamic>> avisos = [aviso('1'), aviso('2'), aviso('3', leido: true)];
  bool claveActAs = false;
  Map<String, dynamic>? meta;

  late final MockClient cliente = MockClient((req) async {
    peticiones.add(req);
    final ruta = req.url.path;
    if (ruta == '/api/v1/me/notifications' && req.method == 'GET') {
      final soloNoLeidos = req.url.queryParameters['unread'] == 'true';
      final lista = avisos.where((a) => !soloNoLeidos || a['read'] != true).toList();
      return _json(200, {'data': lista, 'meta': meta ?? {'next_cursor': null}});
    }
    if (req.method == 'POST' && ruta.startsWith('/api/v1/me/notifications')) {
      if (claveActAs) return _json(403, {'type': '#problem-insufficient-scope', 'title': 'Sin permiso', 'status': 403});
      if (ruta.endsWith(':read-all')) {
        for (final a in avisos) {
          a['read'] = true;
        }
      } else {
        final id = RegExp(r'/me/notifications/([^/:]+):read$').firstMatch(ruta)?.group(1);
        for (final a in avisos.where((a) => a['id'] == id)) {
          a['read'] = true;
        }
      }
      return _json(200, {'ok': true});
    }
    return _json(404, {'type': '#problem-not-found'});
  });

  Iterable<http.Request> get posts => peticiones.where((r) => r.method == 'POST');
}

Future<void> asentar() => Future<void>.delayed(const Duration(milliseconds: 20));

void main() {
  group('Aviso.desde', () {
    test('lee todos los campos del contrato', () {
      final a = Aviso.desde(aviso('7', link: '/marketplace/verifications/6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c/acceptance'))!;
      expect(a.id, '7');
      expect(a.tipo, 'report_ready');
      expect(a.titulo, 'Informe listo para aceptar');
      expect(a.cuerpo, contains('VER-007'));
      expect(a.destinoTipo, 'verification');
      expect(a.destinoId, '6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c');
      expect(a.enlace, startsWith('/marketplace/'));
      expect(a.creadoEn, DateTime.utc(2026, 10, 15, 14).toLocal());
      expect(a.leido, isFalse);
    });

    test('tolera lo que manda Odoo (false, sin target, id numérico) y descarta sin id', () {
      final a = Aviso.desde({'id': 12, 'title': false, 'body': false, 'target': false, 'link': false, 'read': true})!;
      expect(a.id, '12');
      expect(a.titulo, 'Aviso de CamaronMarket');
      expect(a.cuerpo, '');
      expect(a.destinoTipo, isNull);
      expect(a.enlace, isNull);
      expect(a.leido, isTrue);
      expect(Aviso.desde({'title': 'x'}), isNull);
    });

    test('destino: primero el link (C6), si no el target', () {
      expect(destinoDeAviso(Aviso.desde(aviso('1', link: '/marketplace/dispatch/6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c'))!),
          const Destino(TipoDestino.despacho, '6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c'));
      expect(destinoDeAviso(Aviso.desde(aviso('1'))!), const Destino(TipoDestino.verificacion, '6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c'));
      expect(destinoDeAviso(Aviso.desde(aviso('1', target: {'type': 'copack_request', 'id': 'x'}))!),
          const Destino(TipoDestino.solicitudesEmpaque));
      expect(destinoDeAviso(Aviso.desde(aviso('1', target: {'type': 'algo_nuevo', 'id': 'x'}))!), isNull);
    });

    test('carga de la notificación → destino; sin destino → bandeja de avisos', () {
      final a = Aviso.desde(aviso('1'))!;
      expect(destinoDeCarga(cargaDeAviso(a)), const Destino(TipoDestino.verificacion, '6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c'));
      expect(destinoDeCarga('avisos'), const Destino(TipoDestino.avisos));
      expect(destinoDeCarga(null), const Destino(TipoDestino.avisos));
      expect(destinoDeCarga(cargaDeAviso(Aviso.desde(aviso('2', target: {'type': '?', 'id': 'x'}))!)), const Destino(TipoDestino.avisos));
    });
  });

  group('ServicioAvisos', () {
    late ServidorAvisos servidor;
    late AlmacenEstadoFalso almacen;
    late NotificadorFalso notificador;
    late SesionFija sesion;
    late ServicioAvisos servicio;

    Future<void> crear({bool supervisor = false}) async {
      sesion = SesionFija(servidor.cliente, supervisor: supervisor);
      servidor.claveActAs = supervisor;
      servicio = ServicioAvisos(sesion: sesion, almacen: almacen, notificador: notificador);
      await asentar();
    }

    setUp(() {
      servidor = ServidorAvisos();
      almacen = AlmacenEstadoFalso();
      notificador = NotificadorFalso();
    });

    tearDown(() => servicio.dispose());

    test('al abrir la sesión cuenta los no leídos con unread=true y programa la tarea', () async {
      await crear();
      final r = servidor.peticiones.first;
      expect(r.url.path, '/api/v1/me/notifications');
      expect(r.url.queryParameters['unread'], 'true');
      expect(servicio.noLeidos, 2);
      expect(servicio.textoContador, '2');
      expect(notificador.programadas, 1);
      expect(almacen.estado?.noLeidos, 2);
      expect(almacen.estado?.activo, isTrue);
    });

    test('el contador usa meta.total si el servidor lo da; si hay más páginas, "50+"', () async {
      servidor.meta = {'total': 73, 'next_cursor': 'c2'};
      await crear();
      expect(servicio.noLeidos, 73);
      expect(servicio.textoContador, '73');
      servidor.meta = {'next_cursor': 'c2'};
      await servicio.refrescar();
      expect(servicio.textoContador, '50+');
    });

    test('marcar leído: POST /me/notifications/{id}:read, sin firma, con Idempotency-Key; baja el contador', () async {
      await crear();
      final p = await servicio.pagina();
      expect(p.avisos, hasLength(3));
      expect(await servicio.marcarLeido(p.avisos.first), isTrue);
      final post = servidor.posts.single;
      expect(post.url.path, '/api/v1/me/notifications/1:read');
      expect(post.headers['Idempotency-Key'], isNotEmpty);
      expect(post.headers.containsKey('X-Signature'), isFalse, reason: 'no son rutas firmadas');
      expect(servicio.noLeidos, 1);
      expect(almacen.estado?.noLeidos, 1);
      // Uno ya leído no se vuelve a marcar.
      expect(await servicio.marcarLeido(p.avisos.last), isFalse);
      expect(servidor.posts, hasLength(1));
    });

    test('marcar todo leído: POST /me/notifications:read-all y contador a cero', () async {
      await crear();
      await servicio.marcarTodo();
      expect(servidor.posts.single.url.path, '/api/v1/me/notifications:read-all');
      expect(servidor.posts.single.headers['Idempotency-Key'], isNotEmpty);
      expect(servicio.noLeidos, 0);
      expect(almacen.estado?.recordatorioPendiente, isFalse);
    });

    test('modo supervisor: se ve la bandeja del socio, pero NO se marca nada ni se notifica', () async {
      await crear(supervisor: true);
      expect(servicio.noLeidos, 2, reason: 'se lista con la clave de act-as');
      final p = await servicio.pagina();
      expect(await servicio.marcarLeido(p.avisos.first), isFalse);
      await expectLater(servicio.marcarTodo(), throwsA(isA<ErrorSoloLectura>()));
      expect(servidor.posts, isEmpty, reason: 'ni siquiera se intenta (sería 403)');
      expect(notificador.programadas, 0);
      expect(notificador.canceladas, 1, reason: 'sin tarea en segundo plano en modo supervisor');
      await servicio.refrescar(notificarNuevos: true);
      expect(notificador.mostrados, isEmpty);
      expect(almacen.estado?.activo ?? false, isFalse);
    });

    test('en segundo plano notifica cada aviso nuevo una sola vez', () async {
      await crear();
      // Lo que se vio en la campana no se notifica.
      await servicio.refrescar(notificarNuevos: true);
      expect(notificador.mostrados, isEmpty);
      servidor.avisos.add(aviso('9'));
      await servicio.refrescar(notificarNuevos: true);
      expect(notificador.mostrados.map((a) => a.id), ['9']);
      await servicio.refrescar(notificarNuevos: true);
      expect(notificador.mostrados, hasLength(1));
    });

    test('al cerrar la sesión se cancela la tarea y se olvida el estado', () async {
      await crear();
      sesion.cerrar();
      await asentar();
      expect(servicio.noLeidos, 0);
      expect(notificador.canceladas, 1);
      expect(almacen.estado, isNull);
    });
  });

  group('tarea en segundo plano (sin la clave de API)', () {
    tearDown(() => ServicioAvisos.vivo = null);

    test('con la app cerrada: aviso genérico una sola vez, con el contador guardado', () async {
      ServicioAvisos.vivo = null;
      final almacen = AlmacenEstadoFalso()
        ..estado = EstadoAvisos(activo: true, noLeidos: 3, conocidos: ['1', '2', '3'], recordatorioPendiente: true);
      final n = NotificadorFalso();
      expect(await revisarAvisosEnSegundoPlano(almacen: almacen, notificador: n), isTrue);
      expect(n.genericos, 1);
      expect(n.mostrados, isEmpty, reason: 'sin clave no hay títulos: solo el genérico');
      await revisarAvisosEnSegundoPlano(almacen: almacen, notificador: n);
      expect(n.genericos, 1, reason: 'no se repite');
    });

    test('sin sesión (o en modo supervisor) no avisa nada', () async {
      ServicioAvisos.vivo = null;
      final almacen = AlmacenEstadoFalso()..estado = EstadoAvisos(activo: false, noLeidos: 3, recordatorioPendiente: true);
      final n = NotificadorFalso();
      await revisarAvisosEnSegundoPlano(almacen: almacen, notificador: n);
      expect(n.genericos, 0);
    });

    test('con la app viva en el mismo isolate: consulta con la clave en memoria y notifica con título', () async {
      final servidor = ServidorAvisos();
      final n = NotificadorFalso();
      final servicio = ServicioAvisos(sesion: SesionFija(servidor.cliente), almacen: AlmacenEstadoFalso(), notificador: n);
      await asentar();
      servicio.enPrimerPlano = false;
      servidor.avisos.add(aviso('10'));
      await revisarAvisosEnSegundoPlano(almacen: AlmacenEstadoFalso(), notificador: NotificadorFalso());
      expect(n.mostrados.single.titulo, 'Informe listo para aceptar');
      expect(n.genericos, 0);
      servicio.dispose();
    });
  });

  test('EstadoAvisos no guarda nada secreto: solo contador, ids y banderas', () {
    final e = EstadoAvisos(activo: true, noLeidos: 2, conocidos: ['1', '2'], recordatorioPendiente: true);
    expect(e.aJson().keys.toSet(), {'activo', 'no_leidos', 'conocidos', 'recordatorio_pendiente'});
    expect(jsonEncode(e.aJson()).contains('trz_'), isFalse);
  });
}
