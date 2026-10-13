// Modo supervisor (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md, Anexo B):
// login de supervisor, directorio de socios, act-as FIRMADO, lecturas con la
// clave del socio SIN firma, solo lectura (nada se ofrece ni se encola),
// cambio de socio que revoca la clave anterior y caducidades.
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/repositorio.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/cuerpos.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/modelos/supervisor.dart';
import 'package:verificador_campo/modelos/verificacion.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_informes.dart';
import 'package:verificador_campo/pantallas/pantalla_verificacion.dart';
import 'package:verificador_campo/pantallas/supervisor/franja_supervisor.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';

import 'almacen_falso.dart';
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'servidor_firmas.dart' show respuestaJson;

const claveSupervisor = 'trz_sup';

Map<String, dynamic> loginSupervisor() => {
      'supervisor': true,
      'api_key': claveSupervisor,
      'expires_at': '2026-10-12T20:00:00Z',
      'me': {
        'id': 'u-sup-7',
        'name': 'Ana Supervisora',
        'email': 'ana@camaronmarket.com',
        'type': null,
        'supervisor': true,
        'active_role': null,
        'roles': [],
        'api_key': {'prefix': 'sup1', 'expires_at': '2026-10-12T20:00:00Z'},
      },
      'device_required': true,
    };

Map<String, dynamic> socioJson(String id, String nombre, {String type = 'camaronera', List<Object> roles = const ['camaronera']}) => {
      'id': id,
      'name': nombre,
      'type': type,
      'roles': roles,
      'city': 'Machala',
      'province': 'El Oro',
    };

/// Servidor falso con el Anexo B: clave de supervisor (solo directorio,
/// act-as y registro del dispositivo) y claves de act-as de solo lectura.
class ServidorSupervisor {
  final peticiones = <http.Request>[];
  DateTime ahora = DateTime.utc(2026, 10, 12, 12);

  String? dispositivoId;
  String? spki;

  /// Claves de act-as emitidas: clave → id del socio.
  final actAs = <String, String>{};
  final revocadas = <String>{};

  /// act-as aceptados (cadena canónica reconstruida con lo que llegó).
  final cadenasAceptadas = <String>[];

  /// La próxima respuesta de act-as llega como un reintento idempotente
  /// (`"api_key": null`).
  bool proximoSinClave = false;

  final socios = [
    socioJson('p-1', 'Camaronera Las Mareas', roles: [
      'camaronera',
      {'role': 'empacadora', 'label': 'Empacadora', 'state': 'approved'},
    ]),
    socioJson('p-2', 'Empacadora Pacífico', type: 'empacadora', roles: ['empacadora']),
    socioJson('p-3', 'Verificadora del Golfo', type: 'verificador', roles: ['verificador']),
  ];

  late final MockClient cliente = MockClient((req) async {
    peticiones.add(req);
    final ruta = req.url.path.replaceFirst('/api/v1', '');
    if (ruta == '/auth/login') return respuestaJson(200, loginSupervisor());
    final clave = (req.headers['Authorization'] ?? '').replaceFirst('Bearer ', '');
    if (revocadas.contains(clave) || (clave != claveSupervisor && !actAs.containsKey(clave))) {
      return respuestaJson(401, problema(401, 'expired-api-key', 'La clave caducó.'));
    }
    if (ruta == '/auth/logout') {
      revocadas.add(clave);
      if (clave == claveSupervisor) revocadas.addAll(actAs.keys);
      return respuestaJson(200, {'revoked': true});
    }
    if (clave == claveSupervisor) return _comoSupervisor(req, ruta);
    return _comoSocio(req, ruta, actAs[clave]!);
  });

  http.Response _comoSupervisor(http.Request req, String ruta) {
    if (ruta == '/me/devices' && req.method == 'POST') {
      if (dispositivoId != null) return respuestaJson(403, problema(403, 'signature-required', ''));
      spki = (jsonDecode(req.body) as Map)['public_key'] as String;
      dispositivoId = 'dev-sup';
      return respuestaJson(201, {'id': dispositivoId, 'name': 'Android', 'platform': 'android', 'alg': 'ES256', 'created_at': '2026-10-12T12:00:00Z', 'active': true});
    }
    if (ruta == '/supervisor/partners' && req.method == 'GET') {
      final q = (req.url.queryParameters['q'] ?? '').toLowerCase();
      final tipo = req.url.queryParameters['type'];
      final lista = socios.where((s) => (s['name'] as String).toLowerCase().contains(q) && (tipo == null || s['type'] == tipo)).toList();
      final limite = int.parse(req.url.queryParameters['limit'] ?? '50');
      final desde = int.tryParse(req.url.queryParameters['cursor'] ?? '') ?? 0;
      final pagina = lista.skip(desde).take(limite).toList();
      final siguiente = desde + limite < lista.length ? '${desde + limite}' : null;
      return respuestaJson(200, {'data': pagina, 'meta': {'next_cursor': siguiente}});
    }
    if (ruta == '/supervisor/act-as' && req.method == 'POST') {
      final error = _verificarFirma(req);
      if (error != null) return error;
      final socio = (jsonDecode(req.body) as Map)['partner'] as String;
      final me = {..._meDe(socio), 'read_only': true, 'viewed_by': {'name': 'Ana Supervisora (servidor)'}};
      if (proximoSinClave) {
        proximoSinClave = false;
        return respuestaJson(201, {'api_key': null, 'expires_at': '2026-10-12T20:00:00Z', 'me': me, 'read_only': true});
      }
      final clave = 'trz_as_${actAs.length + 1}';
      actAs[clave] = socio;
      return respuestaJson(201, {
        'api_key': clave,
        'expires_at': '2026-10-12T20:00:00Z',
        'partner': socios.firstWhere((s) => s['id'] == socio),
        'me': me,
        'read_only': true,
      });
    }
    // La clave de supervisor no tiene scopes de datos.
    return respuestaJson(403, problema(403, 'insufficient-scope', 'Sin scope.'));
  }

  Map<String, dynamic> _meDe(String socio) => switch (socio) {
        'p-1' => meCamaronera()..['id'] = 'p-1',
        'p-3' => meTecnico(),
        _ => meUnPerfil()
          ..['id'] = socio
          ..['name'] = 'Empacadora Pacífico'
          ..['roles'] = [
            {'role': 'empacadora', 'label': 'Empacadora', 'state': 'approved', 'active': true},
          ],
      };

  http.Response _comoSocio(http.Request req, String ruta, String socio) {
    if (req.method != 'GET') return respuestaJson(403, problema(403, 'insufficient-scope', 'Solo lectura.'));
    if (ruta == '/me') return respuestaJson(200, _meDe(socio));
    return respuestaJson(200, {'data': [], 'meta': {}});
  }

  http.Response? _verificarFirma(http.Request req) {
    final devId = req.headers['X-Device-Id'];
    final ts = req.headers['X-Signature-Timestamp'];
    final firma = req.headers['X-Signature'];
    final idem = req.headers['Idempotency-Key'];
    if (idem == null || idem.isEmpty) return respuestaJson(400, problema(400, 'idempotency-key-required', ''));
    if (devId == null || ts == null || firma == null) return respuestaJson(403, problema(403, 'signature-required', ''));
    if (devId != dispositivoId) return respuestaJson(403, problema(403, 'device-not-registered', ''));
    final cadena = [req.method, req.url.path, ts, idem, sha256.convert(req.bodyBytes).toString()].join('\n');
    if (!verificarEs256(spki!, utf8.encode(cadena), firma)) return respuestaJson(403, problema(403, 'invalid-signature', ''));
    cadenasAceptadas.add(cadena);
    return null;
  }

  Iterable<http.Request> a(String rutaApi) => peticiones.where((r) => r.url.path == '/api/v1$rutaApi');
}

/// Base local solo de configuración (para las pruebas de pantallas).
class BaseConfig extends BaseLocal {
  final config = <String, String>{};
  @override
  Future<String?> leerConfig(String clave) async => config[clave];
  @override
  Future<void> escribirConfig(String clave, String? valor) async {
    if (valor == null) {
      config.remove(clave);
    } else {
      config[clave] = valor;
    }
  }

  @override
  Future<List<Verificacion>> verificaciones() async => const [];
  @override
  Future<List<OperacionPendiente>> cola() async => const [];
}

/// Repositorio que devuelve un detalle fijo (sin red).
class RepoFijo extends Repositorio {
  final Map<String, dynamic> detalleFijo;
  RepoFijo(Sesion sesion, this.detalleFijo) : super(sesion: sesion, bd: BaseConfig());
  @override
  Future<ResultadoLista> detalle(String ruta, {Map<String, String>? params}) async => ResultadoLista([detalleFijo]);
}

void main() {
  group('modelos', () {
    test('respuesta de login de supervisor', () {
      expect(esLoginSupervisor(loginSupervisor()), isTrue);
      expect(esLoginSupervisor({'api_key': 'x', 'me': {'supervisor': true}}), isTrue, reason: 'basta con me.supervisor');
      expect(esLoginSupervisor({'api_key': 'x', 'me': meCamaronera()}), isFalse);
    });

    test('socio del directorio: perfiles como texto o como objeto, lugar y sin id', () {
      final s = SocioDirectorio.desde({
        ...socioJson('p-1', 'Las Mareas'),
        'roles': ['camaronera', {'role': 'empacadora', 'label': 'Empacadora'}, '', 3],
        'city': false,
      })!;
      expect(s.id, 'p-1');
      expect(s.nombre, 'Las Mareas');
      expect(s.tipo, 'camaronera');
      expect(s.tipoEtiqueta, 'Camaronera');
      expect(s.perfiles, ['camaronera', 'empacadora']);
      expect(s.lugar, 'El Oro', reason: 'Odoo manda false en los textos vacíos');
      expect(SocioDirectorio.desde({'name': 'Sin id'}), isNull);
      expect(SocioDirectorio.desde({'id': 'p', 'type': null})!.tipoEtiqueta, 'Sin perfil activo');
    });

    test('respuesta de act-as', () {
      final a = AccesoComoSocio.desde({'api_key': 'trz_as', 'expires_at': '2026-10-12T20:00:00Z', 'partner': {'id': 'p-1'}, 'me': meCamaronera(), 'read_only': true});
      expect(a.clave, 'trz_as');
      expect(a.caducaEn, DateTime.utc(2026, 10, 12, 20).toLocal());
      expect(a.socio?['id'], 'p-1');
      expect(a.me['name'], 'Camaronera Las Mareas');
      expect(a.soloLectura, isTrue);
      expect(() => AccesoComoSocio.desde({'me': meCamaronera()}), throwsFormatException);
      expect(() => AccesoComoSocio.desde({'api_key': 'x'}), throwsFormatException);
    });
  });

  group('flujo', () {
    late ServidorSupervisor servidor;
    late FirmadorFalso firmador;
    late AlmacenFalso almacen;
    late BaseLocal bd;
    late Sesion sesion;
    late Sincronizador sinc;
    final ahora = DateTime.utc(2026, 10, 12, 12);

    setUpAll(sqfliteFfiInit);

    setUp(() {
      servidor = ServidorSupervisor();
      firmador = FirmadorFalso();
      almacen = AlmacenFalso();
      bd = BaseLocal(
          abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
              options: OpenDatabaseOptions(
                  version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));
      sesion = Sesion(credenciales: almacen, bd: bd, clienteHttp: servidor.cliente, firmador: firmador, reloj: () => ahora);
      sinc = Sincronizador(sesion: sesion, bd: bd);
    });

    Future<void> asentar() async {
      for (var i = 0; i < 3; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 10));
        await sinc.sincronizar();
      }
    }

    tearDown(() async {
      await asentar();
      sinc.dispose();
      await (await bd.bd).close();
    });

    Future<void> entrarYProteger() async {
      await sesion.iniciarSesion(url: 'demo.camaronmarket.com', correo: 'ana@camaronmarket.com', contrasena: 'x');
      await sesion.proteger();
      await asentar();
    }

    SocioDirectorio socio(String id) => SocioDirectorio.desde(servidor.socios.firstWhere((s) => s['id'] == id))!;

    Future<void> actuarComo(String id) async {
      await sesion.actuarComo(socio(id));
      await asentar();
    }

    test('login de supervisor: Protege tu cuenta y luego el directorio (sin GET /me)', () async {
      await sesion.iniciarSesion(url: 'demo.camaronmarket.com', correo: 'ana@camaronmarket.com', contrasena: 'x');
      expect(sesion.esSupervisor, isTrue);
      expect(sesion.soloLectura, isTrue);
      expect(sesion.nombre, 'Ana Supervisora');
      expect(sesion.necesitaProteger, isTrue, reason: 'igual que cualquier clave de app');
      expect(sesion.necesitaDirectorio, isFalse);

      await sesion.proteger();
      final registro = servidor.a('/me/devices').single;
      expect(registro.headers['Authorization'], 'Bearer $claveSupervisor');
      expect(registro.headers.containsKey('X-Signature'), isFalse, reason: 'primer registro sin firma');
      expect(almacen.claveCifradaGuardada, isNotNull, reason: 'la clave de supervisor queda cifrada bajo la huella');
      expect(sesion.necesitaProteger, isFalse);
      expect(sesion.necesitaDirectorio, isTrue);
      expect(sesion.necesitaElegirPerfil, isFalse);
      expect(sesion.perfil, isNull);
      expect(sesion.apiOperativa, isNull, reason: 'sin socio elegido no hay datos que leer');
      expect(sesion.avisoCaducidadProxima, isNull, reason: 'la clave de 8 h no avisa por días');

      // Al abrir de nuevo la app: desbloqueo y directorio, sin pedir /me
      // (la clave de supervisor no tiene scopes de datos).
      final otra = Sesion(credenciales: almacen, bd: bd, clienteHttp: servidor.cliente, firmador: firmador, reloj: () => ahora);
      await otra.iniciar();
      expect(otra.bloqueada, isTrue);
      expect(otra.esSupervisor, isTrue);
      await otra.desbloquear();
      expect(otra.necesitaDirectorio, isTrue);
      expect(servidor.a('/me'), isEmpty);
      otra.dispose();
    });

    test('directorio: q, type, limit y cursor con la clave de supervisor, sin X-Shrimp-Role', () async {
      await entrarYProteger();
      final p1 = await sesion.buscarSocios(texto: '  ', limite: 2);
      expect(p1.socios.map((s) => s.nombre), ['Camaronera Las Mareas', 'Empacadora Pacífico']);
      expect(p1.socios.first.perfiles, ['camaronera', 'empacadora']);
      expect(p1.socios.first.lugar, 'Machala, El Oro');
      expect(p1.siguienteCursor, '2');
      final req = servidor.peticiones.last;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/supervisor/partners');
      expect(req.url.queryParameters, {'limit': '2'}, reason: 'sin q vacío ni type');
      expect(req.headers['Authorization'], 'Bearer $claveSupervisor');
      expect(req.headers.containsKey('X-Shrimp-Role'), isFalse);

      final p2 = await sesion.buscarSocios(limite: 2, cursor: p1.siguienteCursor);
      expect(p2.socios.single.nombre, 'Verificadora del Golfo');
      expect(p2.siguienteCursor, isNull);
      expect(servidor.peticiones.last.url.queryParameters['cursor'], '2');

      final filtrado = await sesion.buscarSocios(texto: 'pacíf', tipo: 'empacadora');
      expect(filtrado.socios.single.id, 'p-2');
      expect(servidor.peticiones.last.url.queryParameters, {'q': 'pacíf', 'type': 'empacadora', 'limit': '30'});
    });

    test('act-as va FIRMADO (huella, cadena canónica, Idempotency-Key); las lecturas con su clave NO', () async {
      await entrarYProteger();
      final antes = firmador.confirmaciones;
      await actuarComo('p-1');
      expect(firmador.confirmaciones, antes + 1, reason: 'una huella para ver la app del socio');

      final req = servidor.a('/supervisor/act-as').single;
      expect(req.method, 'POST');
      expect(req.body, '{"partner":"p-1"}');
      expect(req.headers['Authorization'], 'Bearer $claveSupervisor');
      expect(req.headers['X-Device-Id'], 'dev-sup');
      expect(req.headers['Idempotency-Key'], isNotEmpty);
      expect(req.headers.containsKey('X-Shrimp-Role'), isFalse);
      final ts = req.headers['X-Signature-Timestamp']!;
      expect(ts, '${segundosUnix(ahora)}');
      expect(servidor.cadenasAceptadas.single,
          'POST\n/api/v1/supervisor/act-as\n$ts\n${req.headers['Idempotency-Key']}\n${sha256Hex(utf8.encode('{"partner":"p-1"}'))}');
      expect(utf8.decode(firmador.firmados.last), servidor.cadenasAceptadas.single, reason: 'se firmó exactamente esa cadena');

      // Ya viendo al socio: su clave, solo en memoria.
      expect(sesion.comoSocio?.socio.id, 'p-1');
      expect(sesion.comoSocio?.vistoPor, 'Ana Supervisora (servidor)', reason: 'me.viewed_by.name para la franja');
      expect(sesion.comoSocio?.caducaEn, DateTime.utc(2026, 10, 12, 20).toLocal(), reason: 'expires_at del act-as');
      expect(sesion.apiOperativa?.clave, 'trz_as_1');
      expect(almacen.claveCifradaGuardada, isNot(contains('trz_as_1')));
      expect(await bd.leerConfig('yo'), isNot(contains('Las Mareas')), reason: 'el me del socio no se guarda');
      expect(sesion.nombre, 'Camaronera Las Mareas');
      expect(sesion.necesitaDirectorio, isFalse);
      expect(sesion.necesitaElegirPerfil, isTrue, reason: 'el socio tiene dos perfiles aprobados');

      await sesion.elegirPerfil('empacadora');
      expect(servidor.a('/me/active-role'), isEmpty, reason: 'nunca se cambia el perfil activo del servidor');
      await sesion.apiOperativa!.get('/verifications', params: {'role': 'buyer'});
      final lectura = servidor.peticiones.last;
      expect(lectura.headers['Authorization'], 'Bearer trz_as_1');
      expect(lectura.headers['X-Shrimp-Role'], 'empacadora');
      for (final h in ['X-Device-Id', 'X-Signature', 'X-Signature-Timestamp']) {
        expect(lectura.headers.containsKey(h), isFalse, reason: 'las lecturas con la clave del socio no se firman ($h)');
      }
      expect(firmador.confirmaciones, antes + 1, reason: 'leer no pide huella');
    });

    test('reintento idempotente con "api_key": null → act-as NUEVO (otra Idempotency-Key, otra firma)', () async {
      await entrarYProteger();
      servidor.proximoSinClave = true;
      final antes = firmador.confirmaciones;
      await actuarComo('p-2');
      final pedidos = servidor.a('/supervisor/act-as').toList();
      expect(pedidos, hasLength(2));
      expect(pedidos[0].headers['Idempotency-Key'], isNot(pedidos[1].headers['Idempotency-Key']));
      expect(pedidos[0].headers['X-Signature'], isNot(pedidos[1].headers['X-Signature']));
      expect(firmador.confirmaciones, antes + 2);
      expect(sesion.apiOperativa?.clave, 'trz_as_1');
    });

    test('errores nuevos del Anexo B con mensaje en español', () {
      for (final (status, tipo, texto) in [
        (403, 'supervisor-key', 'elegir un socio'),
        (403, 'supervisor-only', 'solo para supervisores'),
        (403, 'act-as-internal-user', 'interno'),
        (422, 'partner-not-approved', 'no está aprobado'),
        (422, 'partner-without-portal-user', 'usuario del portal'),
        (404, 'not-found', 'No se encontró'),
        (400, 'invalid-parameter', 'Filtro no válido'),
      ]) {
        expect(ErrorApi(status, 'Error', '', tipo).mensaje, contains(texto), reason: tipo);
      }
    });

    test('solo lectura: ninguna escritura se encola, se firma ni se envía', () async {
      await entrarYProteger();
      await actuarComo('p-2');
      expect(sesion.perfil, 'empacadora');
      final confirmaciones = firmador.confirmaciones;
      final enviadas = servidor.peticiones.length;

      final r = await sinc.ejecutar(TipoOperacion.aceptacionInforme, 'v-1', cuerpoAceptacion('accept', ''));
      expect(r.enviado, isFalse);
      expect(r.soloLectura, isTrue);
      expect(r.error, contains('Solo lectura en modo supervisor'));
      final v = Verificacion.desde(verificacionLista());
      await expectLater(sinc.iniciarCampo(v), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.darVeredicto(v, 'approve', ''), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.registrarLlegadaCamion(v, ahora), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.guardarInforme(v, InformeCampo.desdeServidor(v.crudo), enviar: false), throwsA(isA<ErrorSoloLectura>()));
      expect(await bd.informeLocal(v.id), isNull, reason: 'ni siquiera un borrador local');
      await expectLater(sinc.guardarFoto(v, '/tmp/x.jpg'), throwsA(isA<ErrorSoloLectura>()));

      expect(await bd.cola(), isEmpty);
      expect(sinc.pendientes, 0);
      expect(firmador.confirmaciones, confirmaciones, reason: 'no se pidió ninguna huella');
      expect(servidor.peticiones.skip(enviadas).where((p) => p.method != 'GET'), isEmpty);

      // Aunque quedara algo en la cola (de otra versión), en modo
      // supervisor no se sube.
      await bd.encolar(OperacionPendiente(
          tipo: TipoOperacion.aceptacionInforme,
          recursoId: 'v-9',
          cuerpoSerializado: '{}',
          claveIdempotencia: 'k',
          creadaEn: ahora));
      await sinc.sincronizar();
      expect(servidor.peticiones.skip(enviadas).where((p) => p.method != 'GET'), isEmpty);
    });

    test('403 insufficient-scope con la clave del socio: mensaje de solo lectura', () async {
      await entrarYProteger();
      await actuarComo('p-2');
      await expectLater(
          sesion.apiOperativa!.post('/verifications/v-1/acceptance', {'decision': 'accept'}, idempotencia: 'k'),
          throwsA(isA<ErrorApi>()
              .having((e) => e.tipo, 'tipo', 'insufficient-scope')
              .having((e) => e.mensaje, 'mensaje', contains('Solo lectura en modo supervisor'))));
      expect(sesion.comoSocio, isNotNull, reason: 'un 403 no saca del socio');
      expect(const ErrorApi(403, 'x', '', 'insufficient-scope').mensaje, 'Tu clave no tiene permiso para esta operación.');
    });

    test('cambiar de socio revoca la clave anterior y vuelve al directorio', () async {
      await entrarYProteger();
      await actuarComo('p-1');
      final vieja = sesion.apiOperativa!;
      await sesion.cambiarDeSocio();
      final logout = servidor.a('/auth/logout').single;
      expect(logout.headers['Authorization'], 'Bearer trz_as_1');
      expect(logout.headers['Idempotency-Key'], isNotEmpty);
      expect(servidor.revocadas, {'trz_as_1'});
      expect(sesion.comoSocio, isNull);
      expect(sesion.necesitaDirectorio, isTrue);
      expect(sesion.conectado, isTrue);
      expect(sesion.apiOperativa, isNull);

      await actuarComo('p-2');
      expect(sesion.apiOperativa?.clave, 'trz_as_2');
      expect(sesion.nombre, 'Empacadora Pacífico');
      // Una petición rezagada con la clave vieja no tumba al socio nuevo.
      await expectLater(vieja.get('/me'), throwsA(isA<ErrorApi>()));
      expect(sesion.comoSocio?.socio.id, 'p-2');
    });

    test('401 de la clave del socio → directorio; 401 de la de supervisor → entrada', () async {
      await entrarYProteger();
      await actuarComo('p-3');
      expect(sesion.esTecnico, isTrue, reason: 'el socio es un técnico de campo');
      servidor.revocadas.add('trz_as_1');
      await expectLater(sesion.apiOperativa!.get('/verifications'), throwsA(isA<ErrorApi>()));
      expect(sesion.comoSocio, isNull);
      expect(sesion.conectado, isTrue, reason: 'la sesión de supervisor sigue');
      expect(sesion.necesitaDirectorio, isTrue);
      expect(sesion.avisoDirectorio, contains('Juan Pérez'));
      expect(sesion.avisoCaducidad, isNull);

      servidor.revocadas.add(claveSupervisor);
      await expectLater(sesion.buscarSocios(), throwsA(isA<ErrorApi>()));
      expect(sesion.conectado, isFalse, reason: 'vuelta a la entrada');
      expect(sesion.avisoCaducidad, 'La sesión caducó. Vuelve a entrar.');
    });

    test('salir del modo supervisor revoca la clave del socio y la de supervisor', () async {
      await entrarYProteger();
      await actuarComo('p-2');
      await sesion.salirModoSupervisor();
      expect(servidor.a('/auth/logout').map((r) => r.headers['Authorization']), ['Bearer trz_as_1', 'Bearer $claveSupervisor']);
      expect(sesion.conectado, isFalse);
      expect(sesion.comoSocio, isNull);
      expect(sesion.esSupervisor, isFalse);
      expect(almacen.claveCifradaGuardada, isNull);
    });
  });

  group('pantallas en modo supervisor', () {
    Future<Sesion> sesionSupervisor() async {
      final bd = BaseConfig()..config['yo'] = jsonEncode(loginSupervisor()['me']);
      final almacen = AlmacenFalso()
        ..urlGuardada = 'https://demo.camaronmarket.com'
        ..claveCifradaGuardada = 'x';
      final s = Sesion(credenciales: almacen, bd: bd, firmador: FirmadorFalso());
      await s.iniciar();
      return s;
    }

    Sesion sesionNormal() => Sesion(credenciales: AlmacenFalso(), bd: BaseConfig(), firmador: FirmadorFalso());

    Future<void> informe(WidgetTester tester, Sesion sesion) => tester.pumpWidget(MultiProvider(
          providers: [
            ChangeNotifierProvider.value(value: sesion),
            Provider<Repositorio>.value(value: RepoFijo(sesion, verificacionDetalle())),
          ],
          child: const MaterialApp(home: PantallaInformePartes(id: 'v')),
        ));

    Future<void> verificacion(WidgetTester tester, Sesion sesion) async {
      final sinc = Sincronizador(sesion: sesion, bd: BaseConfig())..bandeja = [Verificacion.desde(verificacionLista())];
      await tester.pumpWidget(MultiProvider(
        providers: [ChangeNotifierProvider.value(value: sesion), ChangeNotifierProvider.value(value: sinc)],
        child: MaterialApp(home: PantallaVerificacion(id: verificacionLista()['id'] as String)),
      ));
    }

    testWidgets('informe de las partes: sin Aceptar / Rechazar, con el aviso', (tester) async {
      final sesion = (await tester.runAsync(sesionSupervisor))!;
      expect(sesion.soloLectura, isTrue);
      await informe(tester, sesion);
      await tester.pumpAndSettle();
      expect(find.text('Aceptar el informe'), findsNothing);
      expect(find.text('Rechazar el informe'), findsNothing);
      await tester.scrollUntilVisible(find.text(textoSoloLectura), 200);
      expect(find.text(textoSoloLectura), findsOneWidget);
    });

    testWidgets('informe de las partes fuera del modo supervisor: con Aceptar / Rechazar', (tester) async {
      await informe(tester, sesionNormal());
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Aceptar el informe'), 200);
      expect(find.text('Aceptar el informe'), findsOneWidget);
      expect(find.text(textoSoloLectura), findsNothing);
    });

    testWidgets('verificación: ni iniciar campo, ni informe, ni fotos, ni veredicto', (tester) async {
      final sesion = (await tester.runAsync(sesionSupervisor))!;
      await verificacion(tester, sesion);
      await tester.pumpAndSettle();
      for (final t in ['Iniciar trabajo de campo', 'Registrar llegada del camión', 'Informe de campo', 'Fotos de campo', 'Dar veredicto']) {
        expect(find.text(t), findsNothing, reason: t);
      }
      await tester.scrollUntilVisible(find.text(textoSoloLectura), 200);
      expect(find.text(textoSoloLectura), findsOneWidget);

      await verificacion(tester, sesionNormal());
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Iniciar trabajo de campo'), 200);
      expect(find.text('Iniciar trabajo de campo'), findsOneWidget);
    });

    testWidgets('franja fija: "Viendo como <socio> · solo lectura" y sus dos botones', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: ChangeNotifierProvider.value(
          value: sesionNormal(),
          child: const Scaffold(body: FranjaSupervisor(socio: 'Camaronera Las Mareas', supervisor: 'Ana Supervisora')),
        ),
      ));
      expect(find.textContaining('Viendo como Camaronera Las Mareas · solo lectura'), findsOneWidget);
      expect(find.textContaining('Supervisor: Ana Supervisora'), findsOneWidget);
      expect(find.text('Cambiar de socio'), findsOneWidget);
      expect(find.text('Salir del modo supervisor'), findsOneWidget);
    });
  });
}
