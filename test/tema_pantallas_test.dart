// Pantallas clave y componentes en claro y en oscuro: se montan sin
// excepciones y, en oscuro, ningún texto queda oscuro sobre el fondo oscuro.
// El tema se cambia desde la pantalla de entrar y desde el menú de la cuenta.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:provider/single_child_widget.dart';
import 'package:verificador_campo/datos/avisos.dart';
import 'package:verificador_campo/datos/control_legal.dart';
import 'package:verificador_campo/datos/control_version.dart';
import 'package:verificador_campo/datos/preferencia_tema.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/perfil.dart';
import 'package:verificador_campo/modelos/verificacion.dart';
import 'package:verificador_campo/pantallas/comunes/menu_cuenta.dart';
import 'package:verificador_campo/pantallas/comunes/widgets.dart';
import 'package:verificador_campo/pantallas/inicio/pantalla_actualizar.dart';
import 'package:verificador_campo/pantallas/inicio/pantalla_documentos_legales.dart';
import 'package:verificador_campo/pantallas/inicio/pantalla_elegir_perfil.dart';
import 'package:verificador_campo/pantallas/inicio/pantalla_inicio_sesion.dart';
import 'package:verificador_campo/pantallas/pantalla_verificacion.dart';
import 'package:verificador_campo/pantallas/seguridad/pantalla_desbloqueo.dart';
import 'package:verificador_campo/pantallas/supervisor/franja_supervisor.dart';
import 'package:verificador_campo/tema.dart';

import 'avisos_test.dart' show AlmacenEstadoFalso, NotificadorFalso;
import 'datos_api.dart';
import 'sesion_fija.dart';
import 'sesion_test.dart' show BaseFalsa;

/// Sesión fija con un `yo` (perfiles para elegir).
class SesionConYo extends SesionFija {
  final Yo yoFijo;
  SesionConYo(super.http, this.yoFijo, {super.supervisor, super.perfilFijo});
  @override
  Yo? get yo => yoFijo;
}

MockClient _sinDatos() => MockClient((_) async => http.Response('{"data":[]}', 200));

/// La app con los dos temas y el modo de la preferencia, como en main.dart.
Widget _app(Widget home, PreferenciaTema pref, List<SingleChildWidget> otros) => MultiProvider(
      providers: [ChangeNotifierProvider<PreferenciaTema>.value(value: pref), ...otros],
      child: Builder(
        builder: (context) => MaterialApp(
          theme: temaClaro(),
          darkTheme: temaOscuro(),
          themeMode: context.watch<PreferenciaTema>().themeMode,
          home: home,
        ),
      ),
    );

Future<PreferenciaTema> _preferencia(ModoTema modo) async {
  final pref = PreferenciaTema(bd: BaseFalsa());
  await pref.elegir(modo);
  return pref;
}

/// En oscuro, todo texto pintado tiene que ser claro (si no, queda oscuro
/// sobre la superficie oscura).
void _sinTextoOscuro(WidgetTester t, String pantalla) {
  for (final rt in t.widgetList<RichText>(find.byType(RichText))) {
    void revisar(InlineSpan s, Color? heredado) {
      final color = s.style?.color ?? heredado;
      if (s is TextSpan && (s.text?.trim().isNotEmpty ?? false) && color != null) {
        expect(color.computeLuminance(), greaterThan(0.18),
            reason: '$pantalla: «${s.text}» en $color no se lee sobre el fondo oscuro');
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

Brightness _brillo(WidgetTester t, Finder f) => Theme.of(t.element(f)).brightness;

void main() {
  for (final modo in [ModoTema.claro, ModoTema.oscuro]) {
    final oscuro = modo == ModoTema.oscuro;
    final nombre = oscuro ? 'oscuro' : 'claro';

    Future<void> comprobar(WidgetTester t, String pantalla, Finder algo) async {
      expect(t.takeException(), isNull);
      expect(algo, findsWidgets);
      expect(_brillo(t, algo.first), oscuro ? Brightness.dark : Brightness.light);
      if (oscuro) _sinTextoOscuro(t, pantalla);
    }

    group('en $nombre', () {
      testWidgets('entrar', (t) async {
        final pref = await _preferencia(modo);
        final sesion = SesionFija(_sinDatos())..cerrar();
        await t.pumpWidget(_app(const PantallaInicioSesion(), pref, [ChangeNotifierProvider<Sesion>.value(value: sesion)]));
        await t.pump();
        await comprobar(t, 'entrar', find.text('Entra con tu cuenta'));
      });

      testWidgets('desbloqueo', (t) async {
        final pref = await _preferencia(modo);
        final sesion = SesionFija(_sinDatos());
        await t.pumpWidget(_app(const PantallaDesbloqueo(), pref, [
          ChangeNotifierProvider<Sesion>.value(value: sesion),
          ChangeNotifierProvider.value(value: Sincronizador(sesion: sesion, bd: BaseFalsa())),
        ]));
        await t.pumpAndSettle();
        await comprobar(t, 'desbloqueo', find.text('CamaronMarket está bloqueada'));
      });

      testWidgets('«Actualiza la app» y el aviso de versión nueva', (t) async {
        final pref = await _preferencia(modo);
        final cv = ControlVersion(servidor: () => '', version: '1.0.0');
        await t.pumpWidget(_app(const Scaffold(body: Stack(children: [PantallaActualizar(), AvisoVersionNueva()])), pref, [
          ChangeNotifierProvider<Sesion>.value(value: SesionFija(_sinDatos())),
          ChangeNotifierProvider.value(value: cv),
        ]));
        await t.pump();
        await comprobar(t, 'actualizar', find.text('Actualiza la app'));
      });

      testWidgets('«Acepta los documentos actualizados»', (t) async {
        final pref = await _preferencia(modo);
        final cl = ControlLegal(consultarMe: () => null, esSupervisor: () => false)
          ..aplicarMe({
            'legal': {
              'acceptance_required': true,
              'pending_documents': ['Política de privacidad', 'Términos de uso'],
              'accept_url': 'https://demo.camaronmarket.com/my/legal',
            }
          });
        await t.pumpWidget(_app(const PantallaDocumentosLegales(), pref, [
          ChangeNotifierProvider<Sesion>.value(value: SesionFija(_sinDatos())),
          ChangeNotifierProvider.value(value: cl),
        ]));
        await t.pump();
        await comprobar(t, 'documentos legales', find.text('Política de privacidad'));
      });

      testWidgets('elegir perfil', (t) async {
        final pref = await _preferencia(modo);
        final sesion = SesionConYo(_sinDatos(), Yo.desde(meCamaronera()), perfilFijo: null);
        await t.pumpWidget(_app(const PantallaElegirPerfil(), pref, [ChangeNotifierProvider<Sesion>.value(value: sesion)]));
        await t.pump();
        await comprobar(t, 'elegir perfil', find.text('Pendiente de aprobación'));
        // El logo en oscuro va sobre su placa clara, como en el portal.
        final placa = find.ancestor(
            of: find.byType(Image),
            matching: find.byWidgetPredicate(
                (w) => w is Container && (w.decoration as BoxDecoration?)?.color == ColoresMarca.placaLogo));
        expect(placa, oscuro ? findsOneWidget : findsNothing);
      });

      testWidgets('verificación del técnico', (t) async {
        final pref = await _preferencia(modo);
        final sesion = SesionFija(_sinDatos(), perfilFijo: 'tecnico');
        final sinc = Sincronizador(sesion: sesion, bd: BaseFalsa())..bandeja = [Verificacion.desde(verificacionLista())];
        await t.pumpWidget(_app(PantallaVerificacion(id: verificacionLista()['id'] as String), pref, [
          ChangeNotifierProvider<Sesion>.value(value: sesion),
          ChangeNotifierProvider.value(value: sinc),
        ]));
        await t.pumpAndSettle();
        await comprobar(t, 'verificación', find.text('VER/2026/0042'));
      });

      testWidgets('componentes: barra, franjas, avisos, píldoras, tarjetas, botones y campos', (t) async {
        await t.binding.setSurfaceSize(const Size(420, 3200));
        addTearDown(() => t.binding.setSurfaceSize(null));
        final pref = await _preferencia(modo);
        final sesion = SesionFija(_sinDatos(), supervisor: true);
        final sinc = Sincronizador(sesion: sesion, bd: BaseFalsa());
        final avisos = ServicioAvisos(sesion: sesion, almacen: AlmacenEstadoFalso(), notificador: NotificadorFalso());
        await t.pumpWidget(_app(
          Scaffold(
            appBar: const BarraCuenta(),
            body: ListView(padding: const EdgeInsets.all(12), children: [
              const FranjaPerfil(),
              const FranjaSupervisor(socio: 'Camaronera Las Mareas', supervisor: 'Ana'),
              BarraEstado(sinc: sinc),
              const AvisoCache(),
              for (final tipo in TipoAviso.values) CajaAviso(tipo: tipo, icono: Icons.info_outline, texto: 'Aviso ${tipo.name}'),
              const AvisoWeb(),
              const AvisoSoloLectura(),
              const CajaError(texto: 'No se pudo cargar'),
              Wrap(spacing: 6, children: [
                for (final e in ['received', 'assigned', 'in_field', 'done', 'approved', 'rejected', 'cancelled'])
                  Insignia(estado: e, texto: e),
              ]),
              const RejillaResumen(tarjetas: [
                TarjetaResumen(icono: Icons.sell_outlined, valor: '3', titulo: 'Ventas', detalle: '1 por confirmar'),
                TarjetaResumen(icono: Icons.fact_check_outlined, valor: '0', titulo: 'Informes', detalle: 'Nada pendiente'),
              ]),
              FilaMenu(icono: Icons.inventory_2_outlined, titulo: 'Mis lotes', subtitulo: 'Publicados', onTap: () {}),
              const TarjetaRegistro(titulo: 'VER/2026/0042', subtitulo: 'Camarón 30/40', estado: 'approved', estadoTexto: 'Aprobada', datos: [
                Dato(icono: Icons.place_outlined, texto: 'Machala'),
              ]),
              const SeccionDatos(filas: [('Talla', '30/40'), ('Libras', '12,000')]),
              const Rotulo('Acciones'),
              FilledButton(onPressed: () {}, child: const Text('Primario')),
              OutlinedButton(onPressed: () {}, child: const Text('Fantasma')),
              Builder(builder: (c) => OutlinedButton(style: EstilosBoton.peligro(c), onPressed: () {}, child: const Text('Rechazar'))),
              const FilledButton(onPressed: null, child: Text('Deshabilitado')),
              TextButton(onPressed: () {}, child: const Text('Texto')),
              const TextField(decoration: InputDecoration(labelText: 'Correo', helperText: 'El de tu cuenta')),
              ChoiceChip(label: const Text('Elegido'), selected: true, onSelected: (_) {}),
              ChoiceChip(label: const Text('Sin elegir'), selected: false, onSelected: (_) {}),
              SegmentedButton<int>(
                segments: const [ButtonSegment(value: 1, label: Text('Uno')), ButtonSegment(value: 2, label: Text('Dos'))],
                selected: const {1},
                onSelectionChanged: (_) {},
              ),
              SwitchListTile(value: true, onChanged: (_) {}, title: const Text('Interruptor')),
              const ListTile(leading: Icon(Icons.person_outline), title: Text('Fila'), subtitle: Text('Detalle')),
            ]),
          ),
          pref,
          [
            ChangeNotifierProvider<Sesion>.value(value: sesion),
            ChangeNotifierProvider.value(value: sinc),
            ChangeNotifierProvider.value(value: avisos),
          ],
        ));
        await t.pump();
        await comprobar(t, 'componentes', find.text('Aviso peligro'));

        // Un diálogo y un aviso flotante también.
        final contexto = t.element(find.text('Primario'));
        ScaffoldMessenger.of(contexto).showSnackBar(const SnackBar(content: Text('Guardado en el teléfono')));
        showDialog<void>(
            context: contexto,
            builder: (_) => const AlertDialog(title: Text('Cerrar sesión'), content: Text('Se borrará la sesión.')));
        await t.pumpAndSettle();
        await comprobar(t, 'diálogo', find.text('Se borrará la sesión.'));
        avisos.dispose();
      });
    });
  }

  testWidgets('la comprobación de texto oscuro detecta un color fijo de antes', (t) async {
    await t.pumpWidget(MaterialApp(
        theme: temaOscuro(), home: const Scaffold(body: Text('Navy fijo', style: TextStyle(color: Color(0xFF123E5C))))));
    expect(() => _sinTextoOscuro(t, 'prueba'), throwsA(isA<TestFailure>()));
  });

  group('cambiar el tema', () {
    testWidgets('desde la pantalla de entrar (sin sesión): se aplica al momento y se guarda', (t) async {
      final bd = BaseFalsa();
      final pref = PreferenciaTema(bd: bd);
      final sesion = SesionFija(_sinDatos())..cerrar();
      await t.pumpWidget(_app(const PantallaInicioSesion(), pref, [ChangeNotifierProvider<Sesion>.value(value: sesion)]));
      await t.pump();
      expect(_brillo(t, find.text('Entra con tu cuenta')), Brightness.light, reason: 'por defecto, como el teléfono (claro en la prueba)');

      await t.tap(find.byTooltip('Tema: como el teléfono'));
      await t.pumpAndSettle();
      expect(find.text('Tema de la app'), findsOneWidget);
      await t.tap(find.text('Oscuro'));
      await t.pumpAndSettle();
      expect(pref.modo, ModoTema.oscuro);
      expect(bd.config[PreferenciaTema.clave], 'oscuro');
      await t.tap(find.text('Listo'));
      await t.pumpAndSettle();
      expect(_brillo(t, find.text('Entra con tu cuenta')), Brightness.dark);
      expect(find.byTooltip('Tema: oscuro'), findsOneWidget);
      _sinTextoOscuro(t, 'entrar');
    });

    testWidgets('desde el menú de la cuenta', (t) async {
      final bd = BaseFalsa();
      final pref = PreferenciaTema(bd: bd);
      final sesion = SesionFija(_sinDatos(), supervisor: true);
      final avisos = ServicioAvisos(sesion: sesion, almacen: AlmacenEstadoFalso(), notificador: NotificadorFalso());
      await t.pumpWidget(_app(const Scaffold(appBar: BarraCuenta(), body: Text('Panel')), pref, [
        ChangeNotifierProvider<Sesion>.value(value: sesion),
        ChangeNotifierProvider.value(value: Sincronizador(sesion: sesion, bd: BaseFalsa())),
        ChangeNotifierProvider.value(value: avisos),
      ]));
      await t.pump();
      await t.tap(find.byIcon(Icons.account_circle_outlined));
      await t.pumpAndSettle();
      expect(find.text('Tema'), findsOneWidget);
      expect(find.text('Como el teléfono'), findsOneWidget);
      await t.tap(find.text('Tema'));
      await t.pumpAndSettle();
      await t.tap(find.text('Claro'));
      await t.pumpAndSettle();
      expect(pref.modo, ModoTema.claro);
      expect(bd.config[PreferenciaTema.clave], 'claro');
      await t.tap(find.text('Oscuro'));
      await t.pumpAndSettle();
      await t.tap(find.text('Listo'));
      await t.pumpAndSettle();
      expect(_brillo(t, find.text('Panel')), Brightness.dark);
      expect(bd.config[PreferenciaTema.clave], 'oscuro');
      avisos.dispose();
    });
  });
}
