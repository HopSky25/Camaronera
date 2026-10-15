// C4 (CONTRATO-MEJORAS-APP.md): fotos de campo por la cola firmada.
// Compresión (lado mayor ≤ 1600, JPEG 75), cuerpo del contrato y firma ES256
// sobre los bytes exactos del cuerpo con la foto (servidor que verifica como
// shrimp_api), estados por foto y un rechazo que no se reintenta sin fin.
import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/foto_campo.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';
import 'package:verificador_campo/seguridad/firmador.dart';

import 'almacen_falso.dart';
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'servidor_firmas.dart';

Uint8List fotoDePrueba(int ancho, int alto) {
  final i = img.Image(width: ancho, height: alto);
  img.fill(i, color: img.ColorRgb8(20, 160, 176));
  img.fillRect(i, x1: 0, y1: 0, x2: ancho ~/ 2, y2: alto ~/ 2, color: img.ColorRgb8(18, 62, 92));
  return img.encodePng(i);
}

void main() {
  group('compresión', () {
    test('horizontal grande → lado mayor 1600, proporción intacta, JPEG', () {
      final jpeg = comprimirFoto(fotoDePrueba(3000, 2000));
      expect(jpeg.sublist(0, 2), [0xFF, 0xD8], reason: 'JPEG');
      final d = img.decodeJpg(jpeg)!;
      expect(d.width, 1600);
      expect(d.height, closeTo(1067, 1));
    });

    test('vertical grande → alto 1600', () {
      final d = img.decodeJpg(comprimirFoto(fotoDePrueba(1800, 2700)))!;
      expect(d.height, 1600);
      expect(d.width, closeTo(1067, 1));
    });

    test('pequeña: no se agranda', () {
      final d = img.decodeJpg(comprimirFoto(fotoDePrueba(800, 600)))!;
      expect((d.width, d.height), (800, 600));
    });

    test('calidad 75: pesa menos que la misma foto en calidad 100', () {
      final original = fotoDePrueba(1600, 1200);
      final i = img.decodeImage(original)!;
      expect(comprimirFoto(original).length, lessThan(img.encodeJpg(i, quality: 100).length));
      expect(calidadFoto, 75);
      expect(ladoMaximoFoto, 1600);
    });

    test('lo que no es imagen → FormatException', () {
      expect(() => comprimirFoto(Uint8List.fromList(utf8.encode('no soy foto'))), throwsFormatException);
    });
  });

  test('cuerpo: los campos del contrato, base64 y fecha UTC; lat/long/nota aunque sean nulos', () {
    final jpeg = comprimirFoto(fotoDePrueba(400, 300));
    final c = cuerpoFoto(nombreArchivo: 'f1.jpg', jpeg: jpeg, tomadaEn: DateTime.utc(2026, 10, 15, 13, 5, 9, 123));
    expect(c.keys.toSet(), {'filename', 'mime', 'data', 'taken_at', 'latitude', 'longitude', 'note'});
    expect(c['mime'], 'image/jpeg');
    expect(base64.decode(c['data'] as String), jpeg);
    expect(c['taken_at'], '2026-10-15T13:05:09Z');
    expect(c['latitude'], isNull);
    expect(c['note'], isNull);
    final con = cuerpoFoto(nombreArchivo: 'f', jpeg: jpeg, tomadaEn: DateTime.utc(2026), latitud: -2.19, longitud: -79.88, nota: '  piscina 4 ');
    expect((con['latitude'], con['longitude'], con['note']), (-2.19, -79.88, 'piscina 4'));
  });

  group('cola firmada', () {
    late ServidorFirmas servidor;
    late FirmadorFalso firmador;
    late BaseLocal bd;
    late Sesion sesion;
    late Sincronizador sinc;
    final ahora = DateTime.utc(2026, 10, 15, 12);
    const vid = 'v-1';

    setUpAll(sqfliteFfiInit);

    setUp(() async {
      servidor = ServidorFirmas()..ahora = ahora;
      firmador = FirmadorFalso();
      bd = BaseLocal(
          abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
              options: OpenDatabaseOptions(
                  version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));
      sesion = Sesion(credenciales: AlmacenFalso(), bd: bd, clienteHttp: servidor.cliente, firmador: firmador, reloj: () => ahora);
      sinc = Sincronizador(sesion: sesion, bd: bd);
      await sesion.iniciarSesion(url: 'demo.camaronmarket.com', correo: 'tecnico@verifica.ec', contrasena: 'x');
      await sesion.proteger();
      for (var i = 0; i < 3; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 10));
        await sinc.sincronizar();
      }
    });

    tearDown(() async {
      sinc.dispose();
      await (await bd.bd).close();
    });

    Future<FotoCampo> guardar() async {
      final id = await bd.guardarFoto(vid, '/fotos/$vid/1760533509000.jpg',
          tomadaEn: DateTime.utc(2026, 10, 15, 11, 45), latitud: -2.2, longitud: -79.9);
      return (await bd.fotos(vid)).singleWhere((f) => f.id == id);
    }

    Future<Uint8List> leer(String _) async => fotoDePrueba(3200, 2400);

    test('se firma el cuerpo con la foto y el servidor verifica la firma sobre esos bytes', () async {
      final f = await guardar();
      expect((await sinc.fotosDe(vid)).single.estado, EstadoFoto.sinConfirmar);
      final antes = firmador.confirmaciones;
      await sinc.subirFoto(f, leer: leer);
      expect(firmador.confirmaciones, antes + 1, reason: 'una huella por foto');
      await sinc.sincronizar();

      final aceptada = servidor.aceptadas.singleWhere((a) => a.req.url.path == '/api/v1/verifications/$vid/photos');
      final req = aceptada.req;
      expect(req.method, 'POST');
      expect(req.headers['X-Device-Id'], 'dev-1');
      expect(req.headers['Idempotency-Key'], isNotEmpty);
      // La cadena firmada lleva el sha256 de los bytes EXACTOS que viajaron.
      expect(aceptada.cadena.split('\n').last, sha256.convert(req.bodyBytes).toString());
      expect(aceptada.cadena.split('\n')[1], '/api/v1/verifications/$vid/photos');
      final cuerpo = jsonDecode(req.body) as Map<String, dynamic>;
      expect(cuerpo['filename'], '1760533509000.jpg');
      expect(cuerpo['taken_at'], '2026-10-15T11:45:00Z');
      expect((cuerpo['latitude'], cuerpo['longitude']), (-2.2, -79.9));
      final d = img.decodeJpg(base64.decode(cuerpo['data'] as String))!;
      expect(d.width, 1600, reason: 'comprimida antes de firmar');

      expect((await sinc.fotosDe(vid)).single.estado, EstadoFoto.subida);
      expect(sinc.cola, isEmpty);
    });

    test('sin señal queda pendiente (firmada) y sale al volver la señal', () async {
      final f = await guardar();
      servidor.enLinea = false;
      await sinc.subirFoto(f, leer: leer);
      await sinc.sincronizar();
      expect((await sinc.fotosDe(vid)).single.estado, EstadoFoto.pendiente);
      servidor.enLinea = true;
      await sinc.sincronizar();
      expect((await sinc.fotosDe(vid)).single.estado, EstadoFoto.subida);
    });

    test('si no se confirma con la huella, la foto sigue guardada sin confirmar', () async {
      final f = await guardar();
      firmador.fallarCon = 'cancelado';
      await expectLater(sinc.subirFoto(f, leer: leer), throwsA(isA<ErrorSeguridad>()));
      expect(sinc.cola, isEmpty);
      expect((await sinc.fotosDe(vid)).single.estado, EstadoFoto.sinConfirmar);
    });

    test('409 invalid-state (verificación cerrada): error claro y NO se reintenta en cada pasada', () async {
      servidor.forzar = (_) => respuestaJson(409, problema(409, 'invalid-state', 'La verificación no está en campo.'));
      final f = await guardar();
      await sinc.subirFoto(f, leer: leer);
      await sinc.sincronizar();
      final foto = (await sinc.fotosDe(vid)).single;
      expect(foto.estado, EstadoFoto.error);
      expect(foto.error, contains('ya no admite fotos'));
      final envios = servidor.a('/verifications/$vid/photos').length;
      await sinc.sincronizar();
      await sinc.sincronizar();
      expect(servidor.a('/verifications/$vid/photos').length, envios, reason: 'no se vuelve a subir sola');
      // Descartada en Pendientes: vuelve a "sin confirmar" (sigue en el teléfono).
      await sinc.descartarPendiente(sinc.cola.single.idLocal!);
      expect((await sinc.fotosDe(vid)).single.estado, EstadoFoto.sinConfirmar);
    });

    test('la ruta de fotos es sensible (firma obligatoria)', () {
      expect(esRutaSensible('POST', '/verifications/abc/photos'), isTrue);
      expect(esRutaSensible('GET', '/verifications/abc/photos'), isFalse);
    });
  });
}
