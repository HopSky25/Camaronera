// Lo que faltaba del portal para el vendedor: instalaciones y piscinas o
// tanques (crear, editar, archivar, foto), «mis certificados» de la cuenta
// (subir, renovar, quitar, revisión) y corregir un certificado propio del
// lote. Sin firma, con las reglas de Mi cuenta del portal y los 422 en su
// campo.
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
import 'package:verificador_campo/datos/cuenta_vendedor.dart';
import 'package:verificador_campo/datos/lotes_vendedor.dart';
import 'package:verificador_campo/datos/repositorio.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/gestion_lote.dart';
import 'package:verificador_campo/modelos/instalaciones.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_certificados_lote.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_instalaciones.dart';
import 'package:verificador_campo/pantallas/vendedor/pantalla_mis_certificados.dart';
import 'package:verificador_campo/tema.dart';

import 'compra_directa_test.dart' show baseEnMemoria;
import 'datos_api.dart';
import 'gestion_lote_test.dart' show pdf, sinTextoOscuro;
import 'servidor_firmas.dart';
import 'sesion_fija.dart';

class ServidorCuenta {
  final recibidas = <http.Request>[];
  http.Response Function(http.Request)? forzar;
  List<Map<String, dynamic>> instalaciones = [
    {'id': 'f-1', 'name': 'Finca Norte', 'facility_type': 'farm', 'province': 'Guayas', 'active': true, 'pond_count': 1},
  ];
  List<Map<String, dynamic>> piscinas = [
    {'id': 'p-1', 'name': 'Piscina 3', 'pond_type': 'earth', 'facility': {'id': 'f-1', 'name': 'Finca Norte'},
     'capacity_mode': 'dimensions', 'length_m': 100.0, 'width_m': 50.0, 'area_m2': 5000.0, 'volume_m3': 0.0, 'active': true, 'has_image': false},
  ];
  Map<String, dynamic> misCertificados = {
    'data': [
      {'id': 'u-1', 'certificate': {'id': 'c-bap', 'name': 'BAP'}, 'issuer': 'GSA', 'number': 'B-1', 'expiry_date': '2027-01-01',
       'status': 'approved', 'valid': true, 'products': 2},
      {'id': 'u-2', 'certificate': {'id': 'c-asc', 'name': 'ASC'}, 'issuer': 'ASC', 'status': 'pending', 'valid': true, 'products': 0},
    ],
    'catalog': [
      {'id': 'c-bap', 'name': 'BAP', 'expires': false},
      {'id': 'c-asc', 'name': 'ASC', 'expires': false},
    ],
  };
  Map<String, dynamic> certificadosLote = {
    'data': [
      {'id': 'l-1', 'certificate': {'id': 'c-pcr', 'name': 'PCR WSSV'}, 'number': 'A-1', 'issue_date': '2026-10-01',
       'expiry_date': '2027-01-01', 'status': 'approved', 'source': 'product'},
      {'id': 'l-2', 'certificate': {'id': 'c-bap', 'name': 'BAP'}, 'status': 'approved', 'source': 'account'},
    ],
    'available': [],
    'catalog': [
      {'id': 'c-pcr', 'name': 'PCR WSSV', 'expires': true}
    ],
  };

  late final MockClient cliente = MockClient((req) async {
    recibidas.add(req);
    final ruta = req.url.path.replaceFirst('/api/v1', '');
    final f = forzar;
    if (f != null && req.method != 'GET') return f(req);
    if (ruta == '/facilities' && req.method == 'GET') return respuestaJson(200, {'data': instalaciones, 'meta': {'next_cursor': null}});
    if (ruta == '/ponds' && req.method == 'GET') return respuestaJson(200, {'data': piscinas, 'meta': {'next_cursor': null}});
    if (ruta == '/facilities' && req.method == 'POST') {
      final c = jsonDecode(req.body) as Map<String, dynamic>;
      return respuestaJson(201, {...c, 'id': 'f-9', 'active': true});
    }
    if (ruta.startsWith('/facilities/')) {
      if (req.method == 'DELETE') return respuestaJson(200, {...instalaciones.first, 'active': false});
      return respuestaJson(200, {...instalaciones.first, ...jsonDecode(req.body) as Map<String, dynamic>});
    }
    if (ruta == '/ponds' && req.method == 'POST') return respuestaJson(201, {'id': 'p-9', 'name': 'x', 'active': true});
    if (ruta.startsWith('/ponds/')) {
      if (req.method == 'DELETE') return respuestaJson(200, {...piscinas.first, 'active': false});
      return respuestaJson(200, {...piscinas.first, 'active': true});
    }
    if (ruta == '/me/certificates') {
      if (req.method == 'POST') {
        final linea = {'id': 'u-3', 'certificate': {'id': 'c-asc', 'name': 'ASC'}, 'status': 'pending', 'valid': true, 'products': 0};
        misCertificados = {...misCertificados, 'data': [...misCertificados['data'] as List, linea], 'line': linea};
        return respuestaJson(201, misCertificados);
      }
      return respuestaJson(200, misCertificados);
    }
    if (ruta.startsWith('/me/certificates/')) return respuestaJson(200, misCertificados);
    if (ruta == '/products/lote-1/certificates') return respuestaJson(200, certificadosLote);
    if (ruta.startsWith('/products/lote-1/certificates/')) return respuestaJson(200, certificadosLote);
    return respuestaJson(404, problema(404, 'not-found', 'No existe: ${req.method} $ruta'));
  });

  Iterable<http.Request> de(String metodo, String ruta) => recibidas.where((r) => r.method == metodo && r.url.path == '/api/v1$ruta');
}

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
    child: MaterialApp(navigatorKey: navegador, theme: oscuro ? temaOscuro() : temaClaro(), home: const Scaffold(body: Text('inicio'))),
  ));
  unawaited(navegador.currentState!.push(MaterialPageRoute<Object?>(builder: (_) => pantalla)));
  await t.pump();
  await t.pump(const Duration(milliseconds: 400));
  await Future<void>.delayed(const Duration(milliseconds: 300));
}

Future<void> esperar(WidgetTester t) async {
  await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
  await t.pump();
  await t.pump(const Duration(milliseconds: 400));
}

void main() {
  setUpAll(sqfliteFfiInit);

  group('instalaciones: reglas del portal', () {
    test('vocabulario de cada perfil', () {
      expect(vocabularioDe('camaronera').piscinas, 'Piscinas');
      expect(vocabularioDe('camaronera').modo, 'dimensions');
      expect(vocabularioDe('laboratorio').instalaciones, 'Laboratorios');
      expect(vocabularioDe('laboratorio').piscina, 'tanque');
      expect(vocabularioDe('semillero').tipoInstalacion, 'hatchery');
    });

    test('instalación: nombre obligatorio y tipo de la lista; vacíos en null', () {
      final f = InstalacionForm()..tipo = 'castillo';
      expect(f.errores().keys, ['nombre', 'tipo']);
      f
        ..nombre = ' Finca Sur '
        ..tipo = 'farm'
        ..provincia = 'El Oro';
      expect(f.errores(), isEmpty);
      expect(f.aCuerpoApi(), {
        'name': 'Finca Sur', 'code': null, 'facility_type': 'farm', 'city': null, 'province': 'El Oro', 'address': null, 'notes': null,
      });
      final e = InstalacionForm.desde({'name': 'X', 'facility_type': 'laboratory', 'code': 'L1'});
      expect((e.nombre, e.tipo, e.codigo), ('X', 'laboratory', 'L1'));
    });

    test('piscina: límites del portal, área y foto', () {
      final p = PiscinaForm()
        ..nombre = 'P1'
        ..largo = '100'
        ..ancho = '50,5'
        ..profundidad = '80';
      expect(p.errores().keys, ['profundidad']);
      expect(p.errores()['profundidad'], 'Profundidad: el valor «80» no es válido.');
      p.profundidad = '1.5';
      expect(p.errores(), isEmpty);
      expect(p.area, 5050);
      expect((PiscinaForm()..nombre = 'x'..volumen = 'abc').errores().keys, ['volumen']);
      p.foto = Uint8List.fromList([1, 2, 3]);
      expect(p.conFoto({'name': 'P1'}), {'name': 'P1', 'image': 'AQID'});
      p
        ..foto = null
        ..quitarFoto = true;
      expect(p.conFoto({}), {'image': null});
      final d = PiscinaForm.desde({'name': 'T', 'pond_type': 'tank', 'capacity_mode': 'volume', 'manual_volume_m3': 12.5,
        'facility': {'id': 'f-1'}});
      expect((d.tipo, d.modo, d.volumen, d.instalacionId, d.largo), ('tank', 'volume', '12.5', 'f-1', ''));
    });

    test('corregir un certificado: el archivo es opcional y las fechas vacías van en null', () {
      final c = CertificadoNuevo.desde({'certificate': {'id': 'c-1', 'name': 'PCR'}, 'number': 'A-1', 'issue_date': '2026-10-01'},
          tipo: {'id': 'c-1', 'name': 'PCR', 'expires': true});
      expect(c.editando, isTrue);
      expect(c.errores(), {'vence': 'Este certificado exige fecha de vencimiento.'});
      c.vence = DateTime(2027, 1, 1);
      expect(c.errores(), isEmpty, reason: 'sin archivo nuevo se conserva el actual');
      c.numero = '';
      expect(c.aCuerpoEdicion(), {'number': null, 'issue_date': '2026-10-01', 'expiry_date': '2027-01-01'});
      c
        ..archivo = pdf
        ..nombreArchivo = 'n.pdf';
      expect(c.aCuerpoEdicion()['filename'], 'n.pdf');
    });
  });

  group('datos sin firma', () {
    late ServidorCuenta servidor;
    late SesionFija sesion;
    late Sincronizador sinc;

    setUp(() {
      servidor = ServidorCuenta();
      sesion = SesionFija(servidor.cliente, perfilFijo: 'camaronera');
      sinc = Sincronizador(sesion: sesion, bd: baseEnMemoria());
    });
    tearDown(() => sinc.dispose());

    test('cada ruta, con el perfil y sin firma', () async {
      expect(await sinc.instalaciones(), hasLength(1));
      await sinc.instalaciones(archivadas: true);
      expect(servidor.recibidas.last.url.queryParameters['include_archived'], 'true');
      await sinc.crearInstalacion({'name': 'F'}, idempotencia: 'k-1');
      await sinc.editarInstalacion('f-1', {'name': 'G'});
      await sinc.archivarInstalacion('f-1');
      await sinc.crearPiscina({'name': 'P'}, idempotencia: 'k-2');
      await sinc.editarPiscina('p-1', {'image': null});
      await sinc.archivarPiscina('p-1');
      await sinc.subirMiCertificado(CertificadoNuevo()..tipo = {'id': 'c-asc'}..archivo = pdf..nombreArchivo = 'a.pdf', idempotencia: 'k-3');
      await sinc.renovarMiCertificado('u-1', CertificadoNuevo(editando: true)..vence = DateTime(2028, 1, 1));
      await sinc.quitarMiCertificado('u-2');
      await sinc.editarCertificadoLote('lote-1', 'l-1', CertificadoNuevo(editando: true)..numero = 'A-2');
      expect(jsonDecode(servidor.de('PATCH', '/me/certificates/u-1').single.body), {'number': null, 'issue_date': null, 'expiry_date': '2028-01-01'});
      expect(jsonDecode(servidor.de('PATCH', '/products/lote-1/certificates/l-1').single.body)['number'], 'A-2');
      expect(servidor.de('DELETE', '/facilities/f-1'), hasLength(1));
      for (final r in servidor.recibidas.where((r) => r.method != 'GET')) {
        expect(r.headers.containsKey('X-Signature'), isFalse, reason: '${r.method} ${r.url.path}');
        expect(r.headers['X-Shrimp-Role'], 'camaronera');
      }
    });

    test('modo supervisor: lee pero no cambia nada', () async {
      sesion.supervisor = true;
      expect(await sinc.misCertificados(), contains('data'));
      await expectLater(sinc.crearInstalacion({'name': 'x'}, idempotencia: 'k'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.archivarPiscina('p-1'), throwsA(isA<ErrorSoloLectura>()));
      await expectLater(sinc.quitarMiCertificado('u-1'), throwsA(isA<ErrorSoloLectura>()));
    });
  });

  group('pantallas', () {
    testWidgets('instalaciones: lista con sus piscinas y crear una con validación', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 1800));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorCuenta();
      final s = SesionFija(servidor.cliente, perfilFijo: 'camaronera');
      await t.runAsync(() => abrir(t, s, const PantallaInstalaciones()));
      await esperar(t);
      expect(find.text('Fincas y piscinas'), findsOneWidget);
      expect(find.text('Finca Norte'), findsOneWidget);
      expect(find.text('1 piscinas'), findsOneWidget);
      await t.tap(find.text('Agregar finca'));
      await t.pumpAndSettle();
      await t.tap(find.text('Guardar'));
      await t.pump();
      await t.pump(const Duration(milliseconds: 800));
      expect(find.text('Revisa los campos marcados en rojo.'), findsWidgets);
      expect(find.text('El nombre es obligatorio.'), findsOneWidget);
      await t.enterText(find.widgetWithText(TextFormField, 'Nombre *'), 'Finca Sur');
      await t.tap(find.text('Guardar'));
      await esperar(t);
      expect(jsonDecode(servidor.de('POST', '/facilities').single.body)['facility_type'], 'farm', reason: 'el tipo natural del perfil');
    });

    testWidgets('piscina: un 422 del servidor en su campo; archivar desde el formulario', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2200));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorCuenta();
      final s = SesionFija(servidor.cliente, perfilFijo: 'camaronera');
      await t.runAsync(() => abrir(t, s, PantallaInstalacion(instalacion: servidor.instalaciones.first)));
      await esperar(t);
      expect(find.text('Piscina 3'), findsOneWidget);
      expect(find.textContaining('5,000 m²'), findsOneWidget);
      servidor.forzar = (_) => respuestaJson(422, {
            ...problema(422, 'validation-error', 'Ya tienes una piscina o tanque con el código «P1».'),
            'errors': [
              {'field': 'code', 'message': 'Ya tienes una piscina o tanque con el código «P1».'}
            ],
          });
      await t.tap(find.text('Agregar piscina'));
      await t.pumpAndSettle();
      await t.enterText(find.widgetWithText(TextFormField, 'Nombre *'), 'P2');
      await t.enterText(find.widgetWithText(TextFormField, 'Código (opcional)'), 'P1');
      await t.ensureVisible(find.text('Guardar'));
      await t.tap(find.text('Guardar'));
      await esperar(t);
      expect(find.text('Ya tienes una piscina o tanque con el código «P1».'), findsOneWidget);
      final body = jsonDecode(servidor.de('POST', '/ponds').single.body) as Map;
      expect((body['facility'], body['capacity_mode']), ('f-1', 'dimensions'));
    });

    testWidgets('mis certificados: revisión, subir un PDF y renovar', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2000));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorCuenta();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, PantallaMisCertificados(elegirPdf: () async => (nombre: 'asc.pdf', bytes: pdf))));
      await esperar(t);
      expect(find.text('Aprobado'), findsOneWidget);
      expect(find.text('En revisión'), findsOneWidget);
      expect(find.text('En 2 lotes'), findsOneWidget);
      await t.tap(find.text('Subir certificado'));
      await t.pumpAndSettle();
      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('ASC').last);
      await t.pumpAndSettle();
      await t.tap(find.text('PDF'));
      await t.pumpAndSettle();
      await t.tap(find.text('Subir'));
      await esperar(t);
      expect(jsonDecode(servidor.de('POST', '/me/certificates').single.body), {'certificate': 'c-asc', 'filename': 'asc.pdf', 'data': base64.encode(pdf)});
      await t.pumpAndSettle();
      await t.tap(find.byTooltip('Renovar o corregir').first);
      await t.pumpAndSettle();
      expect(find.text('Corregir certificado'), findsOneWidget);
      expect(find.text('B-1'), findsOneWidget, reason: 'llega con su número');
      await t.enterText(find.widgetWithText(TextFormField, 'Número del certificado (opcional)'), 'B-2');
      await t.tap(find.text('Guardar cambios'));
      await esperar(t);
      expect(jsonDecode(servidor.de('PATCH', '/me/certificates/u-1').single.body)['number'], 'B-2');
    });

    testWidgets('certificados del lote: se corrige el propio; el de la cuenta no', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2000));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorCuenta();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaCertificadosLote(productoId: 'lote-1')));
      await esperar(t);
      expect(find.byTooltip('Corregir el certificado'), findsOneWidget, reason: 'solo el propio del lote');
      expect(find.text('Mis certificados de la cuenta'), findsOneWidget);
      expect(find.textContaining('portal web'), findsNothing);
      await t.tap(find.byTooltip('Corregir el certificado'));
      await t.pumpAndSettle();
      await t.enterText(find.widgetWithText(TextFormField, 'Número del certificado (opcional)'), 'A-9');
      await t.tap(find.text('Guardar cambios'));
      await esperar(t);
      expect(jsonDecode(servidor.de('PATCH', '/products/lote-1/certificates/l-1').single.body),
          {'number': 'A-9', 'issue_date': '2026-10-01', 'expiry_date': '2027-01-01'});
    });

    testWidgets('en oscuro: instalaciones, piscina y mis certificados se leen', (t) async {
      await t.binding.setSurfaceSize(const Size(800, 2200));
      addTearDown(() => t.binding.setSurfaceSize(null));
      final servidor = ServidorCuenta();
      final s = SesionFija(servidor.cliente, perfilFijo: 'laboratorio');
      await t.runAsync(() => abrir(t, s, const PantallaInstalaciones(), oscuro: true));
      await esperar(t);
      sinTextoOscuro(t, 'instalaciones');
      await t.runAsync(() => abrir(t, s, PantallaFormPiscina(instalacionId: 'f-1', inicial: servidor.piscinas.first), oscuro: true));
      await esperar(t);
      sinTextoOscuro(t, 'piscina');
      await t.runAsync(() => abrir(t, s, const PantallaMisCertificados(), oscuro: true));
      await esperar(t);
      sinTextoOscuro(t, 'mis certificados');
    });
  });
}
