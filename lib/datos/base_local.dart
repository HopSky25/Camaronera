import 'dart:convert';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import '../modelos/operacion_pendiente.dart';
import '../modelos/verificacion.dart';

/// Lo que guarda una lista en caché: los datos y cuándo se bajaron.
class ListaGuardada {
  final List<Map<String, dynamic>> datos;
  final DateTime guardadoEn;
  const ListaGuardada(this.datos, this.guardadoEn);
}

/// Base de datos en el teléfono. Tablas:
///  - verificaciones: copia local de la bandeja del técnico / verificadora
///    (JSON del servidor) más el informe capturado en campo.
///  - cola: operaciones hechas en la app, en el orden en que se hicieron, que
///    se envían al servidor cuando hay señal (todos los perfiles).
///  - fotos: fotos tomadas en campo (archivo local; la API todavía no recibe
///    archivos, así que se suben luego desde el portal).
///  - cache_listas: última respuesta de cada lista de los perfiles en línea
///    (lotes, ventas, despachos...) para mostrarla sin señal.
///  - config: pares clave/valor (última sincronización, quién soy, perfil).
class BaseLocal {
  static Database? _bd;

  Future<Database> get bd async {
    if (_bd != null) return _bd!;
    final dir = await getApplicationDocumentsDirectory();
    _bd = await openDatabase(
      p.join(dir.path, 'verificador_campo.db'),
      version: 2,
      onCreate: (db, _) async {
        await db.execute('''
          CREATE TABLE verificaciones (
            id TEXT PRIMARY KEY,
            estado TEXT NOT NULL,
            json TEXT NOT NULL,
            informe_local TEXT,
            informe_modificado INTEGER NOT NULL DEFAULT 0,
            actualizado_en TEXT
          )''');
        // `verificacion_id` guarda el id del recurso principal de la
        // operación (verificación, despacho, solicitud u orden); conserva el
        // nombre de la versión 1 para no migrar datos en el teléfono.
        await db.execute('''
          CREATE TABLE cola (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            verificacion_id TEXT NOT NULL,
            despacho_id TEXT,
            cuerpo TEXT NOT NULL,
            clave_idempotencia TEXT NOT NULL,
            creada_en TEXT NOT NULL,
            intentos INTEGER NOT NULL DEFAULT 0,
            ultimo_error TEXT
          )''');
        await db.execute('''
          CREATE TABLE fotos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            verificacion_id TEXT NOT NULL,
            ruta TEXT NOT NULL,
            tomada_en TEXT NOT NULL,
            subida INTEGER NOT NULL DEFAULT 0
          )''');
        await db.execute('CREATE TABLE config (clave TEXT PRIMARY KEY, valor TEXT)');
        await _crearCache(db);
      },
      onUpgrade: (db, desde, _) async {
        if (desde < 2) await _crearCache(db);
      },
    );
    return _bd!;
  }

  static Future<void> _crearCache(Database db) =>
      db.execute('CREATE TABLE cache_listas (clave TEXT PRIMARY KEY, json TEXT NOT NULL, guardado_en TEXT NOT NULL)');

  // ---------------- verificaciones ----------------

  Future<void> guardarVerificaciones(List<Map<String, dynamic>> lista) async {
    final db = await bd;
    final lote = db.batch();
    for (final v in lista) {
      // El detalle (con pesos, líneas...) no debe pisarse con la versión
      // resumida de la lista: se conserva el JSON más completo.
      final previo = await db.query('verificaciones', where: 'id = ?', whereArgs: [v['id']]);
      var json = v;
      if (previo.isNotEmpty) {
        final guardado = jsonDecode(previo.first['json'] as String) as Map<String, dynamic>;
        if (guardado.containsKey('weights') && !v.containsKey('weights')) {
          json = {...guardado, ...v};
        }
      }
      lote.insert(
        'verificaciones',
        {
          'id': v['id'],
          'estado': v['state'] ?? '',
          'json': jsonEncode(json),
          'actualizado_en': v['updated_at'],
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      if (previo.isNotEmpty) {
        // replace borra la fila: se repone el informe local y su marca.
        lote.update(
          'verificaciones',
          {
            'informe_local': previo.first['informe_local'],
            'informe_modificado': previo.first['informe_modificado'],
          },
          where: 'id = ?',
          whereArgs: [v['id']],
        );
      }
    }
    await lote.commit(noResult: true);
  }

  /// Quita de la bandeja local las que el servidor ya no devuelve.
  Future<void> conservarSoloVerificaciones(Iterable<String> ids) async {
    final db = await bd;
    final actuales = await db.query('verificaciones', columns: ['id']);
    final quitar = actuales.map((f) => f['id'] as String).where((id) => !ids.contains(id)).toList();
    for (final id in quitar) {
      await db.delete('verificaciones', where: 'id = ?', whereArgs: [id]);
    }
  }

  Future<List<Verificacion>> verificaciones() async {
    final db = await bd;
    final filas = await db.query('verificaciones', orderBy: 'actualizado_en DESC');
    return filas.map((f) => Verificacion.desde(jsonDecode(f['json'] as String) as Map<String, dynamic>)).toList();
  }

  Future<Verificacion?> verificacion(String id) async {
    final db = await bd;
    final filas = await db.query('verificaciones', where: 'id = ?', whereArgs: [id]);
    if (filas.isEmpty) return null;
    return Verificacion.desde(jsonDecode(filas.first['json'] as String) as Map<String, dynamic>);
  }

  /// Cambia el estado local de inmediato (p. ej. a "in_field" al iniciar campo
  /// sin internet) para que la bandeja lo refleje antes de sincronizar.
  Future<void> cambiarEstadoLocal(String id, String estado) async {
    final db = await bd;
    final filas = await db.query('verificaciones', where: 'id = ?', whereArgs: [id]);
    if (filas.isEmpty) return;
    final json = jsonDecode(filas.first['json'] as String) as Map<String, dynamic>;
    json['state'] = estado;
    await db.update('verificaciones', {'estado': estado, 'json': jsonEncode(json)}, where: 'id = ?', whereArgs: [id]);
  }

  Future<InformeCampo?> informeLocal(String id) async {
    final db = await bd;
    final filas = await db.query('verificaciones', columns: ['informe_local'], where: 'id = ?', whereArgs: [id]);
    final texto = filas.isEmpty ? null : filas.first['informe_local'] as String?;
    if (texto == null) return null;
    return InformeCampo.desdeJsonLocal(jsonDecode(texto) as Map);
  }

  Future<void> guardarInformeLocal(String id, InformeCampo informe, {bool modificado = true}) async {
    final db = await bd;
    await db.update(
      'verificaciones',
      {'informe_local': jsonEncode(informe.aJsonLocal()), 'informe_modificado': modificado ? 1 : 0},
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  // ---------------- cola ----------------

  Future<int> encolar(OperacionPendiente op) async {
    final db = await bd;
    return db.insert('cola', {
      'tipo': op.tipo.name,
      'verificacion_id': op.recursoId,
      'despacho_id': op.despachoId,
      'cuerpo': jsonEncode(op.cuerpo),
      'clave_idempotencia': op.claveIdempotencia,
      'creada_en': op.creadaEn.toIso8601String(),
      'intentos': op.intentos,
      'ultimo_error': op.ultimoError,
    });
  }

  Future<List<OperacionPendiente>> cola() async {
    final db = await bd;
    final filas = await db.query('cola', orderBy: 'id ASC');
    return filas
        .map((f) => OperacionPendiente(
              idLocal: f['id'] as int,
              tipo: TipoOperacion.values.byName(f['tipo'] as String),
              recursoId: f['verificacion_id'] as String,
              despachoId: f['despacho_id'] as String?,
              cuerpo: jsonDecode(f['cuerpo'] as String) as Map<String, dynamic>,
              claveIdempotencia: f['clave_idempotencia'] as String,
              creadaEn: DateTime.parse(f['creada_en'] as String),
              intentos: f['intentos'] as int,
              ultimoError: f['ultimo_error'] as String?,
            ))
        .toList();
  }

  Future<OperacionPendiente?> operacion(int idLocal) async =>
      (await cola()).where((o) => o.idLocal == idLocal).firstOrNull;

  Future<int> pendientes() async {
    final db = await bd;
    return Sqflite.firstIntValue(await db.rawQuery('SELECT COUNT(*) FROM cola')) ?? 0;
  }

  Future<void> quitarDeCola(int idLocal) async {
    final db = await bd;
    await db.delete('cola', where: 'id = ?', whereArgs: [idLocal]);
  }

  Future<void> anotarError(int idLocal, int intentos, String error) async {
    final db = await bd;
    await db.update('cola', {'intentos': intentos, 'ultimo_error': error}, where: 'id = ?', whereArgs: [idLocal]);
  }

  // ---------------- fotos ----------------

  Future<void> guardarFoto(String verificacionId, String ruta) async {
    final db = await bd;
    await db.insert('fotos', {
      'verificacion_id': verificacionId,
      'ruta': ruta,
      'tomada_en': DateTime.now().toIso8601String(),
    });
  }

  Future<List<String>> fotos(String verificacionId) async {
    final db = await bd;
    final filas = await db.query('fotos', where: 'verificacion_id = ?', whereArgs: [verificacionId], orderBy: 'id ASC');
    return filas.map((f) => f['ruta'] as String).toList();
  }

  // ---------------- caché de listas (perfiles en línea) ----------------

  Future<void> guardarLista(String clave, List<Map<String, dynamic>> datos) async {
    final db = await bd;
    await db.insert(
      'cache_listas',
      {'clave': clave, 'json': jsonEncode(datos), 'guardado_en': DateTime.now().toUtc().toIso8601String()},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<ListaGuardada?> leerLista(String clave) async {
    final db = await bd;
    final filas = await db.query('cache_listas', where: 'clave = ?', whereArgs: [clave]);
    if (filas.isEmpty) return null;
    final datos = (jsonDecode(filas.first['json'] as String) as List).cast<Map<String, dynamic>>();
    return ListaGuardada(datos, DateTime.parse(filas.first['guardado_en'] as String).toLocal());
  }

  Future<void> borrarCache() async {
    final db = await bd;
    await db.delete('cache_listas');
  }

  // ---------------- config ----------------

  Future<String?> leerConfig(String clave) async {
    final db = await bd;
    final filas = await db.query('config', where: 'clave = ?', whereArgs: [clave]);
    return filas.isEmpty ? null : filas.first['valor'] as String?;
  }

  Future<void> escribirConfig(String clave, String? valor) async {
    final db = await bd;
    if (valor == null) {
      await db.delete('config', where: 'clave = ?', whereArgs: [clave]);
      return;
    }
    await db.insert('config', {'clave': clave, 'valor': valor}, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<void> borrarTodo() async {
    final db = await bd;
    await db.delete('verificaciones');
    await db.delete('cola');
    await db.delete('fotos');
    await db.delete('cache_listas');
    await db.delete('config');
  }
}
