// Sesion con un servidor falso (MockClient de package:http): inicio de sesión
// correcto, 401, 403 y 429, caducidad de la clave, cambio de perfil y cierre.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:verificador_campo/datos/almacen_credenciales.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/modelos/perfil.dart';

import 'datos_api.dart';

/// Credenciales en memoria (en el teléfono van cifradas).
class AlmacenFalso extends AlmacenCredenciales {
  String? urlGuardada;
  String? claveGuardada;
  @override
  Future<String?> url() async => urlGuardada;
  @override
  Future<String?> clave() async => claveGuardada;
  @override
  Future<void> guardar({required String url, required String clave}) async {
    urlGuardada = AlmacenCredenciales.normalizar(url);
    claveGuardada = clave.trim();
  }

  @override
  Future<void> borrarClave() async => claveGuardada = null;
  @override
  Future<void> borrar() async {
    urlGuardada = null;
    claveGuardada = null;
  }
}

/// Base local en memoria: solo lo que usa Sesion (config y borrados).
class BaseFalsa extends BaseLocal {
  final config = <String, String>{};
  int vecesBorradoTodo = 0;
  int vecesBorradaCache = 0;
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
  Future<void> borrarCache() async => vecesBorradaCache++;
  @override
  Future<void> borrarTodo() async {
    vecesBorradoTodo++;
    config.clear();
  }
}

http.Response _json(int status, Object cuerpo) =>
    http.Response(jsonEncode(cuerpo), status, headers: {'content-type': 'application/json'});

/// Servidor falso: responde según la ruta y guarda cada petición recibida.
class ServidorFalso {
  final peticiones = <http.Request>[];
  Map<String, dynamic> Function() me = meUnPerfil;
  http.Response Function(http.Request)? login;
  http.Response Function(http.Request)? privado;

  late final MockClient cliente = MockClient((req) async {
    peticiones.add(req);
    final ruta = req.url.path;
    if (ruta.endsWith('/auth/login')) {
      return (login ?? (_) => _json(200, {'api_key': 'trz_secreta', 'expires_at': '2027-10-11T10:00:00Z', 'me': me()}))(req);
    }
    if (privado != null) return privado!(req);
    if (ruta.endsWith('/me')) return _json(200, me());
    if (ruta.endsWith('/auth/logout')) return _json(200, {'revoked': true});
    return _json(404, problema(404, 'not-found', 'No existe.'));
  });

  http.Request get ultima => peticiones.last;
  Iterable<http.Request> get privadas => peticiones.where((r) => !r.url.path.endsWith('/auth/login'));
}

void main() {
  late ServidorFalso servidor;
  late AlmacenFalso almacen;
  late BaseFalsa bd;
  late Sesion sesion;

  setUp(() {
    servidor = ServidorFalso();
    almacen = AlmacenFalso();
    bd = BaseFalsa();
    sesion = Sesion(credenciales: almacen, bd: bd, clienteHttp: servidor.cliente);
  });

  Future<void> entrar() => sesion.iniciarSesion(url: 'demo.camaronmarket.com/', correo: ' socio@finca.ec ', contrasena: 'clave 123');

  group('POST /auth/login', () {
    test('correcto: guarda la clave y me, y entra directo con el único perfil aprobado', () async {
      await entrar();
      expect(sesion.conectado, isTrue);
      expect(sesion.necesitaElegirPerfil, isFalse);
      expect(sesion.perfil, 'camaronera');
      expect(sesion.nombre, 'Camaronera Las Mareas');
      expect(sesion.url, 'https://demo.camaronmarket.com');
      expect(almacen.claveGuardada, 'trz_secreta');
      expect(almacen.urlGuardada, 'https://demo.camaronmarket.com');
      expect(bd.config['perfil'], 'camaronera');
      expect(jsonDecode(bd.config['yo']!)['name'], 'Camaronera Las Mareas');
      expect(sesion.caducaEn, DateTime.utc(2027, 10, 11, 10).toLocal());
      expect(bd.config['caduca_en'], '2027-10-11T10:00:00.000Z');
      expect(sesion.avisoCaducidadProxima, isNull);

      final login = servidor.peticiones.single;
      expect(login.method, 'POST');
      expect(login.url.toString(), 'https://demo.camaronmarket.com/api/v1/auth/login');
      expect(jsonDecode(login.body), {'login': 'socio@finca.ec', 'password': 'clave 123', 'device': 'Android'});
      expect(login.headers.containsKey('Authorization'), isFalse);
      expect(login.headers.containsKey('X-Shrimp-Role'), isFalse, reason: 'el login es público');
      expect(login.headers.containsKey('Idempotency-Key'), isFalse);
    });

    test('la contraseña se manda tal cual (puede llevar espacios)', () async {
      await sesion.iniciarSesion(url: 'demo.camaronmarket.com', correo: 'a@b.c', contrasena: ' con espacios ');
      expect(jsonDecode(servidor.ultima.body)['password'], ' con espacios ');
    });

    test('401 invalid-credentials: mensaje claro y nada guardado', () async {
      servidor.login = (_) => _json(401, problema(401, 'invalid-credentials', 'Correo o contraseña incorrectos.'));
      await expectLater(entrar(), throwsA(isA<ErrorApi>()
          .having((e) => e.status, 'status', 401)
          .having((e) => e.tipo, 'tipo', 'invalid-credentials')
          .having((e) => e.mensaje, 'mensaje', 'Correo o contraseña incorrectos.')
          .having((e) => e.esNoAutorizado, 'esNoAutorizado', isTrue)
          .having((e) => e.esDefinitivo, 'esDefinitivo', isFalse)));
      expect(sesion.conectado, isFalse);
      expect(almacen.claveGuardada, isNull);
      expect(sesion.avisoCaducidad, isNull, reason: 'un 401 del login no es una sesión caducada');
    });

    test('403 account-not-approved', () async {
      servidor.login = (_) => _json(403, problema(403, 'account-not-approved', 'Tu cuenta no tiene ningún perfil.'));
      await expectLater(entrar(), throwsA(isA<ErrorApi>()
          .having((e) => e.esProhibido, 'esProhibido', isTrue)
          .having((e) => e.esDefinitivo, 'esDefinitivo', isTrue)
          .having((e) => e.mensaje, 'mensaje', contains('todavía no está aprobada'))));
      expect(sesion.conectado, isFalse);
    });

    test('403 user-not-allowed y mfa-not-supported llevan al soporte / portal', () async {
      servidor.login = (_) => _json(403, problema(403, 'user-not-allowed', 'Este usuario no puede usar la API externa.'));
      await expectLater(entrar(), throwsA(isA<ErrorApi>().having((e) => e.mensaje, 'mensaje', contains('soporte'))));
      servidor.login = (_) => _json(403, problema(403, 'mfa-not-supported', 'Tu usuario tiene verificación en dos pasos.'));
      await expectLater(entrar(), throwsA(isA<ErrorApi>().having((e) => e.mensaje, 'mensaje', contains('dos pasos'))));
    });

    test('429 rate-limited', () async {
      servidor.login = (_) => http.Response(
          jsonEncode(problema(429, 'rate-limited', 'Demasiados intentos fallidos. Espera 15 minutos antes de volver a intentarlo.')),
          429,
          headers: {'content-type': 'application/json', 'retry-after': '900'});
      await expectLater(entrar(), throwsA(isA<ErrorApi>()
          .having((e) => e.status, 'status', 429)
          .having((e) => e.esDefinitivo, 'esDefinitivo', isFalse)
          .having((e) => e.mensaje, 'mensaje', contains('Demasiados intentos'))));
      expect(sesion.conectado, isFalse);
    });

    test('aviso cuando la clave caduca en menos de 15 días', () async {
      final pronto = DateTime.now().add(const Duration(days: 3)).toUtc().toIso8601String();
      servidor.login = (_) => _json(200, {'api_key': 'trz_secreta', 'expires_at': pronto, 'me': meUnPerfil()..['api_key'] = {'expires_at': pronto}});
      await entrar();
      expect(sesion.avisoCaducidadProxima, contains('caduca en'));
      expect(sesion.avisoCaducidadProxima, contains('vuelve a entrar'));
    });

    test('el tipo del error sale del slug problem-<slug>', () async {
      servidor.login = (_) => _json(401, problema(401, 'invalid-credentials', 'x'));
      await expectLater(entrar(), throwsA(isA<ErrorApi>().having((e) => e.tipo, 'tipo', 'invalid-credentials')));
      servidor.privado = (_) => _json(401, problema(401, 'revoked-api-key', 'x'));
      await expectLater(sesion.entrarConClave(url: 'demo.camaronmarket.com', clave: 'k'),
          throwsA(isA<ErrorApi>().having((e) => e.mensaje, 'mensaje', contains('cerrada desde el portal'))));
    });

    test('respuesta sin api_key', () async {
      servidor.login = (_) => _json(200, {'me': meUnPerfil()});
      await expectLater(entrar(), throwsA(isA<ErrorApi>().having((e) => e.status, 'status', 500)));
      expect(sesion.conectado, isFalse);
    });

    test('cuerpo de error que no es JSON', () async {
      servidor.login = (_) => http.Response('<html>502 Bad Gateway</html>', 502);
      await expectLater(entrar(), throwsA(isA<ErrorApi>().having((e) => e.status, 'status', 502)));
      expect(mensajeDeError(const ErrorApi(502, 'Error', '', '')), 'Error');
    });

    test('sin red', () async {
      servidor.login = (_) => throw http.ClientException('Failed host lookup');
      await expectLater(entrar(), throwsA(isA<SinConexion>()));
      expect(mensajeDeError(SinConexion()), contains('Sin internet'));
    });
  });

  group('cabeceras de las rutas privadas', () {
    test('Authorization y X-Shrimp-Role en cada petición privada', () async {
      await entrar();
      await sesion.api!.quienSoy();
      final req = servidor.ultima;
      expect(req.url.path, '/api/v1/me');
      expect(req.headers['Authorization'], 'Bearer trz_secreta');
      expect(req.headers['X-Shrimp-Role'], 'camaronera');
      expect(req.headers['Accept'], 'application/json');
    });

    test('POST privado lleva Idempotency-Key y el perfil pedido', () async {
      await entrar();
      servidor.privado = (req) => _json(200, {'ok': true});
      await sesion.api!.post('/verifications/x:start', {}, idempotencia: 'uuid-1');
      expect(servidor.ultima.headers['Idempotency-Key'], 'uuid-1');
      expect(servidor.ultima.headers['X-Shrimp-Role'], 'camaronera');
      await sesion.api!.post('/verifications/x:start', {}, idempotencia: 'uuid-2', rol: 'empacadora');
      expect(servidor.ultima.headers['X-Shrimp-Role'], 'empacadora', reason: 'la cola manda el perfil con el que se hizo');
    });

    test('el técnico manda el perfil activo de su cuenta', () async {
      servidor.me = meTecnico;
      await entrar();
      expect(sesion.esTecnico, isTrue);
      expect(sesion.perfil, perfilTecnico);
      expect(sesion.rolParaApi, 'verificador');
      await sesion.api!.quienSoy();
      expect(servidor.ultima.headers['X-Shrimp-Role'], 'verificador');
    });
  });

  group('perfiles', () {
    test('varios aprobados: hay que elegir; elegir limpia la caché y cambia la cabecera', () async {
      servidor.me = meCamaronera;
      await entrar();
      expect(sesion.necesitaElegirPerfil, isTrue);
      expect(sesion.yo!.aprobados.length, 2);
      await sesion.elegirPerfil('empacadora');
      expect(sesion.perfil, 'empacadora');
      expect(bd.config['perfil'], 'empacadora');
      expect(bd.vecesBorradaCache, 1);
      await sesion.api!.quienSoy();
      expect(servidor.ultima.headers['X-Shrimp-Role'], 'empacadora');
      await sesion.volverAElegirPerfil();
      expect(sesion.necesitaElegirPerfil, isTrue);
      expect(bd.config.containsKey('perfil'), isFalse);
    });

    test('al abrir la app con sesión guardada se respeta el perfil elegido', () async {
      almacen.urlGuardada = 'https://demo.camaronmarket.com';
      almacen.claveGuardada = 'trz_secreta';
      bd.config['yo'] = jsonEncode(meCamaronera());
      bd.config['perfil'] = 'empacadora';
      await sesion.iniciar();
      expect(sesion.cargando, isFalse);
      expect(sesion.conectado, isTrue);
      expect(sesion.perfil, 'empacadora');
      expect(sesion.rolParaApi, 'empacadora');
    });

    test('me guardado dañado no impide abrir la app', () async {
      almacen.urlGuardada = 'https://demo.camaronmarket.com';
      almacen.claveGuardada = 'trz_secreta';
      bd.config['yo'] = '{esto no es json';
      await sesion.iniciar();
      expect(sesion.cargando, isFalse);
      expect(sesion.conectado, isTrue);
      expect(sesion.perfil, 'camaronera', reason: 'se pidió /me de nuevo');
    });
  });

  group('caducidad (401) y cierre de sesión', () {
    test('401 en una petición privada: vuelve a la entrada SIN borrar la base local', () async {
      await entrar();
      bd.config['marca'] = 'cola del técnico';
      servidor.privado = (_) => _json(401, problema(401, 'expired-api-key', 'La clave caducó.'));
      await expectLater(sesion.api!.get('/verifications'), throwsA(isA<ErrorApi>()));
      expect(sesion.conectado, isFalse);
      expect(sesion.avisoCaducidad, 'La sesión caducó. Vuelve a entrar.');
      expect(almacen.claveGuardada, isNull, reason: 'la clave ya no sirve');
      expect(almacen.urlGuardada, 'https://demo.camaronmarket.com', reason: 'el servidor se conserva');
      expect(bd.vecesBorradoTodo, 0);
      expect(bd.config['marca'], 'cola del técnico');
      expect(sesion.yo, isNotNull, reason: 'se sigue sabiendo quién era para comparar al volver a entrar');
    });

    test('un 401 rezagado de un cliente anterior no tumba la sesión nueva', () async {
      await entrar();
      final viejo = sesion.api!;
      await sesion.cerrarSesion();
      await entrar();
      servidor.privado = (_) => _json(401, problema(401, 'revoked-api-key', 'Revocada.'));
      await expectLater(viejo.get('/me'), throwsA(anything));
      expect(sesion.conectado, isTrue);
      expect(sesion.avisoCaducidad, isNull);
    });

    test('tras caducar, la misma cuenta vuelve a entrar y conserva lo local', () async {
      await entrar();
      bd.config['marca'] = 'x';
      servidor.privado = (_) => _json(401, problema(401, 'expired-api-key', 'La clave caducó.'));
      await expectLater(sesion.api!.get('/me'), throwsA(anything));
      servidor.privado = null;
      await entrar();
      expect(sesion.conectado, isTrue);
      expect(sesion.avisoCaducidad, isNull);
      expect(bd.vecesBorradoTodo, 0);
      expect(bd.config['marca'], 'x');
    });

    test('si entra OTRA cuenta se borra lo de la anterior', () async {
      await entrar();
      servidor.privado = (_) => _json(401, problema(401, 'expired-api-key', 'La clave caducó.'));
      await expectLater(sesion.api!.get('/me'), throwsA(anything));
      servidor.privado = null;
      servidor.me = meTecnico;
      await entrar();
      expect(bd.vecesBorradoTodo, 1);
      expect(sesion.perfil, perfilTecnico);
    });

    test('cerrar sesión revoca la clave y borra todo', () async {
      await entrar();
      await sesion.cerrarSesion();
      final logout = servidor.ultima;
      expect(logout.url.path, '/api/v1/auth/logout');
      expect(logout.headers['Authorization'], 'Bearer trz_secreta');
      expect(logout.headers['Idempotency-Key'], isNotEmpty);
      expect(sesion.conectado, isFalse);
      expect(sesion.yo, isNull);
      expect(sesion.perfil, isNull);
      expect(almacen.claveGuardada, isNull);
      expect(almacen.urlGuardada, isNull);
      expect(bd.vecesBorradoTodo, 1);
    });

    test('cerrar sesión sin señal igual sale', () async {
      await entrar();
      servidor.privado = (_) => throw http.ClientException('sin red');
      await sesion.cerrarSesion();
      expect(sesion.conectado, isFalse);
      expect(bd.vecesBorradoTodo, 1);
    });
  });

  group('entrar con clave de API', () {
    test('GET /me comprueba la clave', () async {
      await sesion.entrarConClave(url: 'demo.camaronmarket.com', clave: ' trz_manual ');
      expect(sesion.conectado, isTrue);
      expect(almacen.claveGuardada, 'trz_manual');
      expect(servidor.ultima.headers['Authorization'], 'Bearer trz_manual');
    });

    test('clave inválida: no se guarda nada', () async {
      servidor.privado = (_) => _json(401, problema(401, 'invalid-api-key', 'No existe.'));
      await expectLater(sesion.entrarConClave(url: 'demo.camaronmarket.com', clave: 'mala'),
          throwsA(isA<ErrorApi>().having((e) => e.mensaje, 'mensaje', 'La clave de API no es válida.')));
      expect(sesion.conectado, isFalse);
      expect(almacen.claveGuardada, isNull);
    });
  });
}
