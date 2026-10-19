// Tema claro / oscuro con los tokens del portal y la preferencia de tema
// (Sistema / Claro / Oscuro) guardada en el teléfono.
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/preferencia_tema.dart';
import 'package:verificador_campo/tema.dart';

import 'sesion_test.dart' show BaseFalsa;

/// Contraste WCAG entre dos colores opacos.
double contraste(Color a, Color b) {
  final la = a.computeLuminance(), lb = b.computeLuminance();
  final (claro, oscuro) = la > lb ? (la, lb) : (lb, la);
  return (claro + 0.05) / (oscuro + 0.05);
}

void main() {
  group('tema', () {
    test('claro y oscuro llevan su paleta de marca y su brillo', () {
      final claro = temaClaro(), oscuro = temaOscuro();
      expect(claro.brightness, Brightness.light);
      expect(oscuro.brightness, Brightness.dark);
      expect(claro.extension<ColoresMarca>(), same(ColoresMarca.claro));
      expect(oscuro.extension<ColoresMarca>(), same(ColoresMarca.oscuro));
      // Tokens del portal: --shrimp-brand / --shrimp-bg y su versión oscura (--dk-*).
      expect(ColoresMarca.claro.marca, const Color(0xFF123E5C));
      expect(ColoresMarca.claro.fondo, const Color(0xFFF6F8FA));
      expect(ColoresMarca.oscuro.fondo, const Color(0xFF0C141B));
      expect(ColoresMarca.oscuro.superficie, const Color(0xFF16212B));
      expect(claro.scaffoldBackgroundColor, ColoresMarca.claro.fondo);
      expect(oscuro.scaffoldBackgroundColor, ColoresMarca.oscuro.fondo);
      expect(oscuro.colorScheme.surface, ColoresMarca.oscuro.superficie);
      expect(claro.textTheme.bodyMedium!.fontFamily, 'Inter');
    });

    for (final c in [ColoresMarca.claro, ColoresMarca.oscuro]) {
      final modo = c.esOscuro ? 'oscuro' : 'claro';

      test('$modo: el texto se lee sobre la superficie y el fondo', () {
        for (final fondo in [c.superficie, c.fondo]) {
          expect(contraste(c.tinta, fondo), greaterThan(7), reason: 'tinta');
          expect(contraste(c.tintaSuave, fondo), greaterThan(4.5), reason: 'tinta suave');
          expect(contraste(c.titulo, fondo), greaterThan(4.5), reason: 'títulos');
          expect(contraste(c.marcaTexto, fondo), greaterThan(4.5), reason: 'marca como texto');
          expect(contraste(c.peligroTexto, fondo), greaterThan(4.5), reason: 'peligro');
          expect(contraste(c.advertenciaTexto, fondo), greaterThan(4.5), reason: 'advertencia');
          // Como en el portal: tenue, teal y verde valen para texto grande o
          // iconos (3:1).
          for (final (nombre, color) in [('tenue', c.tintaTenue), ('acento', c.acentoTexto), ('éxito', c.exitoTexto)]) {
            expect(contraste(color, fondo), greaterThan(3), reason: nombre);
          }
        }
      });

      test('$modo: avisos, píldoras y botones legibles', () {
        for (final t in TipoAviso.values) {
          final e = c.aviso(t);
          expect(contraste(e.texto, e.fondo), greaterThan(4.5), reason: 'aviso $t');
        }
        for (final t in TipoPildora.values) {
          final (fondo, texto) = c.pildora(t);
          expect(contraste(texto, fondo), greaterThan(3), reason: 'píldora $t');
        }
        // Texto blanco sobre los botones rellenos.
        for (final (nombre, fondo) in [('marca', c.marca), ('peligro', c.peligro), ('éxito', c.exito)]) {
          expect(contraste(Colors.white, fondo), greaterThan(3.5), reason: 'botón $nombre');
        }
      });
    }

    test('la píldora sigue el estado del servidor', () {
      const c = ColoresMarca.claro;
      expect(c.insignia('approved'), c.pildora(TipoPildora.ok));
      expect(c.insignia('rejected'), c.pildora(TipoPildora.peligro));
      expect(c.insignia('in_field'), c.pildora(TipoPildora.advertencia));
      expect(c.insignia('assigned'), c.pildora(TipoPildora.info));
      expect(c.insignia('done'), c.pildora(TipoPildora.hecho));
      expect(c.insignia('cancelled'), c.pildora(TipoPildora.apagado));
      expect(c.insignia('algo-nuevo'), c.pildora(TipoPildora.neutro));
      // En oscuro el texto pasa a la variante clara (--dk-success-text).
      expect(ColoresMarca.oscuro.insignia('approved').$2, ColoresMarca.oscuro.exitoTexto);
    });

    test('lerp entre claro y oscuro (animación del cambio de tema)', () {
      final medio = ColoresMarca.claro.lerp(ColoresMarca.oscuro, 0.5);
      expect(medio.fondo, Color.lerp(ColoresMarca.claro.fondo, ColoresMarca.oscuro.fondo, 0.5));
      expect(ColoresMarca.claro.lerp(ColoresMarca.oscuro, 1).superficie, ColoresMarca.oscuro.superficie);
      expect(ColoresMarca.claro.lerp(ColoresMarca.oscuro, 1).brillo, Brightness.dark);
    });
  });

  group('preferencia de tema', () {
    test('por defecto sigue al sistema', () async {
      final pref = PreferenciaTema(bd: BaseFalsa());
      await pref.cargar();
      expect(pref.modo, ModoTema.sistema);
      expect(pref.themeMode, ThemeMode.system);
    });

    test('se guarda en el teléfono y se lee al volver a abrir la app', () async {
      final bd = BaseFalsa();
      final pref = PreferenciaTema(bd: bd);
      var avisos = 0;
      pref.addListener(() => avisos++);
      await pref.elegir(ModoTema.oscuro);
      expect(pref.themeMode, ThemeMode.dark);
      expect(avisos, 1);
      expect(bd.config[PreferenciaTema.clave], 'oscuro');

      final otra = PreferenciaTema(bd: bd);
      await otra.cargar();
      expect(otra.modo, ModoTema.oscuro);

      await otra.elegir(ModoTema.claro);
      expect(bd.config[PreferenciaTema.clave], 'claro');
      final tercera = PreferenciaTema(bd: bd);
      await tercera.cargar();
      expect(tercera.themeMode, ThemeMode.light);

      // Volver a «como el teléfono» quita la clave.
      await tercera.elegir(ModoTema.sistema);
      expect(bd.config.containsKey(PreferenciaTema.clave), isFalse);
    });

    test('un valor desconocido o una base que no abre se quedan en «sistema»', () async {
      final bd = BaseFalsa()..config[PreferenciaTema.clave] = 'morado';
      final pref = PreferenciaTema(bd: bd);
      await pref.cargar();
      expect(pref.modo, ModoTema.sistema);

      final rota = PreferenciaTema(bd: BaseLocal(abrir: () => Future.error(StateError('sin base'))));
      await rota.cargar();
      expect(rota.modo, ModoTema.sistema);
      await rota.elegir(ModoTema.oscuro); // no revienta; se aplica igual
      expect(rota.modo, ModoTema.oscuro);
    });

    group('en la base local de verdad', () {
      late Directory dir;
      setUpAll(sqfliteFfiInit);
      setUp(() async => dir = await Directory.systemTemp.createTemp('tema_'));
      tearDown(() async {
        try {
          await dir.delete(recursive: true);
        } catch (_) {}
      });

      BaseLocal abrir() => BaseLocal(
          abrir: () => databaseFactoryFfiNoIsolate.openDatabase(p.join(dir.path, 'app.db'),
              options: OpenDatabaseOptions(
                  version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));

      test('se guarda y sobrevive a cerrar sesión (el resto de la config se borra)', () async {
        final bd = abrir();
        await PreferenciaTema(bd: bd).elegir(ModoTema.oscuro);
        await bd.escribirConfig('perfil', 'camaronera');
        await bd.borrarTodo();
        expect(await bd.leerConfig('perfil'), isNull);
        expect(await bd.leerConfig(PreferenciaTema.clave), 'oscuro');
        final otraVez = abrir();
        final pref = PreferenciaTema(bd: otraVez);
        await pref.cargar();
        expect(pref.modo, ModoTema.oscuro);
        await (await bd.bd).close();
        await (await otraVez.bd).close();
      });
    });
  });
}
