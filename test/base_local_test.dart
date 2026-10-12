// Migración de la cola en la base local (sqflite_common_ffi, sin teléfono):
// las filas de versiones anteriores conservan su cuerpo exacto y quedan sin
// firma; las nuevas guardan cuerpo serializado + firma + dispositivo.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';

/// Esquema de la cola tal como lo creaban las versiones 1 y 3.
Future<void> _esquemaViejo(Database db, {required bool conRol}) async {
  await db.execute('''
    CREATE TABLE verificaciones (id TEXT PRIMARY KEY, estado TEXT NOT NULL, json TEXT NOT NULL, informe_local TEXT,
      informe_modificado INTEGER NOT NULL DEFAULT 0, actualizado_en TEXT)''');
  await db.execute('''
    CREATE TABLE cola (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, verificacion_id TEXT NOT NULL,
      despacho_id TEXT, cuerpo TEXT NOT NULL, clave_idempotencia TEXT NOT NULL, creada_en TEXT NOT NULL,
      intentos INTEGER NOT NULL DEFAULT 0, ultimo_error TEXT${conRol ? ', rol TEXT' : ''})''');
  await db.execute('''
    CREATE TABLE fotos (id INTEGER PRIMARY KEY AUTOINCREMENT, verificacion_id TEXT NOT NULL, ruta TEXT NOT NULL,
      tomada_en TEXT NOT NULL, subida INTEGER NOT NULL DEFAULT 0)''');
  await db.execute('CREATE TABLE config (clave TEXT PRIMARY KEY, valor TEXT)');
  // La versión 2 añadió la caché de listas (la 3 ya la tenía).
  if (conRol) await db.execute('CREATE TABLE cache_listas (clave TEXT PRIMARY KEY, json TEXT NOT NULL, guardado_en TEXT NOT NULL)');
}

void main() {
  late Directory dir;
  final fabrica = databaseFactoryFfiNoIsolate;

  setUpAll(sqfliteFfiInit);
  setUp(() async => dir = await Directory.systemTemp.createTemp('cola_'));
  tearDown(() async {
    try {
      await dir.delete(recursive: true);
    } catch (_) {}
  });

  BaseLocal abrirV4(String ruta) => BaseLocal(
      abrir: () => fabrica.openDatabase(ruta,
          options: OpenDatabaseOptions(
              version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));

  const cuerpoViejo = '{"verdict":"approve_obs","notes":"Camarón con manchas, ñ"}';

  for (final (versionVieja, conRol) in const [(3, true), (1, false)]) {
    test('v$versionVieja → v${BaseLocal.version}: columnas nuevas y la operación vieja intacta, sin firma', () async {
      final ruta = p.join(dir.path, 'v$versionVieja.db');
      final vieja = await fabrica.openDatabase(ruta,
          options: OpenDatabaseOptions(version: versionVieja, onCreate: (db, _) => _esquemaViejo(db, conRol: conRol)));
      await vieja.insert('cola', {
        'tipo': 'veredicto',
        'verificacion_id': 'v-1',
        'cuerpo': cuerpoViejo,
        'clave_idempotencia': 'idem-viejo',
        'creada_en': '2026-10-01T08:00:00.000',
        if (conRol) 'rol': 'verificador',
      });
      await vieja.close();

      final bd = abrirV4(ruta);
      final columnas = (await (await bd.bd).rawQuery('PRAGMA table_info(cola)')).map((c) => c['name']).toSet();
      expect(columnas, containsAll(['rol', 'firma_timestamp', 'firma', 'firma_clave', 'dispositivo_id', 'ultimo_error_tipo']));
      expect((await (await bd.bd).rawQuery("SELECT name FROM sqlite_master WHERE name = 'cache_listas'")), isNotEmpty);

      final op = (await bd.cola()).single;
      expect(op.cuerpoSerializado, cuerpoViejo, reason: 'los bytes se conservan tal cual');
      expect(op.cuerpo['notes'], 'Camarón con manchas, ñ');
      expect(op.claveIdempotencia, 'idem-viejo');
      expect(op.rol, conRol ? 'verificador' : null);
      expect(op.firma, isNull, reason: 'una operación de la versión anterior no está firmada');
      expect(op.esSensible, isTrue);

      // Se confirma con la huella: la firma queda guardada y el error limpio.
      await bd.anotarError(op.idLocal!, 0, 'Falta confirmarla', tipo: 'firma-pendiente');
      expect((await bd.cola()).single.errorDeFirma, isTrue);
      await bd.guardarFirma(op.idLocal!, const FirmaOperacion(timestamp: 1791817200, firma: 'MEUC', huellaClave: 'h1', dispositivoId: 'dev-1'));
      final firmada = (await bd.cola()).single;
      expect(firmada.firma?.timestamp, 1791817200);
      expect(firmada.firma?.firma, 'MEUC');
      expect(firmada.firma?.huellaClave, 'h1');
      expect(firmada.firma?.dispositivoId, 'dev-1');
      expect(firmada.ultimoError, isNull);
      expect(firmada.ultimoErrorTipo, isNull);
      expect(firmada.cuerpoSerializado, cuerpoViejo);
      await (await bd.bd).close();
    });
  }

  test('esquema nuevo: encolar guarda el cuerpo serializado exacto y la firma', () async {
    final bd = abrirV4(p.join(dir.path, 'nueva.db'));
    // Espacios y orden de claves "raros": no se vuelve a serializar nada.
    const cuerpo = '{ "packed_lb": 1200.50,"boxes":60 }';
    final id = await bd.encolar(OperacionPendiente(
      tipo: TipoOperacion.empaqueMaquila,
      recursoId: 'o-1',
      cuerpoSerializado: cuerpo,
      claveIdempotencia: 'k-1',
      creadaEn: DateTime(2026, 10, 12),
      rol: 'maquilador',
      firma: const FirmaOperacion(timestamp: 42, firma: 'sig', huellaClave: 'h', dispositivoId: null),
    ));
    final op = (await bd.cola()).single;
    expect(op.idLocal, id);
    expect(op.cuerpoSerializado, cuerpo);
    expect(op.firma?.timestamp, 42);
    expect(op.firma?.dispositivoId, isNull, reason: 'firmada con el registro pendiente');
    await bd.anotarError(id, 1, 'caducó', tipo: 'signature-expired');
    expect((await bd.cola()).single.ultimoErrorTipo, 'signature-expired');
    await bd.quitarDeColaPorTipo('o-1', TipoOperacion.empaqueMaquila, excepto: id);
    expect(await bd.cola(), hasLength(1), reason: 'excepto: no borra la recién guardada');
    await bd.quitarDeColaPorTipo('o-1', TipoOperacion.empaqueMaquila);
    expect(await bd.cola(), isEmpty);
    await (await bd.bd).close();
  });
}
