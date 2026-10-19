// Gestionar un lote desde la app sin el portal (shrimp_api 19.0.1.17.0):
// editar (solo lo que cambió; en un lote publicado, precio, cantidad y
// unidad firmados con :update-terms), campos bloqueados por compras, fotos
// (subir, portada, orden, quitar), certificados (ligar, subir, quitar),
// reactivar firmado y eliminar un borrador, con los 422 en su campo y el
// modo oscuro.
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

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
import 'package:verificador_campo/modelos/gestion_lote.dart';
import 'package:verificador_campo/modelos/lote_nuevo.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_certificados_lote.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_fotos_lote.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_mis_lotes.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_publicar_lote.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';
import 'package:verificador_campo/tema.dart';

import 'almacen_falso.dart';
import 'compra_directa_test.dart' show baseEnMemoria;
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'publicar_lote_test.dart' show estadios, tallas, unidades;
import 'servidor_firmas.dart';
import 'sesion_fija.dart';

final png = Uint8List.fromList([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0]);
final pdf = Uint8List.fromList(utf8.encode('%PDF-1.4\n%%EOF\n'));

/// Un lote como lo devuelve `GET /products/{id}` (serializers.product_owner).
Map<String, dynamic> lote({String estado = 'draft', bool activo = true, List<String> bloqueados = const [], String? motivo}) => {
      'id': 'lote-1',
      'name': 'PL12 corrida 7',
      'state': estado,
      'active': activo,
      'stage': {'id': 'st-pl12', 'name': 'PL12'},
      'species': {'id': 'sp-v', 'name': 'L. vannamei'},
      'uom': {'id': 'u-mi', 'name': 'Millares'},
      'price': {'amount': 2.5, 'currency': 'USD'},
      'initial_qty': 1500.0,
      'available_qty': 1500.0,
      'reserved_qty': 0.0,
      'survival_rate': 85.5,
      'avg_size_mg': null,
      'health_status': 'PCR negativo',
      'location': 'Santa Elena',
      'expected_delivery_date': '2026-11-02',
      'origin_facility': null,
      'origin_pond': null,
      'presentation': null,
      'size_grade': null,
      'batch_code': 'L-7',
      'photos': [
        {'id': 'f-a', 'filename': 'a.jpg'},
        {'id': 'f-b', 'filename': 'b.jpg'},
      ],
      'certificate_lines': [],
      'locks': {'fields': bloqueados, 'reason': motivo},
      'can_reactivate': !activo,
      'requires_verification': false,
    };

/// Servidor falso de la gestión de un lote: guarda lo que recibe y responde
/// con la forma de shrimp_api.
class ServidorGestion {
  final recibidas = <http.Request>[];
  Map<String, dynamic> producto = lote();
  List<Map<String, dynamic>> fotos = [
    {'id': 'f-a', 'filename': 'a.jpg'},
    {'id': 'f-b', 'filename': 'b.jpg'},
  ];
  http.Response Function(http.Request)? forzar;

  Map<String, dynamic> certificados = {
    'data': [
      {'id': 'l-1', 'certificate': {'id': 'c-pcr', 'name': 'PCR WSSV'}, 'issuer': 'Lab', 'number': 'A-1', 'expiry_date': '2027-01-01',
       'status': 'approved', 'source': 'account'},
    ],
    'available': [
      {'id': 'u-2', 'certificate': {'id': 'c-bap', 'name': 'BAP'}, 'issuer': 'GSA', 'number': 'B-9', 'expiry_date': '2027-05-01', 'status': 'approved'},
    ],
    'catalog': [
      {'id': 'c-pcr', 'name': 'PCR WSSV', 'expires': true},
      {'id': 'c-otro', 'name': 'Otro', 'expires': false},
    ],
  };

  late final MockClient cliente = MockClient((req) async {
    recibidas.add(req);
    final ruta = req.url.path.replaceFirst('/api/v1', '');
    final f = forzar;
    if (f != null && req.method != 'GET') return f(req);
    List<Map<String, dynamic>>? lista = switch (ruta) {
      '/public/catalogs/stages' => estadios,
      '/public/catalogs/species' => [
          {'id': 'sp-v', 'name': 'L. vannamei'}
        ],
      '/public/catalogs/genetics-lines' => [],
      '/public/catalogs/size-grades' => tallas,
      '/public/catalogs/uoms' => unidades,
      '/facilities' || '/ponds' => [],
      _ => null,
    };
    if (lista != null) return respuestaJson(200, {'data': lista, 'meta': {'next_cursor': null}});
    if (ruta == '/products' && req.method == 'GET') {
      return respuestaJson(200, {'data': [producto], 'meta': {'next_cursor': null}});
    }
    if (ruta == '/products/lote-1') {
      if (req.method == 'GET') return respuestaJson(200, producto);
      if (req.method == 'PATCH') {
        final c = jsonDecode(req.body) as Map<String, dynamic>;
        if (c.containsKey('price')) producto = {...producto, 'price': {'amount': c['price'], 'currency': 'USD'}};
        return respuestaJson(200, producto);
      }
      if (req.method == 'DELETE') return respuestaJson(200, {...producto, 'state': 'cancel', 'active': false});
    }
    if (ruta == '/products/lote-1:update-terms' || ruta == '/products/lote-1:unarchive') {
      return respuestaJson(200, {...producto, 'active': true});
    }
    if (ruta == '/products/lote-1/photos' && req.method == 'GET') return respuestaJson(200, {'data': fotos});
    if (ruta == '/products/lote-1/photos' && req.method == 'POST') {
      fotos = [...fotos, {'id': 'f-n${fotos.length}', 'filename': 'nueva.jpg'}];
      return respuestaJson(201, {'data': fotos, 'photo': fotos.last});
    }
    if (ruta == '/products/lote-1/photos:reorder') {
      final orden = ((jsonDecode(req.body) as Map)['order'] as List).cast<String>();
      fotos = [for (final id in orden) fotos.firstWhere((f) => f['id'] == id)];
      return respuestaJson(200, {'data': fotos});
    }
    if (ruta.startsWith('/products/lote-1/photos/')) {
      final id = ruta.split('/').last;
      if (req.method == 'DELETE') {
        fotos = fotos.where((f) => f['id'] != id).toList();
        return respuestaJson(200, {'data': fotos});
      }
      return http.Response.bytes(png, 200, headers: {'content-type': 'image/png'});
    }
    if (ruta == '/products/lote-1/certificates') {
      if (req.method == 'POST') {
        final c = jsonDecode(req.body) as Map<String, dynamic>;
        final linea = c.containsKey('account_certificate')
            ? {'id': 'l-2', 'certificate': {'id': 'c-bap', 'name': 'BAP'}, 'status': 'approved', 'source': 'account'}
            : {'id': 'l-3', 'certificate': {'id': c['certificate'], 'name': 'PCR WSSV'}, 'status': 'pending', 'source': 'product'};
        certificados = {...certificados, 'data': [...certificados['data'] as List, linea], 'available': [], 'line': linea};
        return respuestaJson(201, certificados);
      }
      return respuestaJson(200, certificados);
    }
    if (ruta.startsWith('/products/lote-1/certificates/') && req.method == 'DELETE') {
      certificados = {...certificados, 'data': []};
      return respuestaJson(200, certificados);
    }
    return respuestaJson(404, problema(404, 'not-found', 'No existe: ${req.method} $ruta'));
  });

  Iterable<http.Request> de(String metodo, String ruta) =>
      recibidas.where((r) => r.method == metodo && r.url.path == '/api/v1$ruta');
}

/// Abre [pantalla] ENCIMA de una pantalla vacía (las que guardan y vuelven
/// hacen `Navigator.pop`).
Future<void> abrir(WidgetTester t, SesionFija sesion, Widget pantalla, {bool oscuro = false}) async {
  final bd = baseEnMemoria();
  final navegador = GlobalKey<NavigatorState>();
  await t.pumpWidget(const SizedBox());
  await t.pumpWidget(MultiProvider(
    providers: [
      ChangeNotifierProvider<Sesion>.value(value: sesion),
      ChangeNotifierProvider(create: (_) => Sincronizador(sesion: sesion, bd: bd)),
      Provider(create: (_) => Repositorio(sesion: sesion, bd: bd)),
    ],
    child: MaterialApp(
      navigatorKey: navegador,
      theme: oscuro ? temaOscuro() : temaClaro(),
      home: const Scaffold(body: Text('inicio')),
    ),
  ));
  unawaited(navegador.currentState!.push(MaterialPageRoute<Object?>(builder: (_) => pantalla)));
  await t.pump();
  await t.pump(const Duration(milliseconds: 400));
  await Future<void>.delayed(const Duration(milliseconds: 300));
}

/// Deja correr las peticiones falsas y pinta el resultado.
Future<void> esperar(WidgetTester t) async {
  await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
  await t.pump();
  await t.pump(const Duration(milliseconds: 400));
}

/// En oscuro ningún texto queda oscuro sobre el fondo oscuro.
void sinTextoOscuro(WidgetTester t, String pantalla) {
  for (final rt in t.widgetList<RichText>(find.byType(RichText))) {
    void revisar(InlineSpan s, Color? heredado) {
      final color = s.style?.color ?? heredado;
      if (s is TextSpan && (s.text?.trim().isNotEmpty ?? false) && color != null) {
        expect(color.computeLuminance(), greaterThan(0.18), reason: '$pantalla: «${s.text}» en $color');
      }
      if (s is TextSpan) {
        for (final h in s.children ?? const <InlineSpan>[]) {
          revisar(h, color);
        }
      }
    }

    revisar(rt.text, null);
  }
}

void main() {
  setUpAll(sqfliteFfiInit);

  group('editar: qué cambió y si va firmado', () {
    test('el lote vuelve al formulario sin cambios fantasma', () {
      final l = LoteNuevo.desdeProducto(lote(), estadios: estadios, unidades: unidades);
      expect(l.nombre, 'PL12 corrida 7');
      expect(l.cantidad, '1500', reason: 'sin .0');
      expect(l.precio, '2.5');
      expect(l.supervivencia, '85.5');
      expect(l.estadio!['code'], 'PL12', reason: 'la etapa sale del catálogo, con su código');
      expect(l.unidad!['code'], 'millar');
      expect(l.fechaEntrega, DateTime(2026, 11, 2));
      expect(cambiosLote(l.aCuerpoApi(), l.aCuerpoApi()), isEmpty);
      expect(l.errores('laboratorio'), isEmpty);
    });

    test('solo va lo que cambió; un opcional que se borra va en null', () {
      final l = LoteNuevo.desdeProducto(lote(), estadios: estadios, unidades: unidades);
      final antes = l.aCuerpoApi();
      l
        ..precio = '2,80'
        ..sanidad = ''
        ..fechaEntrega = null;
      expect(cambiosLote(antes, l.aCuerpoApi()), {'price': 2.8, 'health_status': null, 'expected_delivery_date': null});
    });

    test('precio, cantidad o unidad de un lote publicado van firmados; el resto y los borradores, no', () {
      expect(edicionVaFirmada(lote(estado: 'published'), {'price': 3.0}), isTrue);
      expect(edicionVaFirmada(lote(estado: 'published'), {'initial_qty': 10.0}), isTrue);
      expect(edicionVaFirmada(lote(estado: 'sold'), {'uom': 'u-lb'}), isTrue);
      expect(edicionVaFirmada(lote(estado: 'published'), {'health_status': 'ok', 'location': 'Guayas'}), isFalse);
      expect(edicionVaFirmada(lote(), {'price': 3.0}), isFalse, reason: 'borrador');
      expect(comercialesCambiados({'price': 1, 'name': 'x', 'uom': 'u'}), ['price', 'uom']);
    });

    test('un lote antiguo conserva su etapa aunque el perfil ya no la publique', () {
      final p = {...lote(), 'stage': {'id': 'st-juv', 'name': 'Juvenil'}};
      final l = LoteNuevo.desdeProducto(p, estadios: estadios, unidades: unidades);
      expect(l.errores('laboratorio').containsKey('estadio'), isFalse);
      l.estadio = estadios.firstWhere((e) => e['code'] == 'ENGORDE');
      expect(l.errores('laboratorio')['estadio'], 'Tu perfil no publica camarón.');
    });

    test('campos bloqueados por compras y su motivo', () {
      final p = lote(bloqueados: ['price', 'initial_qty', 'stage'], motivo: 'tiene compras en curso');
      expect(camposBloqueados(p), {'price', 'initial_qty', 'stage'});
      expect(motivoBloqueo(p), 'tiene compras en curso');
      expect(camposBloqueados(lote()), isEmpty);
      expect(campoDelFormulario('initial_qty'), 'cantidad');
      expect(campoDelFormulario('species'), 'especie');
    });

    test('rutas firmadas: :update-terms y :unarchive sí; fotos, certificados, PATCH y DELETE no', () {
      expect(esRutaSensible('POST', '/products/lote-1:update-terms'), isTrue);
      expect(esRutaSensible('POST', '/api/v1/products/lote-1:unarchive'), isTrue);
      expect(esRutaSensible('POST', '/products/lote-1/photos'), isFalse);
      expect(esRutaSensible('POST', '/products/lote-1/photos:reorder'), isFalse);
      expect(esRutaSensible('POST', '/products/lote-1/certificates'), isFalse);
      expect(esRutaSensible('PATCH', '/products/lote-1'), isFalse);
      expect(esRutaSensible('DELETE', '/products/lote-1'), isFalse);
    });
  });

  group('fotos y certificados: funciones puras', () {
    test('mover una foto (la primera es la portada)', () {
      expect(moverFoto(['a', 'b', 'c'], 2, 0), ['c', 'a', 'b']);
      expect(moverFoto(['a', 'b', 'c'], 0, 2), ['b', 'c', 'a']);
      expect(moverFoto(['a', 'b', 'c'], 1, 9), ['a', 'c', 'b']);
    });

    test('cuerpo de la foto y tipo real del archivo', () {
      final c = cuerpoFotoLote(nombreArchivo: 'x.jpg', jpeg: Uint8List.fromList([1, 2, 3]), portada: true);
      expect(c, {'filename': 'x.jpg', 'mime': 'image/jpeg', 'data': 'AQID', 'cover': true});
      expect(cuerpoFotoLote(nombreArchivo: 'x.jpg', jpeg: Uint8List(0)).containsKey('cover'), isFalse);
      expect(tipoDeArchivo(pdf), 'application/pdf');
      expect(tipoDeArchivo(png), 'image/png');
      expect(tipoDeArchivo(Uint8List.fromList([0xFF, 0xD8, 0xFF, 0xE0])), 'image/jpeg');
      expect(tipoDeArchivo(Uint8List.fromList(utf8.encode('<html>'))), isNull);
    });

    test('certificado propio: tipo, archivo y vencimiento como el portal', () {
      final c = CertificadoNuevo();
      expect(c.errores().keys, containsAll(['tipo', 'archivo']));
      c
        ..tipo = {'id': 'c-pcr', 'name': 'PCR', 'expires': true}
        ..archivo = Uint8List.fromList(utf8.encode('<html>'));
      expect(c.errores(), {'vence': 'Este certificado exige fecha de vencimiento.', 'archivo': 'Solo PDF, JPG o PNG.'});
      c
        ..archivo = pdf
        ..nombreArchivo = 'pcr.pdf'
        ..emision = DateTime(2026, 10, 1)
        ..vence = DateTime(2026, 9, 1);
      expect(c.errores().keys, ['vence']);
      c
        ..vence = DateTime(2027, 10, 1)
        ..numero = ' A-7 ';
      expect(c.errores(), isEmpty);
      expect(c.aCuerpoApi(), {
        'certificate': 'c-pcr',
        'number': 'A-7',
        'issue_date': '2026-10-01',
        'expiry_date': '2027-10-01',
        'filename': 'pcr.pdf',
        'data': base64.encode(pdf),
      });
    });
  });

  group('firmados: cambio comercial y reactivar', () {
    late ServidorFirmas servidor;
    late BaseLocal bd;
    late Sesion sesion;
    late Sincronizador sinc;
    final ahora = DateTime.utc(2026, 10, 19, 12);

    setUp(() async {
      servidor = ServidorFirmas()
        ..ahora = ahora
        ..respuesta = (req) => {'id': 'lote-1', 'state': 'published', 'active': true};
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

    test(':update-terms firma exactamente los cambios y el servidor la verifica', () async {
      final op = await sinc.firmarEdicionLote('lote-1', {'price': 2.8, 'initial_qty': 1200.0});
      expect(op.tipo, TipoOperacion.editarLote);
      expect(op.firma, isNotNull);
      await sinc.enviarFirmadaAhora(op);
      final a = servidor.aceptadas.single;
      expect(a.req.url.path, '/api/v1/products/lote-1:update-terms');
      expect(a.req.body, '{"price":2.8,"initial_qty":1200.0}');
      expect(sinc.cola, isEmpty, reason: 'no pasa por la cola');
    });

    test('reactivar va firmado; sin señal, el reintento es la misma operación', () async {
      final op = await sinc.firmarReactivacionLote('lote-1');
      expect(op.ruta, '/products/lote-1:unarchive');
      servidor.enLinea = false;
      await expectLater(sinc.enviarFirmadaAhora(op), throwsA(isA<SinConexion>()));
      servidor.enLinea = true;
      await sinc.enviarFirmadaAhora(op);
      await sinc.enviarFirmadaAhora(op);
      expect(servidor.aceptadas.map((a) => a.req.headers['X-Signature']).toSet(), hasLength(1));
    });
  });

  group('sin firma: editar el borrador, fotos, certificados y eliminar', () {
    late ServidorGestion servidor;
    late SesionFija sesion;
    late Sincronizador sinc;

    setUp(() {
      servidor = ServidorGestion();
      sesion = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      sinc = Sincronizador(sesion: sesion, bd: baseEnMemoria());
    });
    tearDown(() => sinc.dispose());

    test('cada ruta, con el perfil y sin cabeceras de firma', () async {
      await sinc.editarLote('lote-1', {'price': 2.8}, idempotencia: 'k-1');
      final patch = servidor.de('PATCH', '/products/lote-1').single;
      expect(jsonDecode(patch.body), {'price': 2.8});
      expect(patch.headers['Idempotency-Key'], 'k-1');
      final fotos = await sinc.subirFotoLote('lote-1', Uint8List.fromList([1, 2, 3]), nombreArchivo: 'x.jpg', portada: true, idempotencia: 'k-2');
      expect(fotos.last['id'], 'f-n2');
      expect(jsonDecode(servidor.de('POST', '/products/lote-1/photos').single.body),
          {'filename': 'x.jpg', 'mime': 'image/jpeg', 'data': 'AQID', 'cover': true});
      expect((await sinc.ordenarFotosLote('lote-1', ['f-b', 'f-a', 'f-n2'], idempotencia: 'k-3')).first['id'], 'f-b');
      expect((await sinc.quitarFotoLote('lote-1', 'f-a')).map((f) => f['id']), ['f-b', 'f-n2']);
      expect(await sinc.bajarFotoLote('lote-1', 'f-b'), png);
      expect(servidor.de('GET', '/products/lote-1/photos/f-b').single.url.queryParameters, {'thumb': '1'});
      await sinc.ligarCertificadoLote('lote-1', 'u-2', idempotencia: 'k-4');
      await sinc.quitarCertificadoLote('lote-1', 'l-1');
      await sinc.eliminarBorradorLote('lote-1');
      for (final r in servidor.recibidas) {
        expect(r.headers.containsKey('X-Signature'), isFalse, reason: '${r.method} ${r.url.path}');
        if (r.method != 'GET') expect(r.headers['X-Shrimp-Role'], 'laboratorio', reason: '${r.method} ${r.url.path}');
      }
      expect(servidor.de('DELETE', '/products/lote-1'), hasLength(1));
    });

    test('modo supervisor: ve las fotos pero no cambia nada', () async {
      sesion.supervisor = true;
      expect(await sinc.fotosLote('lote-1'), hasLength(2));
      await expectLater(sinc.editarLote('lote-1', {'price': 1}, idempotencia: 'k'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.subirFotoLote('lote-1', Uint8List(1), nombreArchivo: 'x', idempotencia: 'k'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.quitarCertificadoLote('lote-1', 'l-1'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.firmarReactivacionLote('lote-1'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.eliminarBorradorLote('lote-1'), throwsA(isA<ErrorSoloLectura>()));
    });
  });

  group('pantallas', () {
    setUp(() {});

    testWidgets('editar un borrador: llega lleno y manda solo el precio, sin huella', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorGestion();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, PantallaPublicarLote(producto: lote())));
      await t.pump();
      expect(find.text('Editar lote'), findsOneWidget);
      expect(find.text('PL12 corrida 7'), findsOneWidget);
      expect(find.text('1500'), findsOneWidget);
      expect(find.textContaining('fotos y los certificados'), findsNothing, reason: 'eso es del alta');
      await t.ensureVisible(find.text('Guardar cambios'));
      await t.tap(find.text('Guardar cambios'));
      await t.pump();
      expect(find.text('No cambiaste nada.'), findsWidgets);
      await t.enterText(find.widgetWithText(TextFormField, 'Precio por millar *'), '2,80');
      await t.ensureVisible(find.text('Guardar cambios'));
      await t.tap(find.text('Guardar cambios'));
      await esperar(t);
      expect(find.text('Confirmar el cambio'), findsNothing, reason: 'un borrador no pide huella');
      expect(jsonDecode(servidor.de('PATCH', '/products/lote-1').single.body), {'price': 2.8});
    });

    testWidgets('editar un lote publicado: el precio se confirma y va por :update-terms; la sanidad, por PATCH', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorGestion()..producto = lote(estado: 'published');
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, PantallaPublicarLote(producto: lote(estado: 'published'))));
      await t.pump();
      expect(find.textContaining('se confirman con tu huella'), findsOneWidget);
      await t.enterText(find.widgetWithText(TextFormField, 'Precio por millar *'), '3');
      await t.ensureVisible(find.text('Guardar cambios'));
      await t.tap(find.text('Guardar cambios'));
      await t.pumpAndSettle();
      expect(find.text('Confirmar el cambio'), findsOneWidget);
      expect(find.textContaining(r'Precio: $ 2.50 por millar → $ 3.00 por millar'), findsOneWidget);
      await t.tap(find.text('Confirmar'));
      await esperar(t);
      final terms = servidor.de('POST', '/products/lote-1:update-terms').single;
      expect(terms.body, '{"price":3.0}');
      expect(servidor.de('PATCH', '/products/lote-1'), isEmpty);

      // Solo la sanidad: PATCH sin confirmar.
      await t.runAsync(() => abrir(t, s, PantallaPublicarLote(producto: lote(estado: 'published'))));
      await t.pump();
      await t.enterText(find.widgetWithText(TextFormField, 'Estado sanitario / observaciones (opcional)'), 'Revisado');
      await t.ensureVisible(find.text('Guardar cambios'));
      await t.tap(find.text('Guardar cambios'));
      await esperar(t);
      expect(find.text('Confirmar el cambio'), findsNothing);
      expect(jsonDecode(servidor.de('PATCH', '/products/lote-1').single.body), {'health_status': 'Revisado'});
    });

    testWidgets('con compras: precio y cantidad bloqueados, y un 422 se muestra en su campo', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorGestion()
        ..forzar = (_) => respuestaJson(422, {
              ...problema(422, 'validation-error', 'No puedes cambiar esto: el lote tiene compras en curso.'),
              'errors': [
                {'field': 'survival_rate', 'message': 'No se puede cambiar ahora.'}
              ],
            });
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      final p = lote(estado: 'published', bloqueados: ['price', 'initial_qty', 'uom', 'stage'], motivo: 'tiene compras en curso');
      await t.runAsync(() => abrir(t, s, PantallaPublicarLote(producto: p)));
      await t.pump();
      expect(find.textContaining('Precio y datos del lote bloqueados: el lote tiene compras en curso.'), findsOneWidget);
      final precio = t.widget<TextField>(find.descendant(of: find.widgetWithText(TextFormField, 'Precio por millar *'), matching: find.byType(TextField)));
      expect(precio.enabled, isFalse);
      final qty = t.widget<TextField>(find.descendant(of: find.widgetWithText(TextFormField, 'Cantidad disponible en millares *'), matching: find.byType(TextField)));
      expect(qty.enabled, isFalse);
      await t.enterText(find.widgetWithText(TextFormField, 'Supervivencia'), '80');
      await t.ensureVisible(find.text('Guardar cambios'));
      await t.tap(find.text('Guardar cambios'));
      await esperar(t);
      expect(find.text('No se puede cambiar ahora.'), findsOneWidget, reason: 'el 422 en su campo');
      expect(find.textContaining('No se guardó.'), findsOneWidget);
    });

    testWidgets('fotos: portada, hacer portada, subir desde la galería y quitar', (t) async {
      final servidor = ServidorGestion();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(
          t,
          s,
          PantallaFotosLote(
            productoId: 'lote-1',
            nombre: 'PL12 corrida 7',
            elegir: (origen, maximo) async => [png],
            comprimir: (b) async => b,
          )));
      await esperar(t);
      expect(find.text('Portada'), findsOneWidget);
      expect(find.text('Foto 2'), findsOneWidget);
      expect(find.textContaining('(2 de 20)'), findsOneWidget);
      await t.tap(find.text('Hacer portada'));
      await esperar(t);
      expect(jsonDecode(servidor.de('POST', '/products/lote-1/photos:reorder').single.body), {'order': ['f-b', 'f-a']});
      await t.tap(find.text('Galería'));
      await esperar(t);
      expect(servidor.de('POST', '/products/lote-1/photos'), hasLength(1));
      expect(find.text('Foto 3'), findsOneWidget);
      await t.tap(find.byTooltip('Quitar la foto').first);
      await t.pumpAndSettle();
      await t.tap(find.widgetWithText(FilledButton, 'Quitar'));
      await esperar(t);
      expect(servidor.de('DELETE', '/products/lote-1/photos/f-b'), hasLength(1));
      expect(find.text('Foto 3'), findsNothing);
    });

    testWidgets('certificados: agregar uno de la cuenta y subir uno propio (con validación)', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2000));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorGestion();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(
          t,
          s,
          PantallaCertificadosLote(
            productoId: 'lote-1',
            elegirPdf: () async => (nombre: 'pcr.pdf', bytes: pdf),
          )));
      await esperar(t);
      expect(find.text('PCR WSSV'), findsOneWidget);
      expect(find.text('Aprobado'), findsOneWidget);
      expect(find.text('BAP'), findsOneWidget);
      await t.tap(find.text('Agregar'));
      await esperar(t);
      expect(jsonDecode(servidor.de('POST', '/products/lote-1/certificates').single.body), {'account_certificate': 'u-2'});
      await t.tap(find.text('Subir certificado'));
      await t.pumpAndSettle();
      await t.tap(find.text('Subir'));
      await t.pump();
      await t.pump(const Duration(milliseconds: 800));
      expect(find.text('Revisa los campos marcados en rojo.'), findsWidgets);
      expect(find.text('Elige el certificado.'), findsOneWidget);
      expect(find.text('Adjunta el archivo del certificado (PDF o foto).'), findsOneWidget);
      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('Otro').last);
      await t.pumpAndSettle();
      await t.tap(find.text('PDF'));
      await t.pumpAndSettle();
      expect(find.textContaining('pcr.pdf'), findsOneWidget);
      await t.tap(find.text('Subir'));
      await esperar(t);
      final post = servidor.de('POST', '/products/lote-1/certificates').last;
      expect(jsonDecode(post.body), {'certificate': 'c-otro', 'filename': 'pcr.pdf', 'data': base64.encode(pdf)});
      await t.pumpAndSettle();
      expect(find.textContaining('queda en revisión hasta que la plataforma lo apruebe'), findsOneWidget);
      expect(find.text('En revisión'), findsOneWidget);
    });

    testWidgets('detalle: un lote archivado se reactiva con la huella; un borrador se elimina', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorGestion()..producto = lote(activo: false);
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaLote(id: 'lote-1')));
      await esperar(t);
      expect(find.text('Archivado'), findsOneWidget);
      expect(find.textContaining('portal web'), findsNothing);
      await t.ensureVisible(find.text('Reactivar con mi huella'));
      await t.tap(find.text('Reactivar con mi huella'));
      await esperar(t);
      expect(servidor.de('POST', '/products/lote-1:unarchive'), hasLength(1));

      servidor.producto = lote();
      await t.runAsync(() => abrir(t, s, const PantallaLote(id: 'lote-1')));
      await esperar(t);
      expect(find.text('Editar'), findsOneWidget);
      expect(find.text('Fotos (2)'), findsOneWidget);
      expect(find.text('Certificados (0)'), findsOneWidget);
      await t.ensureVisible(find.text('Eliminar borrador'));
      await t.tap(find.text('Eliminar borrador'));
      await t.pump();
      await t.pump(const Duration(milliseconds: 500));
      await t.tap(find.widgetWithText(FilledButton, 'Eliminar'));
      await esperar(t);
      expect(servidor.de('DELETE', '/products/lote-1'), hasLength(1));
    });

    testWidgets('Mis lotes: lista de archivados', (t) async {
      final servidor = ServidorGestion()..producto = lote(activo: false);
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaMisLotes()));
      await esperar(t);
      await t.tap(find.text('Archivados'));
      await esperar(t);
      expect(servidor.recibidas.where((r) => r.url.path == '/api/v1/products' && r.url.queryParameters['state'] == 'archived'), isNotEmpty);
      expect(find.text('Archivado'), findsOneWidget);
    });

    testWidgets('en oscuro: editar, fotos y certificados se leen', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorGestion();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      final p = lote(estado: 'published', bloqueados: ['price'], motivo: 'tiene compras en curso');
      await t.runAsync(() => abrir(t, s, PantallaPublicarLote(producto: p), oscuro: true));
      await t.pump();
      sinTextoOscuro(t, 'editar');
      await t.runAsync(() => abrir(t, s, const PantallaFotosLote(productoId: 'lote-1'), oscuro: true));
      await esperar(t);
      sinTextoOscuro(t, 'fotos');
      await t.runAsync(() => abrir(t, s, const PantallaCertificadosLote(productoId: 'lote-1'), oscuro: true));
      await esperar(t);
      sinTextoOscuro(t, 'certificados');
      await t.runAsync(() => abrir(t, s, const PantallaLote(id: 'lote-1'), oscuro: true));
      await esperar(t);
      sinTextoOscuro(t, 'detalle');
    });
  });
}
