// Correcciones de la prueba en el emulador: el informe de campo con un campo
// mal escrito avisa y lleva al campo; el error se borra al corregirlo; la
// pantalla "Actualiza la app" deja cerrar sesión.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/control_version.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/pantallas/inicio/pantalla_actualizar.dart';
import 'package:verificador_campo/pantallas/pantalla_informe.dart';

import 'datos_api.dart';
import 'sesion_fija.dart';

void main() {
  setUpAll(sqfliteFfiInit);

  testWidgets('informe: con un conteo en cero avisa, baja al campo y el error se va al corregirlo', (t) async {
    final sesion = SesionFija(MockClient((_) async => http.Response('{"data":[]}', 200)), perfilFijo: 'tecnico');
    late Sincronizador sinc;
    await t.runAsync(() async {
      final bd = BaseLocal(
          abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
              options: OpenDatabaseOptions(
                  version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));
      await bd.guardarVerificaciones([verificacionLista()..['state'] = 'in_field']);
      sinc = Sincronizador(sesion: sesion, bd: bd);
      await sinc.recargarLocal();
    });
    await t.pumpWidget(MultiProvider(
      providers: [ChangeNotifierProvider<Sesion>.value(value: sesion), ChangeNotifierProvider.value(value: sinc)],
      child: MaterialApp(home: PantallaInforme(id: verificacionLista()['id'] as String)),
    ));
    await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
    await t.pump();

    final lista = find.byType(Scrollable).first;
    await t.scrollUntilVisible(find.text('Agregar conteo'), 200, scrollable: lista);
    await t.tap(find.text('Agregar conteo'));
    await t.pump();
    expect(find.text('Cam./lb'), findsOneWidget, reason: 'etiqueta corta, sin "Camaron…"');

    await t.scrollUntilVisible(find.text('Guardar y enviar cuando haya internet'), 300, scrollable: lista);
    await t.drag(lista, const Offset(0, -300));
    await t.pumpAndSettle();
    await t.tap(find.text('Guardar y enviar cuando haya internet'));
    await t.pump();
    await t.pump(const Duration(milliseconds: 100));
    expect(find.text('Revisa los campos marcados en rojo.'), findsOneWidget);
    await t.pumpAndSettle();
    expect(find.text('Más de 0'), findsWidgets);
    // El primer campo con error quedó a la vista.
    expect(find.text('Más de 0').hitTestable(), findsWidgets);

    // Corregir el valor borra el error (autovalidateMode onUserInteraction).
    final conteo = find.widgetWithText(TextFormField, 'Cam./lb');
    await t.enterText(conteo, '68');
    await t.pump();
    expect(find.descendant(of: conteo, matching: find.text('Más de 0')), findsNothing);
  });

  testWidgets('"Actualiza la app" deja cerrar sesión', (t) async {
    final sesion = SesionFija(MockClient((_) async => http.Response('{}', 200)));
    final cv = ControlVersion(servidor: () => '', version: '1.0.0')
      ..alRecibir426(const ErrorApi(426, 'x', '', 'app-update-required'));
    await t.pumpWidget(MultiProvider(
      providers: [ChangeNotifierProvider<Sesion>.value(value: sesion), ChangeNotifierProvider.value(value: cv)],
      child: const MaterialApp(home: PantallaActualizar()),
    ));
    expect(find.text('Cerrar sesión'), findsOneWidget);
  });
}
