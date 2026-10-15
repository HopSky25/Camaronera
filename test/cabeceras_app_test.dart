// C0 (CONTRATO-MEJORAS-APP.md): X-App-Version y X-App-Platform en TODAS las
// peticiones, también el login y las rutas públicas.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/version_app.dart';

void main() {
  test('la versión de la app es la de pubspec.yaml (sin el +build)', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final linea = RegExp(r'^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\+([0-9]+)', multiLine: true).firstMatch(pubspec);
    expect(linea, isNotNull);
    expect(linea!.group(1), versionApp);
    expect(versionApp, '1.2.0');
    expect(linea.group(2), '3');
  });

  test('cabecerasApp: versión y plataforma (android en las pruebas)', () {
    expect(cabecerasApp(), {'X-App-Version': '1.2.0', 'X-App-Platform': 'android'});
  });

  group('cada petición lleva las cabeceras de la app', () {
    late List<http.Request> recibidas;
    late MockClient cliente;

    setUp(() {
      recibidas = [];
      cliente = MockClient((req) async {
        recibidas.add(req);
        return http.Response(jsonEncode({'api_key': 'trz_x', 'me': {}, 'data': []}), 200,
            headers: {'content-type': 'application/json'});
      });
    });

    void comprobar(http.Request r) {
      expect(r.headers['X-App-Version'], versionApp, reason: '${r.method} ${r.url.path}');
      expect(r.headers['X-App-Platform'], 'android', reason: '${r.method} ${r.url.path}');
    }

    test('login (sin clave)', () async {
      await ApiCliente.iniciarSesion(baseUrl: 'https://demo.camaronmarket.com', correo: 'a@b.ec', contrasena: 'x', dispositivo: 'X', cliente: cliente);
      comprobar(recibidas.single);
      expect(recibidas.single.headers.containsKey('Authorization'), isFalse);
    });

    test('GET, POST, PATCH, enviarSerializado (firmada) y rutas públicas', () async {
      final api = ApiCliente(baseUrl: 'https://demo.camaronmarket.com', clave: 'trz_x', cliente: cliente)..rolActivo = 'empacadora';
      await api.get('/me');
      await api.get('/public/products');
      await api.post('/me/devices', {}, idempotencia: 'k1');
      await api.patch('/dispatches/d1', {}, idempotencia: 'k2');
      await api.enviarSerializado('POST', '/verifications/v1:start', '{}',
          idempotencia: 'k3', firma: {'X-Device-Id': 'dev', 'X-Signature-Timestamp': '1', 'X-Signature': 'f'});
      await api.cerrarSesion(idempotencia: 'k4');
      expect(recibidas, hasLength(6));
      recibidas.forEach(comprobar);
      // Las cabeceras de la app no pisan las demás.
      expect(recibidas[4].headers['X-Signature'], 'f');
      expect(recibidas[0].headers['X-Shrimp-Role'], 'empacadora');
      expect(recibidas[0].headers['Authorization'], 'Bearer trz_x');
    });

    test('registrar el dispositivo manda la plataforma de la app', () async {
      final api = ApiCliente(baseUrl: 'https://demo.camaronmarket.com', clave: 'trz_x', cliente: cliente);
      await api.registrarDispositivo(nombre: 'Samsung', clavePublica: 'AAAA', idempotencia: 'k');
      expect((jsonDecode(recibidas.single.body) as Map)['platform'], 'android');
      comprobar(recibidas.single);
    });
  });
}
