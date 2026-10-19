// Publicar lotes desde la app (naupliero, laboratorio, camaronera): campos y
// cuerpo de POST /products según el tipo, validación como el portal, firma
// ES256 de POST /products/{id}:publish verificada como shrimp_api, reintento
// sin duplicar, 403 permission-denied / 422 y modo supervisor.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/lotes_vendedor.dart';
import 'package:verificador_campo/datos/repositorio.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/lote_nuevo.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/pantallas/comunes/widgets.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_mis_lotes.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_publicar_lote.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';

import 'almacen_falso.dart';
import 'compra_directa_test.dart' show baseEnMemoria;
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'servidor_firmas.dart';
import 'sesion_fija.dart';

// Catálogos con la forma de shrimp_api (serializers.stage, uom, size_grade…).
final estadios = [
  {'id': 'st-n', 'name': 'Nauplio', 'code': 'NAUPLIO', 'sequence': 1, 'type': 'nauplio'},
  {'id': 'st-pl12', 'name': 'PL12', 'code': 'PL12', 'sequence': 7, 'type': 'larva'},
  {'id': 'st-juv', 'name': 'Juvenil', 'code': 'JUVENIL', 'sequence': 10, 'type': 'camaron'},
  {'id': 'st-eng', 'name': 'Engorde', 'code': 'ENGORDE', 'sequence': 11, 'type': 'camaron'},
];
final unidades = [
  {'id': 'u-lb', 'name': 'Libras', 'code': 'libra'},
  {'id': 'u-mi', 'name': 'Millares', 'code': 'millar'},
];
final tallas = [
  {'id': 't-3040', 'name': '30/40', 'presentation': 'entero', 'sequence': 1},
  {'id': 't-2125', 'name': '21/25', 'presentation': 'cola', 'sequence': 1},
];
Map<String, dynamic> estadio(String code) => estadios.firstWhere((e) => e['code'] == code);

/// Servidor falso para el formulario: catálogos, /products (borrador) y
/// :archive, sin firma. Guarda lo que recibe.
class ServidorLotes {
  final recibidas = <http.Request>[];
  http.Response Function(http.Request)? forzar;

  late final MockClient cliente = MockClient((req) async {
    recibidas.add(req);
    final ruta = req.url.path.replaceFirst('/api/v1', '');
    final f = forzar;
    if (f != null && req.method == 'POST') return f(req);
    List<Map<String, dynamic>>? lista = switch (ruta) {
      '/public/catalogs/stages' => estadios,
      '/public/catalogs/species' => [
          {'id': 'sp-v', 'name': 'L. vannamei'}
        ],
      '/public/catalogs/genetics-lines' => [],
      '/public/catalogs/size-grades' => tallas,
      '/public/catalogs/uoms' => unidades,
      '/facilities' => [
          {'id': 'f-1', 'name': 'Finca Norte', 'facility_type': 'farm'},
          {'id': 'f-2', 'name': 'Bodega', 'facility_type': 'warehouse'},
        ],
      '/ponds' => [
          {'id': 'p-1', 'name': 'Piscina 3', 'facility': {'id': 'f-1', 'name': 'Finca Norte'}},
        ],
      '/products' when req.method == 'GET' => [],
      _ => null,
    };
    if (lista != null) return respuestaJson(200, {'data': lista, 'meta': {'next_cursor': null}});
    if (ruta == '/products' && req.method == 'POST') {
      final c = jsonDecode(req.body) as Map<String, dynamic>;
      // Como serializers.product_owner: las relaciones son {id, name}.
      final st = estadios.where((e) => e['id'] == c['stage']).firstOrNull;
      return respuestaJson(201, {
        'id': 'nuevo-1',
        'state': 'draft',
        'active': true,
        'name': c['name'],
        'available_qty': c['initial_qty'],
        'stage': st == null ? null : {'id': st['id'], 'name': st['name']},
      });
    }
    if (ruta.endsWith(':archive')) return respuestaJson(200, {'id': 'x', 'state': 'draft', 'active': false});
    if (ruta.endsWith(':publish')) return respuestaJson(200, {'id': 'nuevo-1', 'state': 'published', 'active': true});
    return respuestaJson(404, problema(404, 'not-found', 'No existe.'));
  });

  Iterable<http.Request> posts(String ruta) => recibidas.where((r) => r.method == 'POST' && r.url.path == '/api/v1$ruta');
}

void main() {
  setUpAll(sqfliteFfiInit);

  group('qué publica cada perfil', () {
    test('naupliero y laboratorio: nauplio y larva; camaronera: camarón; los demás, nada', () {
      expect(tiposQuePublica('semillero'), ['nauplio', 'larva']);
      expect(tiposQuePublica('laboratorio'), ['nauplio', 'larva']);
      expect(tiposQuePublica('camaronera'), ['camaron']);
      for (final p in ['empacadora', 'maquilador', 'verificador', 'tecnico', null]) {
        expect(puedePublicarLotes(p), isFalse, reason: '$p');
      }
    });

    test('tipo del estadío: el de la API o, sin él, por el código', () {
      expect(tipoDeEstadio(estadio('PL12')), 'larva');
      expect(tipoDeEstadio({'code': 'NAUPLIO'}), 'nauplio');
      expect(tipoDeEstadio({'code': 'ZOEA'}), 'larva');
      expect(tipoDeEstadio({'code': 'PL20'}), 'larva');
      expect(tipoDeEstadio({'code': 'JUVENIL'}), 'camaron');
      expect(exigeTalla(estadio('ENGORDE')), isTrue);
      expect(exigeTalla(estadio('JUVENIL')), isFalse);
    });

    test('unidad por defecto: millar en nauplio y larva, libra en camarón', () {
      expect(unidadPorDefecto('semillero', unidades)!['id'], 'u-mi');
      expect(unidadPorDefecto('laboratorio', unidades)!['id'], 'u-mi');
      expect(unidadPorDefecto('camaronera', unidades)!['id'], 'u-lb');
      expect(unidadPorDefecto('laboratorio', [unidades.first])!['id'], 'u-lb', reason: 'sin millar, la primera');
    });

    test('las bodegas y oficinas no son origen de un lote', () {
      expect(instalacionDeOrigen({'facility_type': 'farm'}), isTrue);
      expect(instalacionDeOrigen({'facility_type': 'hatchery'}), isTrue);
      expect(instalacionDeOrigen({'facility_type': 'warehouse'}), isFalse);
      expect(instalacionDeOrigen({'facility_type': 'office'}), isFalse);
    });

    test('aviso de verificación: obligatoria en camarón, opcional en nauplio y larva', () {
      expect(avisoVerificacion('camaronera'), contains('requiere verificación en campo'));
      expect(avisoVerificacion('laboratorio'), contains('opcional'));
    });
  });

  group('cuerpo de POST /products según el tipo', () {
    test('nauplio: sin presentación ni talla aunque se hayan marcado, en millares', () {
      final l = LoteNuevo()
        ..nombre = '  Nauplios corrida 32 '
        ..estadio = estadio('NAUPLIO')
        ..presentacion = 'entero'
        ..tallaId = 't-3040'
        ..unidad = unidades[1]
        ..cantidad = '5000'
        ..precio = '0,45';
      expect(jsonEncode(l.aCuerpoApi()),
          '{"name":"Nauplios corrida 32","price":0.45,"initial_qty":5000.0,"uom":"u-mi","stage":"st-n"}');
    });

    test('larva: supervivencia, tamaño, sanidad, ubicación, entrega y origen', () {
      final l = LoteNuevo()
        ..nombre = 'PL12 lote 44'
        ..codigoLote = ' L-44 '
        ..especieId = 'sp-v'
        ..estadio = estadio('PL12')
        ..geneticaId = 'g-1'
        ..supervivencia = '85,5'
        ..tamanoMg = '1.2'
        ..sanidad = 'PCR negativo'
        ..provincia = 'Santa Elena'
        ..fechaEntrega = DateTime(2026, 11, 2)
        ..instalacionId = 'f-1'
        ..piscinaId = 'p-1'
        ..unidad = unidades[1]
        ..cantidad = '1500'
        ..precio = '2.97';
      expect(l.aCuerpoApi(), {
        'name': 'PL12 lote 44',
        'price': 2.97,
        'initial_qty': 1500.0,
        'uom': 'u-mi',
        'species': 'sp-v',
        'stage': 'st-pl12',
        'genetics_line': 'g-1',
        'survival_rate': 85.5,
        'avg_size_mg': 1.2,
        'health_status': 'PCR negativo',
        'location': 'Santa Elena',
        'expected_delivery_date': '2026-11-02',
        'origin_facility': 'f-1',
        'origin_pond': 'p-1',
        'batch_code': 'L-44',
      });
    });

    test('camarón de engorde: presentación y talla, en libras', () {
      final l = LoteNuevo()
        ..nombre = 'Camarón 30/40'
        ..estadio = estadio('ENGORDE')
        ..presentacion = 'entero'
        ..tallaId = 't-3040'
        ..unidad = unidades[0]
        ..cantidad = '12000'
        ..precio = '2.10';
      final c = l.aCuerpoApi();
      expect(c['presentation'], 'entero');
      expect(c['size_grade'], 't-3040');
      expect(c['uom'], 'u-lb');
      expect(c['stage'], 'st-eng');
    });

    test('sin instalación no se manda la piscina; sin presentación no se manda la talla', () {
      final l = LoteNuevo()
        ..nombre = 'Juvenil'
        ..estadio = estadio('JUVENIL')
        ..tallaId = 't-3040'
        ..piscinaId = 'p-1'
        ..cantidad = '10'
        ..precio = '3';
      final c = l.aCuerpoApi();
      expect(c.containsKey('origin_pond'), isFalse);
      expect(c.containsKey('size_grade'), isFalse);
      expect(c.containsKey('presentation'), isFalse);
      expect(c.containsKey('survival_rate'), isFalse);
    });
  });

  group('validación (como el portal)', () {
    test('nombre, etapa, cantidad mayor a 0 y precio son obligatorios', () {
      final e = (LoteNuevo()..unidad = unidades[1]).errores('laboratorio');
      expect(e.keys, containsAll(['nombre', 'estadio', 'cantidad', 'precio']));
      expect(e['precio'], 'Ingresa el precio por millar para poder publicar.');
      final ok = LoteNuevo()
        ..nombre = 'x'
        ..estadio = estadio('PL12')
        ..cantidad = '1'
        ..precio = '0';
      expect(ok.errores('laboratorio'), isEmpty, reason: 'precio 0 vale (como en el portal)');
      expect((ok..cantidad = '0').errores('laboratorio').keys, ['cantidad']);
      expect((ok..cantidad = 'abc').errores('laboratorio').keys, ['cantidad']);
    });

    test('la etapa tiene que poder publicarla el perfil', () {
      final l = LoteNuevo()
        ..nombre = 'x'
        ..estadio = estadio('PL12')
        ..cantidad = '1'
        ..precio = '1';
      expect(l.errores('camaronera')['estadio'], 'Tu perfil no publica larva.');
      expect((l..estadio = estadio('ENGORDE')).errores('semillero')['estadio'], 'Tu perfil no publica camarón.');
    });

    test('engorde exige presentación y talla; juvenil no', () {
      final l = LoteNuevo()
        ..nombre = 'x'
        ..estadio = estadio('ENGORDE')
        ..cantidad = '1'
        ..precio = '1';
      expect(l.errores('camaronera').keys, ['presentacion', 'talla']);
      expect((l..estadio = estadio('JUVENIL')).errores('camaronera'), isEmpty);
    });

    test('supervivencia de 0 a 100 y tamaño no negativo (opcionales)', () {
      final l = LoteNuevo()
        ..nombre = 'x'
        ..estadio = estadio('PL12')
        ..cantidad = '1'
        ..precio = '1'
        ..supervivencia = '120'
        ..tamanoMg = '-1';
      expect(l.errores('laboratorio').keys, containsAll(['supervivencia', 'tamano']));
    });

    test('qué le falta a un borrador para publicarse', () {
      expect(faltantesParaPublicar({'stage': estadio('PL12'), 'available_qty': 10}), isEmpty);
      expect(faltantesParaPublicar({'stage': null, 'available_qty': 10}), ['la etapa']);
      expect(faltantesParaPublicar({'stage': estadio('ENGORDE'), 'available_qty': 0}),
          ['cantidad disponible', 'la presentación', 'la talla']);
      // El lote trae la etapa como {id, name}: el código sale del catálogo.
      final ref = {'stage': {'id': 'st-eng', 'name': 'Engorde'}, 'available_qty': 5};
      expect(faltantesParaPublicar(ref), isEmpty, reason: 'sin catálogo no se sabe que es engorde (lo dice el servidor)');
      expect(faltantesParaPublicar(ref, estadios: estadios), ['la presentación', 'la talla']);
    });

    test('un 422 marca los campos del formulario y se muestra el detail sin repetir', () {
      final e = ErrorApi(422, 'Datos no válidos', 'x', 'validation-error', datos: {
        'detail': 'La etapa «Engorde» no la puede publicar un perfil laboratorio.',
        'errors': [
          {'field': 'stage', 'message': 'La etapa «Engorde» no la puede publicar un perfil laboratorio.'},
          {'field': 'origin_pond', 'message': 'Elige primero la instalación.'},
          {'field': 'otro', 'message': '¿?'},
        ],
      });
      expect(erroresDeCampo(e), {
        'estadio': 'La etapa «Engorde» no la puede publicar un perfil laboratorio.',
        'piscina': 'Elige primero la instalación.',
      });
      expect(mensajeLote(e), 'La etapa «Engorde» no la puede publicar un perfil laboratorio.');
    });
  });

  group('POST /products/{id}:publish firmado', () {
    late ServidorFirmas servidor;
    late BaseLocal bd;
    late Sesion sesion;
    late Sincronizador sinc;
    final ahora = DateTime.utc(2026, 10, 19, 12);

    setUp(() async {
      servidor = ServidorFirmas()
        ..ahora = ahora
        ..respuesta = (req) => {'id': 'lote-1', 'state': 'published'};
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

    test('la ruta está entre las firmadas, en sus dos formas', () {
      expect(esRutaSensible('POST', '/products/lote-1:publish'), isTrue);
      expect(esRutaSensible('POST', '/api/v1/products/lote-1/publish'), isTrue);
      expect(esRutaSensible('POST', '/products'), isFalse, reason: 'crear el borrador no se firma');
      expect(esRutaSensible('PATCH', '/products/lote-1'), isFalse);
      expect(esRutaSensible('POST', '/products/lote-1:archive'), isFalse, reason: 'retirar no se firma');
    });

    test('firma la cadena canónica y el servidor la verifica', () async {
      final op = await sinc.firmarPublicacionLote('lote-1');
      expect(op.tipo, TipoOperacion.publicarLote);
      expect(op.firma, isNotNull);
      final r = await sinc.enviarFirmadaAhora(op);
      expect(r['state'], 'published');
      final a = servidor.aceptadas.single;
      expect(a.req.url.path, '/api/v1/products/lote-1:publish');
      expect(a.req.method, 'POST');
      expect(a.req.body, '{}');
      expect(a.req.headers['X-Shrimp-Role'], 'camaronera');
      expect(a.cadena.split('\n').first, 'POST');
      expect(sinc.cola, isEmpty, reason: 'publicar no pasa por la cola');
    });

    test('sin señal no publica; el reintento manda la MISMA operación (misma Idempotency-Key y firma)', () async {
      final op = await sinc.firmarPublicacionLote('lote-1');
      servidor.enLinea = false;
      await expectLater(sinc.enviarFirmadaAhora(op), throwsA(isA<SinConexion>()));
      servidor.enLinea = true;
      await sinc.enviarFirmadaAhora(op);
      await sinc.enviarFirmadaAhora(op); // reintento idempotente legítimo: no es replay
      expect(servidor.aceptadas.map((a) => a.req.headers['Idempotency-Key']).toSet(), {op.claveIdempotencia});
      expect(servidor.aceptadas.map((a) => a.req.headers['X-Signature']).toSet(), hasLength(1));
    });

    test('403 permission-denied (subusuario sin «Vender») y 422: se muestra el motivo', () async {
      servidor.forzar = (_) => respuestaJson(403, problema(403, 'permission-denied', ''));
      final op = await sinc.firmarPublicacionLote('lote-1');
      await expectLater(
          sinc.enviarFirmadaAhora(op),
          throwsA(isA<ErrorApi>()
              .having((e) => e.esDefinitivo, 'definitivo', isTrue)
              .having((e) => mensajeLote(e), 'mensaje', 'El administrador de tu cuenta no te dio permiso para esto.')));
      servidor.forzar = (_) => respuestaJson(403, problema(403, 'permission-denied', 'No tienes permiso «Vender» en el perfil Laboratorio.'));
      await expectLater(sinc.enviarFirmadaAhora(await sinc.firmarPublicacionLote('lote-1')),
          throwsA(isA<ErrorApi>().having((e) => mensajeLote(e), 'mensaje', contains('«Vender»'))));
      servidor.forzar = (_) => respuestaJson(422, problema(422, 'validation-error', 'Elige la etapa del lote antes de publicarlo.'));
      await expectLater(sinc.enviarFirmadaAhora(await sinc.firmarPublicacionLote('lote-1')),
          throwsA(isA<ErrorApi>().having((e) => mensajeLote(e), 'mensaje', 'Elige la etapa del lote antes de publicarlo.')));
    });

    test('si no se confirma con la huella no se envía nada', () async {
      (sesion.firmador as FirmadorFalso).fallarCon = 'cancelado';
      await expectLater(sinc.firmarPublicacionLote('lote-1'), throwsA(anything));
      expect(servidor.a('/products/lote-1:publish'), isEmpty);
    });
  });

  group('borrador y retirar (sin firma)', () {
    late ServidorLotes servidor;
    late SesionFija sesion;
    late Sincronizador sinc;

    setUp(() {
      servidor = ServidorLotes();
      sesion = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      sinc = Sincronizador(sesion: sesion, bd: baseEnMemoria());
    });
    tearDown(() => sinc.dispose());

    test('POST /products con Idempotency-Key, el perfil y sin cabeceras de firma', () async {
      final p = await sinc.crearBorradorLote({'name': 'PL12', 'price': 1.0, 'initial_qty': 10.0}, idempotencia: 'k-1');
      expect(p['state'], 'draft');
      final r = servidor.posts('/products').single;
      expect(r.headers['Idempotency-Key'], 'k-1');
      expect(r.headers['X-Shrimp-Role'], 'laboratorio');
      expect(r.headers.containsKey('X-Signature'), isFalse);
      await sinc.archivarLote('lote-9', idempotencia: 'k-2');
      final a = servidor.posts('/products/lote-9:archive').single;
      expect(a.headers['Idempotency-Key'], 'k-2');
      expect(a.headers.containsKey('X-Signature'), isFalse);
    });

    test('403 permission-denied al crear el borrador', () async {
      servidor.forzar = (_) => respuestaJson(403, problema(403, 'permission-denied', 'No tienes permiso «Vender».'));
      await expectLater(sinc.crearBorradorLote({'name': 'x'}, idempotencia: 'k'),
          throwsA(isA<ErrorApi>().having((e) => mensajeLote(e), 'mensaje', 'No tienes permiso «Vender».')));
    });

    test('modo supervisor: ni borrador, ni retirar, ni firma', () async {
      sesion.supervisor = true;
      await expectLater(sinc.crearBorradorLote({'name': 'x'}, idempotencia: 'k'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.archivarLote('x', idempotencia: 'k'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.firmarPublicacionLote('x'), throwsA(isA<ErrorSoloLectura>()));
      expect(servidor.recibidas, isEmpty);
    });
  });

  group('pantallas', () {
    Future<void> abrir(WidgetTester t, SesionFija sesion, Widget pantalla) async {
      final bd = baseEnMemoria();
      await t.pumpWidget(MultiProvider(
        providers: [
          ChangeNotifierProvider<Sesion>.value(value: sesion),
          ChangeNotifierProvider(create: (_) => Sincronizador(sesion: sesion, bd: bd)),
          Provider(create: (_) => Repositorio(sesion: sesion, bd: bd)),
        ],
        child: MaterialApp(home: pantalla),
      ));
      await Future<void>.delayed(const Duration(milliseconds: 300));
    }

    testWidgets('laboratorio: solo etapas de nauplio y larva, precio "por millar"; sin datos no se guarda', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorLotes();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaPublicarLote()));
      await t.pump();
      expect(find.widgetWithText(ChoiceChip, 'Nauplio'), findsOneWidget);
      expect(find.widgetWithText(ChoiceChip, 'PL12'), findsOneWidget);
      expect(find.widgetWithText(ChoiceChip, 'Engorde'), findsNothing);
      expect(find.text('Presentación *'), findsNothing);
      expect(find.text('Precio por millar *'), findsOneWidget);
      expect(find.text('Cantidad disponible en millares *'), findsOneWidget);
      expect(find.textContaining('opcional: solo se hace si el comprador'), findsOneWidget);
      expect(find.textContaining('fotos y los certificados del lote se agregan desde el portal web'), findsOneWidget);
      await t.ensureVisible(find.text('Guardar borrador'));
      await t.tap(find.text('Guardar borrador'));
      await t.pump();
      expect(find.text('Revisa los campos marcados en rojo.'), findsOneWidget);
      expect(find.text('Escribe el nombre del producto.'), findsOneWidget);
      expect(find.text('Elige la etapa del lote.'), findsOneWidget);
      expect(find.text('La cantidad debe ser mayor a 0.'), findsOneWidget);
      expect(servidor.posts('/products'), isEmpty);
    });

    testWidgets('laboratorio: guardar borrador, ver el resumen y publicar', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorLotes();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaPublicarLote()));
      await t.pump();
      await t.enterText(find.widgetWithText(TextFormField, 'Nombre del producto *'), 'PL12 corrida 7');
      await t.ensureVisible(find.widgetWithText(ChoiceChip, 'PL12'));
      await t.tap(find.widgetWithText(ChoiceChip, 'PL12'));
      await t.pump();
      await t.enterText(find.widgetWithText(TextFormField, 'Cantidad disponible en millares *'), '1500');
      await t.enterText(find.widgetWithText(TextFormField, 'Precio por millar *'), '2,97');
      await t.pump();
      expect(find.textContaining(r'Total del lote: $ 4,455.00'), findsOneWidget);
      await t.ensureVisible(find.text('Guardar borrador'));
      await t.tap(find.text('Guardar borrador'));
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
      await t.pumpAndSettle();
      final post = servidor.posts('/products').single;
      expect(jsonDecode(post.body), {'name': 'PL12 corrida 7', 'price': 2.97, 'initial_qty': 1500.0, 'uom': 'u-mi', 'stage': 'st-pl12'});
      expect(find.textContaining('Borrador guardado'), findsOneWidget);
      await t.ensureVisible(find.text('Publicar con mi huella'));
      await t.tap(find.text('Publicar con mi huella'));
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
      await t.pump();
      expect(servidor.posts('/products/nuevo-1:publish'), hasLength(1));
      expect(find.text('Lote publicado: ya está en el marketplace.'), findsWidgets);
    });

    testWidgets('camaronera: camarón en libras; engorde pide presentación y talla', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorLotes();
      final s = SesionFija(servidor.cliente, perfilFijo: 'camaronera');
      await t.runAsync(() => abrir(t, s, const PantallaPublicarLote()));
      await t.pump();
      expect(find.widgetWithText(ChoiceChip, 'PL12'), findsNothing);
      expect(find.text('Precio por libra *'), findsOneWidget);
      expect(find.textContaining('Este lote requiere verificación en campo'), findsOneWidget);
      await t.ensureVisible(find.widgetWithText(ChoiceChip, 'Engorde'));
      await t.tap(find.widgetWithText(ChoiceChip, 'Engorde'));
      await t.pump();
      expect(find.text('Presentación *'), findsOneWidget);
      expect(find.text('Talla *'), findsOneWidget);
      expect(find.text('Elige primero la presentación.'), findsOneWidget);
      await t.ensureVisible(find.widgetWithText(ChoiceChip, 'Cola'));
      await t.tap(find.widgetWithText(ChoiceChip, 'Cola'));
      await t.pump();
      expect(find.widgetWithText(ChoiceChip, '21/25'), findsOneWidget);
      expect(find.widgetWithText(ChoiceChip, '30/40'), findsNothing, reason: 'tallas filtradas por presentación');
    });

    testWidgets('modo supervisor: ni "Publicar lote" en Mis lotes ni formulario', (t) async {
      final servidor = ServidorLotes();
      final s = SesionFija(servidor.cliente, perfilFijo: 'camaronera', supervisor: true);
      await t.runAsync(() => abrir(t, s, const PantallaMisLotes()));
      await t.pump();
      expect(find.text('Publicar lote'), findsNothing);
      await t.runAsync(() => abrir(t, s, const PantallaPublicarLote()));
      await t.pump();
      expect(find.byType(AvisoSoloLectura), findsOneWidget);
      expect(find.text('Guardar borrador'), findsNothing);
    });

    testWidgets('Mis lotes de un laboratorio: botón "Publicar lote"', (t) async {
      final servidor = ServidorLotes();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaMisLotes()));
      await t.pump();
      expect(find.text('Publicar lote'), findsOneWidget);
    });

    testWidgets('resumen de un borrador al que le falta la etapa: no se puede publicar desde la app', (t) async {
      final s = SesionFija(ServidorLotes().cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaConfirmarPublicacion(p: {
            'id': 'b-1',
            'name': 'Sin etapa',
            'state': 'draft',
            'active': true,
            'available_qty': 10,
            'uom': {'id': 'u-mi', 'name': 'Millares'},
            'price': {'amount': 2.0, 'currency': 'USD'},
          })));
      await t.pump();
      expect(find.textContaining('Para publicarlo falta la etapa'), findsOneWidget);
      expect(find.text('Publicar con mi huella'), findsNothing);
      expect(find.text(r'$ 2.00 por millar'), findsOneWidget);
    });
  });
}
