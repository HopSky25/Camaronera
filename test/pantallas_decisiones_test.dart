// Pantallas de las decisiones de la verificación, en claro y en oscuro:
// el técnico de apoyo no ve el veredicto, la empresa acepta o declina la
// orden, el veredicto y el «producto no presentado» validan como el informe
// (SnackBar y el error a la vista), el equipo de apoyo y la segunda opinión
// del vendedor. En oscuro ningún texto queda oscuro sobre el fondo oscuro.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:provider/single_child_widget.dart';
import 'package:verificador_campo/datos/preferencia_tema.dart';
import 'package:verificador_campo/datos/repositorio.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/verificacion.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_informes.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_segunda_opinion.dart';
import 'package:verificador_campo/pantallas/pantalla_asignar_apoyo.dart';
import 'package:verificador_campo/pantallas/pantalla_veredicto.dart';
import 'package:verificador_campo/pantallas/pantalla_veredicto_no_presentado.dart';
import 'package:verificador_campo/pantallas/pantalla_verificacion.dart';
import 'package:verificador_campo/tema.dart';

import 'datos_api.dart';
import 'sesion_fija.dart';
import 'sesion_test.dart' show BaseFalsa;
import 'supervisor_test.dart' show BaseConfig;

const _id = '6f1c0000-0000-4000-8000-00000000000a';

/// Detalles fijos por ruta (sin red).
class RepoRutas extends Repositorio {
  final Map<String, Map<String, dynamic>> porRuta;
  RepoRutas(Sesion sesion, this.porRuta) : super(sesion: sesion, bd: BaseConfig());
  @override
  Future<ResultadoLista> detalle(String ruta, {Map<String, String>? params}) async => ResultadoLista([porRuta[ruta]!]);
}

MockClient _api() => MockClient((req) async {
      if (req.url.path.endsWith('/me/technicians')) {
        return http.Response(
            '{"data":[{"id":"t1","name":"Juan Pérez","active":true},{"id":"t2","name":"Rosa Apoyo","active":true},'
            '{"id":"t3","name":"Luis Nuevo","active":true}]}',
            200,
            headers: {'content-type': 'application/json; charset=utf-8'});
      }
      return http.Response('{"data":[]}', 200, headers: {'content-type': 'application/json'});
    });

Map<String, dynamic> _apoyo() => verificacionLista()
  ..['my_role'] = 'support_technician'
  ..['state'] = 'in_field'
  ..['support_technicians'] = [
    {'id': 't2', 'name': 'Rosa Apoyo'},
  ]
  ..['can_verdict'] = false;

Map<String, dynamic> _empresaPorAceptar() => verificacionLista()
  ..['my_role'] = 'verifier'
  ..['state'] = 'received'
  ..['technician'] = null
  ..['technician_assigned'] = false
  ..['verifier_response'] = 'pending'
  ..['verifier_response_deadline'] = '2026-10-02T14:00:00Z'
  ..['deadline_assign'] = '2026-10-03T14:00:00Z'
  ..['deadline_verdict'] = '2026-10-05T04:59:59Z'
  ..['can_respond'] = true
  ..['can_decline'] = true
  ..['can_verdict'] = true;

Map<String, dynamic> _responsableEnCampo() => verificacionLista()
  ..['state'] = 'in_field'
  ..['can_verdict'] = true
  ..['contest_hours'] = 24
  ..['photo_count'] = 0;

Map<String, dynamic> _vendedorRechazada() => verificacionDetalle()
  ..['my_role'] = 'seller'
  ..['state'] = 'rejected'
  ..['acceptance_state'] = 'na'
  ..['rejection_reason'] = 'field'
  ..['contest_state'] = 'open'
  ..['contest_deadline'] = '2026-10-02T13:00:00Z'
  ..['can_request_second_opinion'] = true
  ..['verdict_notes'] = 'Talla más chica que la publicada';

Map<String, dynamic> _opciones() => {
      'contest_state': 'open',
      'contest_deadline': '2026-10-02T13:00:00Z',
      'can_request': true,
      'problem': null,
      'second_opinion': null,
      'options': [
        {
          'verifier': {'id': 'ver-2', 'name': 'Otra Verificadora'},
          'fee': {'amount': 120.0, 'currency': 'USD'},
        },
      ],
    };

void main() {
  for (final modo in [ModoTema.claro, ModoTema.oscuro]) {
    final oscuro = modo == ModoTema.oscuro;

    group('en ${oscuro ? 'oscuro' : 'claro'}', () {
      late PreferenciaTema pref;
      late SesionFija sesion;
      late Sincronizador sinc;

      setUp(() async {
        pref = PreferenciaTema(bd: BaseFalsa());
        await pref.elegir(modo);
        sesion = SesionFija(_api(), perfilFijo: 'verificador');
        sinc = Sincronizador(sesion: sesion, bd: BaseFalsa());
      });

      Future<void> montar(WidgetTester t, Widget home, {List<Map<String, dynamic>> bandeja = const [], List<SingleChildWidget> otros = const []}) async {
        await t.binding.setSurfaceSize(const Size(420, 2400));
        addTearDown(() => t.binding.setSurfaceSize(null));
        sinc.bandeja = [for (final j in bandeja) Verificacion.desde(j)];
        await t.pumpWidget(MultiProvider(
          providers: [
            ChangeNotifierProvider<PreferenciaTema>.value(value: pref),
            ChangeNotifierProvider<Sesion>.value(value: sesion),
            ChangeNotifierProvider<Sincronizador>.value(value: sinc),
            ...otros,
          ],
          child: Builder(
            builder: (context) => MaterialApp(
              theme: temaClaro(),
              darkTheme: temaOscuro(),
              themeMode: context.watch<PreferenciaTema>().themeMode,
              home: home,
            ),
          ),
        ));
        await t.pumpAndSettle();
      }

      void sinTextoOscuro(WidgetTester t, String pantalla) {
        expect(t.takeException(), isNull);
        if (!oscuro) return;
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

      testWidgets('el técnico de apoyo carga datos pero no ve el veredicto', (t) async {
        await montar(t, const PantallaVerificacion(id: _id), bandeja: [_apoyo()]);
        expect(find.text('Vas de apoyo'), findsOneWidget);
        expect(find.text('Informe de campo'), findsOneWidget);
        expect(find.text('Fotos de campo'), findsOneWidget);
        expect(find.text('Dar veredicto'), findsNothing);
        expect(find.text('El producto no se presentó'), findsNothing);
        expect(find.textContaining('Vas de apoyo: puedes iniciar'), findsOneWidget);
        expect(find.textContaining('Rosa Apoyo'), findsOneWidget);
        sinTextoOscuro(t, 'apoyo');
      });

      testWidgets('la empresa acepta o declina la orden y ve sus plazos', (t) async {
        await montar(t, const PantallaVerificacion(id: _id), bandeja: [_empresaPorAceptar()]);
        expect(find.text('Por aceptar'), findsOneWidget);
        expect(find.text('Aceptar o declinar hasta'), findsOneWidget);
        expect(find.text('Asignar técnico hasta'), findsOneWidget);
        expect(find.text('Veredicto hasta'), findsOneWidget);
        expect(find.text('Aceptar la orden'), findsOneWidget);
        sinTextoOscuro(t, 'por aceptar');
        await t.ensureVisible(find.text('Declinar la orden'));
        await t.tap(find.text('Declinar la orden'));
        // El botón queda ocupado (girando) mientras el diálogo está abierto.
        await t.pump();
        await t.pump(const Duration(milliseconds: 400));
        await t.tap(find.widgetWithText(FilledButton, 'Declinar'));
        await t.pump();
        await t.pump(const Duration(milliseconds: 400));
        expect(find.text('Escribe el motivo'), findsOneWidget, reason: 'motivo obligatorio');
        sinTextoOscuro(t, 'declinar');
      });

      testWidgets('veredicto: el rechazo explica la segunda opinión y exige la conclusión', (t) async {
        await montar(t, const PantallaVeredicto(id: _id), bandeja: [_responsableEnCampo()]);
        expect(find.textContaining('segunda opinión en las próximas 24 horas'), findsOneWidget);
        expect(find.textContaining('la compra se cancela'), findsWidgets);
        expect(find.textContaining('No cumple; la compra se cancela'), findsNothing);
        await t.tap(find.text('Rechazar'));
        await t.pumpAndSettle();
        await t.tap(find.text('Firmar y emitir veredicto'));
        await t.pumpAndSettle();
        expect(find.text('Con observaciones o rechazo, escribe la conclusión.'), findsOneWidget);
        expect(find.text('Escribe por qué'), findsOneWidget);
        expect(find.text('¿El producto no se presentó en la planta?'), findsOneWidget);
        sinTextoOscuro(t, 'veredicto');
      });

      testWidgets('producto no presentado: hora, fotos y explicación obligatorias', (t) async {
        await montar(t, const PantallaNoPresentado(id: _id), bandeja: [verificacionLista()..['can_verdict'] = true]);
        expect(find.textContaining('El honorario lo paga el vendedor y recibe un strike'), findsOneWidget);
        sinTextoOscuro(t, 'no presentado');
        await t.ensureVisible(find.text('Firmar el rechazo'));
        await t.tap(find.text('Firmar el rechazo'));
        await t.pumpAndSettle();
        expect(find.text('Revisa los campos marcados en rojo.'), findsOneWidget);
        expect(find.textContaining('Toma al menos una foto'), findsOneWidget);
        expect(find.text('Explica qué encontraste al llegar'), findsOneWidget);
        sinTextoOscuro(t, 'no presentado con errores');
      });

      testWidgets('producto no presentado: con fotos ya subidas la foto nueva es opcional', (t) async {
        await montar(t, const PantallaNoPresentado(id: _id), bandeja: [verificacionLista()..['can_verdict'] = true..['photo_count'] = 2]);
        expect(find.textContaining('ya tiene 2 fotos'), findsOneWidget);
        await t.ensureVisible(find.text('Firmar el rechazo'));
        await t.tap(find.text('Firmar el rechazo'));
        await t.pumpAndSettle();
        expect(find.textContaining('Toma al menos una foto'), findsNothing);
        expect(find.text('Explica qué encontraste al llegar'), findsOneWidget);
      });

      testWidgets('equipo de campo: quitar al de apoyo y sumar a otro', (t) async {
        final j = _apoyo()..['my_role'] = 'verifier';
        await montar(t, const PantallaAsignarApoyo(id: _id), bandeja: [j]);
        expect(find.text('Juan Pérez'), findsOneWidget, reason: 'responsable');
        expect(find.text('Rosa Apoyo'), findsOneWidget);
        expect(find.text('Quitar'), findsOneWidget);
        expect(find.text('Luis Nuevo'), findsOneWidget, reason: 'disponible para sumar');
        expect(find.text('Sumar'), findsOneWidget, reason: 'ni el responsable ni el de apoyo se ofrecen otra vez');
        sinTextoOscuro(t, 'equipo de campo');
      });

      testWidgets('el vendedor ve el plazo y pide la segunda opinión', (t) async {
        final repo = RepoRutas(sesion, {
          '/verifications/v': _vendedorRechazada(),
          '/verifications/$_id/second-opinion': _opciones(),
        });
        await montar(t, const PantallaInformePartes(id: 'v'), otros: [Provider<Repositorio>.value(value: repo)]);
        expect(find.text('SEGUNDA OPINIÓN'), findsOneWidget);
        expect(find.textContaining('pide una segunda opinión hasta el'), findsOneWidget);
        sinTextoOscuro(t, 'informe del vendedor');
        await t.ensureVisible(find.text('Pedir segunda opinión'));
        await t.tap(find.text('Pedir segunda opinión'));
        await t.pumpAndSettle();
        expect(find.byType(PantallaSegundaOpinion), findsOneWidget);
        expect(find.text('Otra Verificadora'), findsOneWidget);
        sinTextoOscuro(t, 'segunda opinión');
        // Sin elegir: SnackBar y el error a la vista.
        final boton = find.descendant(of: find.byType(ListView), matching: find.text('Pedir segunda opinión'));
        await t.ensureVisible(boton);
        await t.tap(boton);
        await t.pumpAndSettle();
        expect(find.text('Elige la verificadora de la segunda opinión.'), findsOneWidget);
        expect(find.text('Elige una verificadora'), findsOneWidget);
      });

      testWidgets('el comprador ve el rechazo sin el botón del vendedor', (t) async {
        final j = _vendedorRechazada()
          ..['my_role'] = 'buyer'
          ..['can_request_second_opinion'] = false;
        await montar(t, const PantallaInformePartes(id: 'v'),
            otros: [Provider<Repositorio>.value(value: RepoRutas(sesion, {'/verifications/v': j}))]);
        expect(find.textContaining('El vendedor puede pedir una segunda opinión'), findsOneWidget);
        expect(find.text('Pedir segunda opinión'), findsNothing);
      });
    });
  }
}
