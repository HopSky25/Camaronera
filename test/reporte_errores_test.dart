// C2 (CONTRATO-MEJORAS-APP.md): reporte de errores sin terceros. Cola local
// (SQLite real con sqflite_common_ffi), envío con señal, máximo 20 al día y
// sin datos personales: solo los 8 campos del contrato.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:verificador_campo/datos/version_app.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/reporte_errores.dart';

const camposContrato = {'app_version', 'platform', 'os_version', 'device_model', 'screen', 'error', 'stack', 'occurred_at'};

void main() {
  setUpAll(sqfliteFfiInit);

  late BaseLocal bd;
  late List<http.Request> recibidas;
  late bool enLinea;
  late int status;
  late DateTime ahora;
  late ReporteErrores reporte;

  setUp(() {
    bd = BaseLocal(
        abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
            options: OpenDatabaseOptions(
                version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));
    recibidas = [];
    enLinea = true;
    status = 204;
    ahora = DateTime(2026, 10, 15, 10);
    reporte = ReporteErrores(
      bd: bd,
      reloj: () => ahora,
      servidor: () => 'https://demo.camaronmarket.com',
      info: () async => const InfoEquipo('Android 14 (SDK 34)', 'samsung SM-A546E'),
      cliente: MockClient((req) async {
        if (!enLinea) throw http.ClientException('sin red');
        recibidas.add(req);
        return http.Response(status == 204 ? '' : jsonEncode({'type': '#problem-x'}), status);
      }),
    );
  });

  tearDown(() async => (await bd.bd).close());

  StackTrace pila(String archivo) => StackTrace.fromString('#0      _Estado.build (package:verificador_campo/pantallas/$archivo.dart:42:7)\n'
      '#1      StatelessElement.build (package:flutter/src/widgets/framework.dart:5000:49)');

  test('cuerpo: exactamente los campos del contrato, con la pantalla de la pila', () async {
    await reporte.capturar(StateError('Bad state: sin datos'), pila('empacadora/pantalla_catalogo'));
    await reporte.enviar();
    final r = recibidas.single;
    expect(r.method, 'POST');
    expect(r.url.toString(), 'https://demo.camaronmarket.com/api/v1/public/app-errors');
    expect(r.headers.containsKey('Authorization'), isFalse, reason: 'público: sin clave');
    expect(r.headers.containsKey('X-Shrimp-Role'), isFalse);
    expect(r.headers['X-App-Version'], isNotEmpty);
    final cuerpo = jsonDecode(r.body) as Map<String, dynamic>;
    expect(cuerpo.keys.toSet(), camposContrato, reason: 'campos extra → 422');
    expect(cuerpo['app_version'], versionApp);
    expect(cuerpo['platform'], 'android');
    expect(cuerpo['os_version'], 'Android 14 (SDK 34)');
    expect(cuerpo['device_model'], 'samsung SM-A546E');
    expect(cuerpo['screen'], 'empacadora/pantalla_catalogo');
    expect(cuerpo['error'], contains('Bad state'));
    expect(cuerpo['stack'], contains('pantalla_catalogo.dart:42'));
    expect(DateTime.parse(cuerpo['occurred_at'] as String).isUtc, isTrue);
    expect(await bd.erroresPendientes(), isEmpty, reason: '204 → fuera de la cola');
  });

  test('sin datos personales: correo, clave, cédula, teléfono, contraseña y parámetros de URL', () async {
    final error = Exception('Falló para juan.perez@finca.ec con trz_AbC123secreta, cédula 0912345678, '
        'tel +593991234567, {"password": "Clave123", "login": "x"} en https://demo.camaronmarket.com/api/v1/x?q=Juan%20Perez');
    final s = StackTrace.fromString('#0 f (package:verificador_campo/datos/x.dart:1:1) Authorization: Bearer trz_otra');
    await reporte.capturar(error, s);
    await reporte.enviar();
    final texto = recibidas.single.body;
    for (final prohibido in ['juan.perez', 'finca.ec', 'trz_', 'AbC123secreta', '0912345678', '991234567', 'Clave123', 'Juan%20Perez']) {
      expect(texto.contains(prohibido), isFalse, reason: 'no debe viajar "$prohibido"');
    }
    expect(texto, contains('<correo>'));
    expect(texto, contains('<clave>'));
  });

  test('de un ErrorApi viaja solo estado y tipo (no el detail del servidor)', () async {
    await reporte.capturar(const ErrorApi(422, 'Datos inválidos', 'El lote "Piscina de Pedro Gómez" no existe', 'validation-error'), null);
    await reporte.enviar();
    final cuerpo = jsonDecode(recibidas.single.body) as Map<String, dynamic>;
    expect(cuerpo['error'], 'ErrorApi(422, validation-error)');
    expect(recibidas.single.body.contains('Pedro'), isFalse);
  });

  test('límites de largo: error ≤ 4000 y stack ≤ 20000', () async {
    await reporte.capturar(Exception('x' * 9000), StackTrace.fromString('y' * 50000));
    await reporte.enviar();
    final cuerpo = jsonDecode(recibidas.single.body) as Map<String, dynamic>;
    expect((cuerpo['error'] as String).length, lessThanOrEqualTo(largoMaximoError));
    expect((cuerpo['stack'] as String).length, lessThanOrEqualTo(largoMaximoStack));
  });

  test('sin señal queda en la cola y se envía al volver la señal', () async {
    enLinea = false;
    await reporte.capturar(Exception('a'), pila('pantalla_fotos'));
    await reporte.enviar();
    expect(await bd.erroresPendientes(), hasLength(1));
    enLinea = true;
    await reporte.enviar();
    expect(recibidas, hasLength(1));
    expect(await bd.erroresPendientes(), isEmpty);
  });

  test('máximo 20 por día por instalación; lo demás sale al día siguiente', () async {
    enLinea = false;
    for (var i = 0; i < 25; i++) {
      await reporte.capturar(Exception('error número $i'), pila('pantalla_$i'));
    }
    enLinea = true;
    await reporte.enviar();
    expect(recibidas, hasLength(reportesPorDia));
    await reporte.capturar(Exception('otro más'), pila('pantalla_x'));
    await reporte.enviar();
    expect(recibidas, hasLength(reportesPorDia), reason: 'el cupo del día ya se gastó');
    expect(await bd.erroresPendientes(), hasLength(6));
    ahora = ahora.add(const Duration(days: 1));
    await reporte.enviar();
    expect(recibidas, hasLength(26));
    expect(await bd.erroresPendientes(), isEmpty);
  });

  test('el mismo error dos veces se encola una sola vez', () async {
    enLinea = false;
    await reporte.capturar(StateError('igual'), pila('pantalla_bandeja'));
    await reporte.capturar(StateError('igual'), pila('pantalla_bandeja'));
    expect(await bd.erroresPendientes(), hasLength(1));
  });

  test('422 (el servidor nunca lo aceptará) se descarta; 429 se reintenta luego', () async {
    status = 422;
    await reporte.capturar(Exception('malo'), null);
    await reporte.enviar();
    expect(await bd.erroresPendientes(), isEmpty);
    status = 429;
    await reporte.capturar(Exception('limitado'), null);
    await reporte.enviar();
    expect(await bd.erroresPendientes(), hasLength(1));
  });

  test('deshabilitado (depuración) no guarda ni envía', () async {
    final apagado = ReporteErrores(bd: bd, habilitado: false, info: () async => const InfoEquipo('a', 'b'));
    await apagado.capturar(Exception('x'), null);
    expect(await bd.erroresPendientes(), isEmpty);
  });

  test('limpiarDatosPersonales deja intactas las líneas de la pila', () {
    const linea = '#3      PantallaFotos._tomar (package:verificador_campo/pantallas/pantalla_fotos.dart:58:7)';
    expect(limpiarDatosPersonales(linea), linea);
  });
}
