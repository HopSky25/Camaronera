// C6 (CONTRATO-MEJORAS-APP.md): enlaces directos. De la dirección (https en
// los dominios de CamaronMarket o camaronmarket://) a la pantalla; ruta
// desconocida → inicio; el destino espera al desbloqueo / login.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:verificador_campo/datos/repositorio.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/navegacion/destinos.dart';
import 'package:verificador_campo/navegacion/enrutador.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_avisos.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_despachos.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_informes.dart';
import 'package:verificador_campo/pantallas/comunes/pantalla_transacciones.dart';
import 'package:verificador_campo/pantallas/empacadora/pantalla_catalogo.dart';
import 'package:verificador_campo/pantallas/maquilador/pantalla_ordenes.dart';
import 'package:verificador_campo/pantallas/maquilador/pantalla_solicitudes.dart';
import 'package:verificador_campo/pantallas/pantalla_verificacion.dart';

import 'sesion_fija.dart';

const id = '6f1c2a3b-0d4e-4f5a-8b6c-7d8e9f0a1b2c';

void main() {
  group('destinoDeEnlace', () {
    final rutas = {
      '/marketplace/verifications/$id/acceptance': const Destino(TipoDestino.informePorAceptar, id),
      '/verifier/verifications/$id': const Destino(TipoDestino.verificacion, id),
      '/marketplace/purchases/$id': const Destino(TipoDestino.compra, id),
      '/marketplace/purchases/$id/traceability': const Destino(TipoDestino.compra, id),
      '/marketplace/sales/$id': const Destino(TipoDestino.venta, id),
      '/marketplace/dispatch/$id': const Destino(TipoDestino.despachoDeCompra, id),
      '/copacker/orders/$id': const Destino(TipoDestino.ordenEmpaque, id),
      '/marketplace/products/$id': const Destino(TipoDestino.lote, id),
    };

    test('las rutas del contrato en todos los dominios https', () {
      for (final host in dominiosEnlaces) {
        for (final e in rutas.entries) {
          expect(destinoDeEnlace(Uri.parse('https://$host${e.key}')), e.value, reason: '$host${e.key}');
        }
      }
      expect(dominiosEnlaces, containsAll(['camaronmarket.com', 'www.camaronmarket.com', 'demo.camaronmarket.com',
          'verificadores.camaronmarket.com', 'empaque.camaronmarket.com', 'verificadores.demo.camaronmarket.com', 'empaque.demo.camaronmarket.com']));
    });

    test('esquema propio con la misma ruta (camaronmarket:///ruta y camaronmarket://ruta)', () {
      for (final e in rutas.entries) {
        expect(destinoDeEnlace(Uri.parse('camaronmarket://${e.key}')), e.value);
        expect(destinoDeEnlace(Uri.parse('camaronmarket:/${e.key}')), e.value);
      }
    });

    test('con query y barra final también vale', () {
      expect(destinoDeEnlace(Uri.parse('https://demo.camaronmarket.com/marketplace/purchases/$id/?utm=x')),
          const Destino(TipoDestino.compra, id));
    });

    test('ruta desconocida, id que no es uuid, otro dominio o http → nulo (la app va al inicio)', () {
      for (final u in [
        'https://demo.camaronmarket.com/',
        'https://demo.camaronmarket.com/my/datos',
        'https://demo.camaronmarket.com/marketplace/purchases',
        'https://demo.camaronmarket.com/marketplace/purchases/123',
        'https://demo.camaronmarket.com/marketplace/purchases/$id/otra-cosa',
        'https://demo.camaronmarket.com/marketplace/verifications/$id',
        'https://evil.example.com/marketplace/purchases/$id',
        'http://demo.camaronmarket.com/marketplace/purchases/$id',
        'camaronmarket://algo/raro',
      ]) {
        expect(destinoDeEnlace(Uri.parse(u)), isNull, reason: u);
      }
    });
  });

  group('pantallaDe', () {
    test('cada destino abre su pantalla', () {
      expect(pantallaDe(const Destino(TipoDestino.informePorAceptar, id)), isA<PantallaInformePartes>());
      expect(pantallaDe(const Destino(TipoDestino.compra, id)), isA<PantallaTransaccion>());
      expect(pantallaDe(const Destino(TipoDestino.venta, id)), isA<PantallaTransaccion>());
      expect(pantallaDe(const Destino(TipoDestino.despachoDeCompra, id)), isA<PantallaTransaccion>());
      expect(pantallaDe(const Destino(TipoDestino.despacho, id)), isA<PantallaDespacho>());
      expect(pantallaDe(const Destino(TipoDestino.ordenEmpaque, id)), isA<PantallaOrden>());
      expect(pantallaDe(const Destino(TipoDestino.lote, id)), isA<PantallaLotePublico>());
      expect(pantallaDe(const Destino(TipoDestino.solicitudesEmpaque)), isA<PantallaSolicitudes>());
      expect(pantallaDe(const Destino(TipoDestino.avisos)), isA<PantallaAvisos>());
    });

    test('verificación: flujo de campo para técnico y verificadora; informe para las partes', () {
      const d = Destino(TipoDestino.verificacion, id);
      expect(pantallaDe(d, perfil: 'tecnico'), isA<PantallaVerificacion>());
      expect(pantallaDe(d, perfil: 'verificador'), isA<PantallaVerificacion>());
      expect(pantallaDe(d, perfil: 'empacadora'), isA<PantallaInformePartes>());
      expect((pantallaDe(d, perfil: 'camaronera') as PantallaInformePartes).id, id);
    });
  });

  group('Enrutador', () {
    late SesionFija sesion;
    late GlobalKey<NavigatorState> clave;
    late List<String?> abiertas;

    Future<void> montar(WidgetTester t) async {
      final cliente = MockClient((_) async => http.Response(jsonEncode({'id': id, 'name': 'Lote'}), 200));
      sesion = SesionFija(cliente);
      clave = GlobalKey<NavigatorState>();
      abiertas = [];
      await t.pumpWidget(MultiProvider(
        providers: [
          ChangeNotifierProvider<Sesion>.value(value: sesion),
          Provider(create: (_) => Repositorio(sesion: sesion, bd: null)),
        ],
        child: MaterialApp(
          navigatorKey: clave,
          navigatorObservers: [_Anotador(abiertas)],
          home: const Text('inicio'),
        ),
      ));
    }

    testWidgets('con la sesión lista abre la pantalla del enlace', (t) async {
      await montar(t);
      final e = Enrutador(sesion: sesion, navegador: clave);
      e.ir(destinoDeEnlace(Uri.parse('https://empaque.camaronmarket.com/marketplace/products/$id')));
      await t.pump(const Duration(milliseconds: 400));
      await t.pump();
      expect(abiertas, contains('enlace/lote'));
      e.dispose();
    });

    testWidgets('sin sesión (o bloqueada) espera; al entrar / desbloquear, abre', (t) async {
      await montar(t);
      sesion.abierta = false;
      final e = Enrutador(sesion: sesion, navegador: clave);
      e.ir(const Destino(TipoDestino.lote, id));
      await t.pump(const Duration(milliseconds: 400));
      expect(abiertas.where((n) => n == 'enlace/lote'), isEmpty);
      expect(e.pendiente, const Destino(TipoDestino.lote, id));
      sesion.abierta = true;
      sesion.avisar();
      await t.pump(const Duration(milliseconds: 400));
      await t.pump();
      expect(abiertas, contains('enlace/lote'));
      e.dispose();
    });

    testWidgets('ruta desconocida → inicio (cierra lo que hubiera encima)', (t) async {
      await montar(t);
      clave.currentState!.push(MaterialPageRoute(settings: const RouteSettings(name: 'otra'), builder: (_) => const Text('otra')));
      await t.pumpAndSettle();
      expect(find.text('otra'), findsOneWidget);
      final e = Enrutador(sesion: sesion, navegador: clave);
      e.ir(destinoDeEnlace(Uri.parse('https://demo.camaronmarket.com/no/existe')));
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.text('inicio'), findsOneWidget);
      expect(find.text('otra'), findsNothing);
      e.dispose();
    });
  });
}

class _Anotador extends NavigatorObserver {
  final List<String?> nombres;
  _Anotador(this.nombres);
  @override
  void didPush(Route<dynamic> route, Route<dynamic>? previousRoute) => nombres.add(route.settings.name);
}
