// C5 (CONTRATO-MEJORAS-APP.md): compra directa de larva / nauplio. Cantidad
// (stock y unidad), firma ES256 de POST /purchases verificada como
// shrimp_api, reintento sin duplicar, errores con el detail del servidor y
// modo supervisor (oculto).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/compra_directa.dart';
import 'package:verificador_campo/pantallas/comunes/formato.dart';
import 'package:verificador_campo/pantallas/comunes/widgets.dart';
import 'package:verificador_campo/pantallas/empacadora/pantalla_catalogo.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';
import 'package:verificador_campo/seguridad/firmador.dart';

import 'almacen_falso.dart';
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'servidor_firmas.dart';
import 'sesion_fija.dart';

Map<String, dynamic> loteLarva({num disponible = 2500, String unidad = 'Millares', bool verificacion = false}) => {
      'id': '0b8f5c2e-4a1d-4e6b-9c3f-1a2b3c4d5e6f',
      'name': 'Larva PL12 Lote 44',
      'seller': {'id': 's1', 'name': 'Laboratorio Bioaqua'},
      'seller_type': 'laboratorio',
      'stage': {'id': 'e1', 'name': 'PL12'},
      'uom': {'id': 'u1', 'name': unidad},
      'available_qty': disponible,
      'price': {'amount': 2.97, 'currency': 'USD'},
      'requires_verification': verificacion,
    };

BaseLocal baseEnMemoria() => BaseLocal(
    abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
        options: OpenDatabaseOptions(
            version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));

void main() {
  setUpAll(sqfliteFfiInit);

  group('unidades', () {
    test('"por libra" y "por millar": singular y en minúscula', () {
      expect(porUnidad({'name': 'Libras'}), 'por libra');
      expect(porUnidad({'name': 'Millares'}), 'por millar');
      expect(porUnidad({'name': 'Unidades'}), 'por unidad');
      expect(precioPorUnidad({'amount': 2.97, 'currency': 'USD'}, {'name': 'Libras'}), r'$ 2.97 por libra');
      expect(cantidad(1, {'name': 'Millares'}), '1 millar');
      expect(cantidad(1200, {'name': 'Libras'}), '1,200 libras');
    });
  });

  group('validarCantidadCompra', () {
    test('vacía, cero o negativa', () {
      expect(validarCantidadCompra('', loteLarva()), contains('Escribe la cantidad'));
      expect(validarCantidadCompra('0', loteLarva()), isNotNull);
      expect(validarCantidadCompra('-3', loteLarva()), isNotNull);
      expect(validarCantidadCompra('abc', loteLarva()), isNotNull);
    });
    test('no más que el disponible', () {
      expect(validarCantidadCompra('2500', loteLarva()), isNull);
      expect(validarCantidadCompra('2500,5', loteLarva()), contains('Solo hay 2,500 millares'));
      expect(validarCantidadCompra('1', loteLarva(disponible: 0)), contains('ya no tiene disponible'));
    });
    test('decimales según la unidad: millares hasta 3, unidades enteras, libras hasta 2', () {
      expect(validarCantidadCompra('12,345', loteLarva()), isNull, reason: 'coma decimal');
      expect(validarCantidadCompra('12.3456', loteLarva()), contains('hasta 3 decimales'));
      expect(validarCantidadCompra('10', loteLarva(unidad: 'Unidades')), isNull);
      expect(validarCantidadCompra('10.5', loteLarva(unidad: 'Unidades')), contains('número entero'));
      expect(validarCantidadCompra('100.25', loteLarva(unidad: 'Libras')), isNull);
      expect(validarCantidadCompra('100.255', loteLarva(unidad: 'Libras')), contains('hasta 2 decimales'));
    });
    test('qué lotes se compran directo', () {
      expect(esCompraDirecta(loteLarva()), isTrue);
      expect(esCompraDirecta(loteLarva(verificacion: true)), isFalse);
    });
  });

  group('POST /purchases firmado', () {
    late ServidorFirmas servidor;
    late BaseLocal bd;
    late Sesion sesion;
    late Sincronizador sinc;
    final ahora = DateTime.utc(2026, 10, 15, 12);

    setUp(() async {
      servidor = ServidorFirmas()
        ..ahora = ahora
        ..respuesta = (req) => {'id': 'tx-1', 'reference': 'TX-0099', 'state': 'confirmed', 'qty': 25};
      bd = baseEnMemoria();
      sesion = Sesion(credenciales: AlmacenFalso(), bd: bd, clienteHttp: servidor.cliente, firmador: FirmadorFalso(), reloj: () => ahora);
      sinc = Sincronizador(sesion: sesion, bd: bd);
      await sesion.iniciarSesion(url: 'demo.camaronmarket.com', correo: 'socio@finca.ec', contrasena: 'x');
      await sesion.proteger();
    });

    tearDown(() async {
      await Future<void>.delayed(const Duration(milliseconds: 20));
      sinc.dispose();
      await (await bd.bd).close();
    });

    test('firma la cadena canónica y el servidor la verifica; devuelve la compra', () async {
      final op = await sinc.firmarCompraDirecta(productoId: loteLarva()['id'] as String, cantidad: 25, notas: '  ');
      expect(op.firma, isNotNull);
      final tx = await sinc.enviarCompraDirecta(op);
      expect(tx['reference'], 'TX-0099');
      final a = servidor.aceptadas.single;
      expect(a.req.url.path, '/api/v1/purchases');
      expect(a.req.method, 'POST');
      expect(a.req.body, '{"product":"0b8f5c2e-4a1d-4e6b-9c3f-1a2b3c4d5e6f","qty":25.0}', reason: 'sin notes vacías');
      expect(a.req.headers['X-Shrimp-Role'], 'camaronera');
      expect(a.req.headers['X-App-Version'], isNotEmpty);
      expect(esRutaSensible('POST', '/purchases'), isTrue);
      expect(sinc.cola, isEmpty, reason: 'la compra directa no pasa por la cola');
    });

    test('sin señal no compra; el reintento manda la MISMA operación (misma Idempotency-Key y firma)', () async {
      final op = await sinc.firmarCompraDirecta(productoId: 'p', cantidad: 3, notas: 'Entregar el martes');
      servidor.enLinea = false;
      await expectLater(sinc.enviarCompraDirecta(op), throwsA(isA<SinConexion>()));
      servidor.enLinea = true;
      await sinc.enviarCompraDirecta(op);
      await sinc.enviarCompraDirecta(op); // reintento idempotente legítimo: no es replay
      final enviadas = servidor.aceptadas.map((a) => a.req.headers['Idempotency-Key']).toSet();
      expect(enviadas, {op.claveIdempotencia});
      expect(jsonDecode(servidor.aceptadas.first.req.body)['notes'], 'Entregar el martes');
    });

    test('422 verification-required, 403 forbidden y 422 business-rule: se muestra el detail', () async {
      servidor.forzar = (_) => respuestaJson(422, problema(422, 'verification-required', 'Este lote exige verificación.'));
      final op = await sinc.firmarCompraDirecta(productoId: 'p', cantidad: 1);
      await expectLater(sinc.enviarCompraDirecta(op), throwsA(isA<ErrorApi>().having((e) => e.tipo, 'tipo', 'verification-required')));
      servidor.forzar = (_) => respuestaJson(403, problema(403, 'forbidden', 'Una empacadora no compra larva.'));
      await expectLater(sinc.enviarCompraDirecta(await sinc.firmarCompraDirecta(productoId: 'p', cantidad: 1)),
          throwsA(isA<ErrorApi>().having((e) => e.mensaje, 'mensaje', 'Una empacadora no compra larva.')));
      servidor.forzar = (_) => respuestaJson(422, problema(422, 'business-rule', 'La cantidad solicitada supera el stock disponible.'));
      await expectLater(sinc.enviarCompraDirecta(await sinc.firmarCompraDirecta(productoId: 'p', cantidad: 1)),
          throwsA(isA<ErrorApi>().having((e) => e.mensaje, 'mensaje', contains('stock'))));
    });

    test('si no se confirma con la huella no se envía nada', () async {
      (sesion.firmador as FirmadorFalso).fallarCon = 'cancelado';
      await expectLater(sinc.firmarCompraDirecta(productoId: 'p', cantidad: 1), throwsA(isA<ErrorSeguridad>()));
      expect(servidor.a('/purchases'), isEmpty);
    });
  });

  group('pantalla', () {
    Future<void> abrir(WidgetTester t, Sesion sesion) async {
      final sinc = Sincronizador(sesion: sesion, bd: baseEnMemoria());
      await t.pumpWidget(MultiProvider(
        providers: [ChangeNotifierProvider<Sesion>.value(value: sesion), ChangeNotifierProvider.value(value: sinc)],
        child: MaterialApp(home: PantallaCompraDirecta(p: loteLarva())),
      ));
      await t.pump();
    }

    MockClient sinRed() => MockClient((_) async => http.Response('{}', 200));

    testWidgets('modo supervisor: sin cantidad ni botón Comprar, con el aviso de solo lectura', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 1400));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final s = SesionFija(sinRed(), supervisor: true);
      await t.runAsync(() => abrir(t, s));
      await t.pump();
      expect(find.byType(AvisoSoloLectura), findsOneWidget);
      expect(find.text('Comprar'), findsNothing);
      expect(find.byType(TextFormField), findsNothing);
    });

    testWidgets('cuenta normal: "por millar", y una cantidad mayor al stock no pide huella', (t) async {
      final s = SesionFija(sinRed(), perfilFijo: 'camaronera');
      await t.runAsync(() => abrir(t, s));
      await t.pump();
      expect(find.text(r'$ 2.97 por millar'), findsOneWidget);
      await t.enterText(find.byType(TextFormField).first, '9000');
      await t.scrollUntilVisible(find.text('Comprar'), 200, scrollable: find.byType(Scrollable).first);
      await t.tap(find.text('Comprar'));
      await t.pump();
      expect(find.textContaining('Solo hay 2,500 millares'), findsOneWidget);
      expect(find.text('Confirmar la compra'), findsNothing);
    });
  });
}
