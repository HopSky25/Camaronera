import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import '../modelos/operacion_pendiente.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firma_operacion.dart';

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
  /// Una sola apertura compartida: Sesion y Sincronizador arrancan a la vez
  /// y sin esto abrirían la base dos veces.
  static Future<Database>? _abriendo;

  /// En pruebas: abre una base propia (sqflite_common_ffi) en vez de la del teléfono.
  final Future<Database> Function()? _abrirPropia;
  Future<Database>? _propia;

  BaseLocal({Future<Database> Function()? abrir}) : _abrirPropia = abrir;

  /// Versiones del esquema (onUpgrade migra desde la que tenga el teléfono):
  ///  1 verificaciones, cola, fotos, config
  ///  2 + cache_listas
  ///  3 + cola.rol (perfil con el que se hizo cada operación)
  ///  4 + cola.firma_timestamp, firma, firma_clave, dispositivo_id,
  ///      ultimo_error_tipo (operaciones firmadas con el dispositivo). Desde
  ///      la 4, cola.cuerpo es el cuerpo serializado EXACTO que se firmó y se
  ///      envía (antes ya se guardaba con jsonEncode, así que las filas
  ///      viejas valen tal cual; solo les falta la firma).
  static const version = 4;

  Future<Database> get bd {
    final propia = _abrirPropia;
    if (propia != null) return _propia ??= propia();
    return _abriendo ??= _abrir().catchError((Object e) {
      _abriendo = null; // la próxima llamada vuelve a intentar
      throw e;
    });
  }

  static Future<Database> _abrir() async {
    final dir = await getApplicationDocumentsDirectory();
    return openDatabase(
      p.join(dir.path, 'verificador_campo.db'),
      version: version,
      onCreate: crearEsquema,
      onUpgrade: migrar,
    );
  }

  static Future<void> crearEsquema(Database db, int _) async {
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
        ultimo_error TEXT,
        rol TEXT,
        firma_timestamp INTEGER,
        firma TEXT,
        firma_clave TEXT,
        dispositivo_id TEXT,
        ultimo_error_tipo TEXT
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
  }

  static Future<void> migrar(Database db, int desde, int _) async {
    if (desde < 2) await _crearCache(db);
    if (desde < 3) await _agregarColumnas(db, const {'rol': 'TEXT'});
    if (desde < 4) {
      await _agregarColumnas(db, const {
        'firma_timestamp': 'INTEGER',
        'firma': 'TEXT',
        'firma_clave': 'TEXT',
        'dispositivo_id': 'TEXT',
        'ultimo_error_tipo': 'TEXT',
      });
    }
  }

  static Future<void> _crearCache(Database db) => db.execute(
      'CREATE TABLE IF NOT EXISTS cache_listas (clave TEXT PRIMARY KEY, json TEXT NOT NULL, guardado_en TEXT NOT NULL)');

  static Future<void> _agregarColumnas(Database db, Map<String, String> columnas) async {
    final existentes = (await db.rawQuery('PRAGMA table_info(cola)')).map((c) => c['name']).toSet();
    for (final c in columnas.entries) {
      if (!existentes.contains(c.key)) await db.execute('ALTER TABLE cola ADD COLUMN ${c.key} ${c.value}');
    }
  }

  /// Decodifica el JSON guardado; nulo si está dañado (no tumba la lista).
  static Map<String, dynamic>? _json(Object? texto) {
    try {
      final v = jsonDecode(texto as String);
      return v is Map<String, dynamic> ? v : null;
    } catch (_) {
      return null;
    }
  }

  // ---------------- verificaciones ----------------

  Future<void> guardarVerificaciones(List<Map<String, dynamic>> lista) async {
    final db = await bd;
    final lote = db.batch();
    for (final v in lista) {
      if (!Verificacion.tieneId(v)) continue; // sin id no se puede guardar ni abrir
      // El detalle (con pesos, líneas...) no debe pisarse con la versión
      // resumida de la lista: se conserva el JSON más completo.
      final previo = await db.query('verificaciones', where: 'id = ?', whereArgs: [v['id']]);
      var json = v;
      if (previo.isNotEmpty) {
        final guardado = _json(previo.first['json']);
        if (guardado != null && guardado.containsKey('weights') && !v.containsKey('weights')) {
          json = {...guardado, ...v};
        }
      }
      lote.insert(
        'verificaciones',
        {
          'id': v['id'],
          'estado': jsonTexto(v['state']) ?? '',
          'json': jsonEncode(json),
          'actualizado_en': jsonTexto(v['updated_at']),
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
    return filas.map((f) => _json(f['json'])).whereType<Map<String, dynamic>>().map(Verificacion.desde).toList();
  }

  Future<Verificacion?> verificacion(String id) async {
    final db = await bd;
    final filas = await db.query('verificaciones', where: 'id = ?', whereArgs: [id]);
    final json = filas.isEmpty ? null : _json(filas.first['json']);
    return json == null ? null : Verificacion.desde(json);
  }

  /// Cambia el estado local de inmediato (p. ej. a "in_field" al iniciar campo
  /// sin internet) para que la bandeja lo refleje antes de sincronizar.
  Future<void> cambiarEstadoLocal(String id, String estado) async {
    final db = await bd;
    final filas = await db.query('verificaciones', where: 'id = ?', whereArgs: [id]);
    final json = filas.isEmpty ? null : _json(filas.first['json']);
    if (json == null) return;
    json['state'] = estado;
    await db.update('verificaciones', {'estado': estado, 'json': jsonEncode(json)}, where: 'id = ?', whereArgs: [id]);
  }

  Future<InformeCampo?> informeLocal(String id) async {
    final db = await bd;
    final filas = await db.query('verificaciones', columns: ['informe_local'], where: 'id = ?', whereArgs: [id]);
    final guardado = filas.isEmpty ? null : _json(filas.first['informe_local']);
    return guardado == null ? null : InformeCampo.desdeJsonLocal(guardado);
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
      'cuerpo': op.cuerpoSerializado, // tal cual: son los bytes firmados
      'clave_idempotencia': op.claveIdempotencia,
      'creada_en': op.creadaEn.toIso8601String(),
      'intentos': op.intentos,
      'ultimo_error': op.ultimoError,
      'ultimo_error_tipo': op.ultimoErrorTipo,
      'rol': op.rol,
      ..._columnasFirma(op.firma),
    });
  }

  static Map<String, Object?> _columnasFirma(FirmaOperacion? f) => {
        'firma_timestamp': f?.timestamp,
        'firma': f?.firma,
        'firma_clave': f?.huellaClave,
        'dispositivo_id': f?.dispositivoId,
      };

  static FirmaOperacion? _firmaDe(Map<String, Object?> f) {
    final ts = f['firma_timestamp'];
    final firma = f['firma'];
    final clave = f['firma_clave'];
    if (ts is! int || firma is! String || clave is! String) return null;
    return FirmaOperacion(timestamp: ts, firma: firma, huellaClave: clave, dispositivoId: f['dispositivo_id'] as String?);
  }

  /// Guarda una firma nueva (volver a firmar) y limpia el error anterior.
  Future<void> guardarFirma(int idLocal, FirmaOperacion firma) async {
    final db = await bd;
    await db.update('cola', {..._columnasFirma(firma), 'ultimo_error': null, 'ultimo_error_tipo': null},
        where: 'id = ?', whereArgs: [idLocal]);
  }

  /// La cola en el orden en que se hizo cada cosa. Una fila que no se puede
  /// leer (tipo de una versión más nueva, JSON dañado) se salta y se avisa en
  /// consola; no bloquea a las demás.
  Future<List<OperacionPendiente>> cola() async {
    final db = await bd;
    final filas = await db.query('cola', orderBy: 'id ASC');
    final ops = <OperacionPendiente>[];
    for (final f in filas) {
      try {
        ops.add(OperacionPendiente(
          idLocal: f['id'] as int,
          tipo: TipoOperacion.values.byName(f['tipo'] as String),
          recursoId: f['verificacion_id'] as String,
          despachoId: f['despacho_id'] as String?,
          cuerpoSerializado: f['cuerpo'] as String? ?? '{}',
          claveIdempotencia: f['clave_idempotencia'] as String,
          creadaEn: DateTime.tryParse(f['creada_en'] as String? ?? '') ?? DateTime.now(),
          intentos: (f['intentos'] as int?) ?? 0,
          ultimoError: f['ultimo_error'] as String?,
          ultimoErrorTipo: f['ultimo_error_tipo'] as String?,
          rol: f['rol'] as String?,
          firma: _firmaDe(f),
        ));
      } catch (e) {
        debugPrint('Operación ${f['id']} ilegible en la cola: $e');
      }
    }
    return ops;
  }

  Future<OperacionPendiente?> operacion(int idLocal) async =>
      (await cola()).where((o) => o.idLocal == idLocal).firstOrNull;

  /// Quita las operaciones de un tipo sobre un recurso que todavía no se
  /// enviaron (p. ej. un informe guardado dos veces: vale el último).
  Future<void> quitarDeColaPorTipo(String recursoId, TipoOperacion tipo, {int? excepto}) async {
    final db = await bd;
    await db.delete('cola',
        where: 'verificacion_id = ? AND tipo = ? AND id != ?', whereArgs: [recursoId, tipo.name, excepto ?? -1]);
  }

  Future<int> pendientes() async {
    final db = await bd;
    return Sqflite.firstIntValue(await db.rawQuery('SELECT COUNT(*) FROM cola')) ?? 0;
  }

  Future<void> quitarDeCola(int idLocal) async {
    final db = await bd;
    await db.delete('cola', where: 'id = ?', whereArgs: [idLocal]);
  }

  Future<void> anotarError(int idLocal, int intentos, String error, {String? tipo}) async {
    final db = await bd;
    await db.update('cola', {'intentos': intentos, 'ultimo_error': error, 'ultimo_error_tipo': tipo},
        where: 'id = ?', whereArgs: [idLocal]);
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

  /// Carpeta donde se guardan las fotos de campo (`fotos/<verificacion>/`).
  static Future<Directory> carpetaFotos() async =>
      Directory(p.join((await getApplicationDocumentsDirectory()).path, 'fotos'));

  /// Modo supervisor: al cambiar de socio (o al caducar su acceso) se borra
  /// lo que se bajó con la clave de "actuar como" (bandeja, caché de listas)
  /// sin tocar la configuración de la sesión del supervisor.
  Future<void> borrarDatosDeSocio() async {
    final db = await bd;
    await db.transaction((t) async {
      await t.delete('verificaciones');
      await t.delete('cola');
      await t.delete('fotos');
      await t.delete('cache_listas');
    });
  }

  /// Borra todo lo guardado en el teléfono (cerrar sesión): tablas y fotos,
  /// en una sola transacción para no dejar la base a medias.
  Future<void> borrarTodo() async {
    final db = await bd;
    await db.transaction((t) async {
      await t.delete('verificaciones');
      await t.delete('cola');
      await t.delete('fotos');
      await t.delete('cache_listas');
      await t.delete('config');
    });
    try {
      final carpeta = await carpetaFotos();
      if (await carpeta.exists()) await carpeta.delete(recursive: true);
    } catch (e) {
      debugPrint('No se pudieron borrar las fotos: $e');
    }
  }
}
