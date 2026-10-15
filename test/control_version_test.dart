// C1 (CONTRATO-MEJORAS-APP.md): versión mínima obligatoria. Comparación
// semántica, consulta como mucho una vez por hora, 426 app-update-required en
// cualquier respuesta y la pantalla bloqueante.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/control_version.dart';
import 'package:verificador_campo/pantallas/inicio/pantalla_actualizar.dart';

http.Response _json(int status, Object cuerpo) =>
    http.Response(jsonEncode(cuerpo), status, headers: {'content-type': 'application/json'});

Map<String, dynamic> _cuerpo426() => {
      'type': 'https://demo.camaronmarket.com/api/v1/docs#problem-app-update-required',
      'title': 'Actualiza la app',
      'status': 426,
      'detail': 'Esta versión ya no se admite.',
      'min_version': '1.3.0',
      'download_url': 'https://camaronmarket.com/app.apk',
    };

void main() {
  group('compararVersiones', () {
    test('semántica x.y.z (no alfabética)', () {
      expect(compararVersiones('1.2.0', '1.2.0'), 0);
      expect(compararVersiones('1.2.0', '1.10.0'), lessThan(0));
      expect(compararVersiones('1.10.0', '1.9.9'), greaterThan(0));
      expect(compararVersiones('2.0.0', '1.99.99'), greaterThan(0));
      expect(compararVersiones('1.2.9', '1.3.0'), lessThan(0));
    });
    test('partes que faltan valen 0; el +build, el -sufijo y la v no cuentan', () {
      expect(compararVersiones('1.2', '1.2.0'), 0);
      expect(compararVersiones('1', '1.0.1'), lessThan(0));
      expect(compararVersiones('1.2.0+3', '1.2.0'), 0);
      expect(compararVersiones('v1.2.0', '1.2.0'), 0);
      expect(compararVersiones('1.2.0-beta', '1.2.0'), 0);
      expect(compararVersiones(' 1.2.1 ', '1.2.0'), greaterThan(0));
    });
    test('esVersionMenor', () {
      expect(esVersionMenor('1.2.0', '1.2.1'), isTrue);
      expect(esVersionMenor('1.2.0', '1.2.0'), isFalse);
      expect(esVersionMenor('1.3.0', '1.2.5'), isFalse);
    });
  });

  group('ControlVersion', () {
    late List<http.Request> peticiones;
    late Map<String, dynamic> respuesta;
    late DateTime ahora;
    late ControlVersion cv;

    setUp(() {
      peticiones = [];
      respuesta = {'min_version': null, 'latest_version': null, 'download_url': null, 'message': null};
      ahora = DateTime(2026, 10, 15, 9);
      final cliente = MockClient((req) async {
        peticiones.add(req);
        return _json(200, respuesta);
      });
      cv = ControlVersion(servidor: () => 'https://demo.camaronmarket.com', cliente: cliente, reloj: () => ahora, version: '1.2.0');
    });

    test('pregunta a la ruta pública con la plataforma y las cabeceras de la app', () async {
      await cv.comprobar();
      final r = peticiones.single;
      expect(r.method, 'GET');
      expect(r.url.path, '/api/v1/public/app-version');
      expect(r.url.queryParameters, {'platform': 'android'});
      expect(r.headers['X-App-Version'], isNotEmpty);
      expect(r.headers['X-App-Platform'], 'android');
      expect(r.headers.containsKey('Authorization'), isFalse);
    });

    test('sin mínimo (vacío por defecto) no bloquea', () async {
      await cv.comprobar();
      expect(cv.obligatoria, isFalse);
      expect(cv.hayNueva, isFalse);
    });

    test('versión menor que la mínima → obligatoria, con enlace y mensaje', () async {
      respuesta = {'min_version': '1.3.0', 'latest_version': '1.3.0', 'download_url': 'https://x/app.apk', 'message': 'Hay cambios.'};
      await cv.comprobar();
      expect(cv.obligatoria, isTrue);
      expect(cv.urlDescarga, 'https://x/app.apk');
      expect(cv.mensaje, 'Hay cambios.');
      expect(cv.hayNueva, isFalse, reason: 'con la pantalla bloqueante no se muestra el aviso discreto');
    });

    test('igual a la mínima → no bloquea; latest mayor → aviso descartable', () async {
      respuesta = {'min_version': '1.2.0', 'latest_version': '1.4.0', 'download_url': 'https://x/app.apk'};
      await cv.comprobar();
      expect(cv.obligatoria, isFalse);
      expect(cv.hayNueva, isTrue);
      expect(cv.versionNueva, '1.4.0');
      cv.descartar();
      expect(cv.hayNueva, isFalse);
      // Sale otra más nueva: se vuelve a avisar.
      respuesta = {'min_version': '1.2.0', 'latest_version': '1.5.0'};
      await cv.comprobar(forzar: true);
      expect(cv.hayNueva, isTrue);
    });

    test('como mucho una consulta por hora (salvo forzar)', () async {
      await cv.comprobar();
      ahora = ahora.add(const Duration(minutes: 30));
      await cv.comprobar();
      expect(peticiones, hasLength(1));
      ahora = ahora.add(const Duration(minutes: 31));
      await cv.comprobar();
      expect(peticiones, hasLength(2));
      await cv.comprobar(forzar: true);
      expect(peticiones, hasLength(3));
    });

    test('sin señal no cuenta como consulta: se reintenta al volver', () async {
      var enLinea = false;
      final c = ControlVersion(
          servidor: () => 'https://demo.camaronmarket.com',
          reloj: () => ahora,
          cliente: MockClient((req) async {
            if (!enLinea) throw http.ClientException('sin red');
            return _json(200, {'min_version': '9.0.0'});
          }));
      await c.comprobar();
      expect(c.obligatoria, isFalse);
      enLinea = true;
      await c.comprobar();
      expect(c.obligatoria, isTrue);
    });

    test('si el servidor quita el mínimo, se desbloquea', () async {
      cv.alRecibir426(ErrorApi(426, 'x', '', 'app-update-required', datos: _cuerpo426()));
      expect(cv.obligatoria, isTrue);
      await cv.comprobar();
      expect(cv.obligatoria, isFalse);
    });
  });

  group('426 app-update-required en cualquier respuesta', () {
    late List<ErrorApi> recibidos;
    setUp(() {
      recibidos = [];
      ApiCliente.alActualizacionObligatoria = recibidos.add;
    });
    tearDown(() => ApiCliente.alActualizacionObligatoria = null);

    test('petición privada: avisa y lanza ErrorApi con el cuerpo', () async {
      final api = ApiCliente(baseUrl: 'https://demo.camaronmarket.com', clave: 'trz_x', cliente: MockClient((_) async => _json(426, _cuerpo426())));
      await expectLater(api.get('/me'), throwsA(isA<ErrorApi>().having((e) => e.exigeActualizar, 'exige', isTrue)));
      expect(recibidos.single.datos['download_url'], 'https://camaronmarket.com/app.apk');
      expect(recibidos.single.mensaje, contains('Actualízala'));
    });

    test('login (sin cabecera de versión el servidor también responde 426)', () async {
      await expectLater(
          ApiCliente.iniciarSesion(
              baseUrl: 'https://demo.camaronmarket.com', correo: 'a@b.ec', contrasena: 'x', dispositivo: 'X',
              cliente: MockClient((_) async => _json(426, _cuerpo426()))),
          throwsA(isA<ErrorApi>()));
      expect(recibidos, hasLength(1));
    });

    test('un 426 sin type también cuenta', () async {
      final api = ApiCliente(baseUrl: 'https://demo.camaronmarket.com', clave: 'trz_x', cliente: MockClient((_) async => _json(426, {})));
      await expectLater(api.post('/x', {}, idempotencia: 'k'), throwsA(isA<ErrorApi>()));
      expect(recibidos, hasLength(1));
    });

    test('ControlVersion.alRecibir426 bloquea con el enlace del cuerpo', () {
      final cv = ControlVersion(servidor: () => '', version: '1.2.0');
      cv.alRecibir426(ErrorApi(426, 'x', 'Esta versión ya no se admite.', 'app-update-required', datos: _cuerpo426()));
      expect(cv.obligatoria, isTrue);
      expect(cv.minVersion, '1.3.0');
      expect(cv.urlDescarga, 'https://camaronmarket.com/app.apk');
    });

    test('otros errores no avisan', () async {
      final api = ApiCliente(
          baseUrl: 'https://demo.camaronmarket.com', clave: 'trz_x', cliente: MockClient((_) async => _json(403, {'type': '#problem-insufficient-scope'})));
      await expectLater(api.get('/me'), throwsA(isA<ErrorApi>()));
      expect(recibidos, isEmpty);
    });
  });

  testWidgets('pantalla "Actualiza la app": botón de descarga y versiones', (t) async {
    final cv = ControlVersion(servidor: () => '', version: '1.2.0');
    cv.alRecibir426(ErrorApi(426, 'x', '', 'app-update-required', datos: _cuerpo426()));
    await t.pumpWidget(ChangeNotifierProvider.value(value: cv, child: const MaterialApp(home: PantallaActualizar())));
    expect(find.text('Actualiza la app'), findsOneWidget);
    expect(find.text('Descargar la actualización'), findsOneWidget);
    expect(find.textContaining('mínima: 1.3.0'), findsOneWidget);
  });
}
