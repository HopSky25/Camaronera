// Decisiones de la verificación (shrimp_api): rechazo por «Producto no
// presentado» con fotos dentro del veredicto, rechazo por el informe con
// segunda opinión, técnicos de apoyo que no firman, aceptar o declinar la
// orden y plazos visibles. Modelo, cuerpos, rutas firmadas y la cola
// firmada contra el servidor falso que verifica como shrimp_api.
import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/cuerpos.dart';
import 'package:verificador_campo/modelos/foto_campo.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/modelos/verificacion.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';

import 'almacen_falso.dart';
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'servidor_firmas.dart';

/// Foto con ruido (no se comprime casi nada): el peor caso para el cupo.
Uint8List fotoRuidosa(int ancho, int alto) {
  final i = img.Image(width: ancho, height: alto);
  var semilla = 7;
  for (final p in i) {
    semilla = (semilla * 1103515245 + 12345) & 0x7fffffff;
    p
      ..r = semilla & 0xff
      ..g = (semilla >> 8) & 0xff
      ..b = (semilla >> 16) & 0xff;
  }
  return img.encodePng(i);
}

Map<String, dynamic> verificacionApoyo() => verificacionLista()
  ..['my_role'] = 'support_technician'
  ..['state'] = 'in_field'
  ..['support_technicians'] = [
    {'id': 't2', 'name': 'Rosa Apoyo'},
  ]
  ..['can_verdict'] = false
  ..['contest_hours'] = 24;

void main() {
  group('modelo', () {
    test('técnico de apoyo: no firma el veredicto y ve el equipo', () {
      final v = Verificacion.desde(verificacionApoyo());
      expect(v.soyApoyo, isTrue);
      expect(v.soyResponsable, isFalse);
      expect(v.puedoDictaminar, isFalse);
      expect(v.tecnicosApoyo.single.nombre, 'Rosa Apoyo');
      expect(v.horasImpugnacion, 24);
    });

    test('sin can_verdict (lista) se deduce del rol', () {
      expect(Verificacion.desde(verificacionLista()).puedoDictaminar, isTrue, reason: 'responsable');
      expect(Verificacion.desde(verificacionLista()..['my_role'] = 'verifier').puedoDictaminar, isTrue);
      expect(Verificacion.desde(verificacionLista()..['my_role'] = 'support_technician').puedoDictaminar, isFalse);
    });

    test('la empresa que se asignó a sí misma sigue administrando', () {
      final v = Verificacion.desde(verificacionLista()..['can_manage'] = true);
      expect(v.soyResponsable, isTrue);
      expect(v.soyVerificadora, isTrue);
      expect(Verificacion.desde(verificacionLista()).soyVerificadora, isFalse);
    });

    test('«Informe listo» no está cerrada; con veredicto sí', () {
      expect(Verificacion.desde(verificacionLista()..['state'] = 'done').cerrada, isFalse);
      expect(Verificacion.desde(verificacionLista()..['state'] = 'rejected').cerrada, isTrue);
    });

    test('respuesta a la orden y plazos', () {
      final v = Verificacion.desde(verificacionLista()
        ..['my_role'] = 'verifier'
        ..['state'] = 'received'
        ..['verifier_response'] = 'pending'
        ..['verifier_response_deadline'] = '2026-10-02T14:00:00Z'
        ..['deadline_assign'] = '2026-10-03T14:00:00Z'
        ..['deadline_verdict'] = '2026-10-05T04:59:59Z'
        ..['deadline_overdue'] = 'response'
        ..['can_respond'] = true
        ..['can_decline'] = false);
      expect(v.respuestaOrden, 'pending');
      expect(v.puedoResponder, isTrue);
      expect(v.puedoDeclinar, isFalse);
      expect(v.plazoRespuesta, DateTime.parse('2026-10-02T14:00:00Z').toLocal());
      expect(v.plazoAsignar, DateTime.parse('2026-10-03T14:00:00Z').toLocal());
      expect(v.plazoVeredicto, DateTime.parse('2026-10-05T04:59:59Z').toLocal());
      expect(v.plazoVencidoEtiqueta, 'Venció el plazo para aceptar o declinar la orden');
      final d = Verificacion.desde(verificacionLista()
        ..['verifier_response'] = 'declined'
        ..['verifier_decline_reason'] = 'Sin capacidad');
      expect(d.declinada, isTrue);
      expect(d.motivoDeclinada, 'Sin capacidad');
    });

    test('rechazo por «producto no presentado» y por el informe con segunda opinión', () {
      final np = Verificacion.desde(verificacionLista()
        ..['state'] = 'rejected'
        ..['rejection_reason'] = 'not_presented'
        ..['not_presented_arrival'] = '2026-10-01T13:00:00Z');
      expect(np.rechazoNoPresentado, isTrue);
      expect(np.llegadaNoPresentado, DateTime.parse('2026-10-01T13:00:00Z').toLocal());
      final campo = Verificacion.desde(verificacionLista()
        ..['my_role'] = 'seller'
        ..['state'] = 'rejected'
        ..['rejection_reason'] = 'field'
        ..['contest_state'] = 'open'
        ..['contest_deadline'] = '2026-10-02T13:00:00Z'
        ..['can_request_second_opinion'] = true);
      expect(campo.rechazoNoPresentado, isFalse);
      expect(campo.puedoPedirSegundaOpinion, isTrue);
      expect(campo.impugnacionEtiqueta, 'Plazo para pedir segunda opinión');
      expect(campo.plazoImpugnacion, DateTime.parse('2026-10-02T13:00:00Z').toLocal());
      final segunda = Verificacion.desde(verificacionLista()..['second_opinion_of'] = 'v-0');
      expect(segunda.esSegundaOpinion, isTrue);
    });
  });

  group('cuerpos y rutas', () {
    test('el rechazo desde el veredicto es por el informe', () {
      expect(cuerpoVeredicto('reject', ' Talla chica '), {'verdict': 'reject', 'notes': 'Talla chica', 'reject_reason': 'field'});
      expect(cuerpoVeredicto('approve_obs', 'x').containsKey('reject_reason'), isFalse);
    });

    test('producto no presentado: hora UTC, notas y fotos', () {
      final c = cuerpoNoPresentado(
          llegada: DateTime.utc(2026, 10, 1, 13, 5), notas: '  No llegó el camión ', fotos: [{'data': 'QUJD', 'name': 'e.jpg'}]);
      expect(c, {
        'verdict': 'reject',
        'reject_reason': 'not_presented',
        'arrival_at': '2026-10-01T13:05:00Z',
        'notes': 'No llegó el camión',
        'photos': [
          {'data': 'QUJD', 'name': 'e.jpg'},
        ],
      });
      expect(cuerpoNoPresentado(llegada: DateTime.utc(2026), notas: 'x').containsKey('photos'), isFalse);
    });

    test('responder la orden, equipo de apoyo y segunda opinión', () {
      expect(cuerpoResponderOrden('accept', 'no se manda'), {'decision': 'accept'});
      expect(cuerpoResponderOrden('decline', ' Sin capacidad '), {'decision': 'decline', 'reason': 'Sin capacidad'});
      expect(cuerpoEquipoApoyo(sumar: ['t2']), {
        'add': [
          {'technician': 't2'},
        ],
      });
      expect(cuerpoEquipoApoyo(quitar: ['t2', 't3']), {
        'remove': [
          {'technician': 't2'},
          {'technician': 't3'},
        ],
      });
      expect(cuerpoSegundaOpinion('v2'), {'verifier': 'v2'});
      expect(cuerpoCambiarVerificadora('v3'), {'verifier': 'v3'});
      expect(cuerpoCancelarCompra('  '), isEmpty);
      expect(cuerpoCancelarCompra(' Busco otra opción '), {'note': 'Busco otra opción'});
    });

    test('verificadora vencida: cambiarla o cancelar la compra, firmadas', () {
      OperacionPendiente op(TipoOperacion t) =>
          OperacionPendiente(tipo: t, recursoId: 'T1', cuerpoSerializado: '{}', claveIdempotencia: 'k', creadaEn: DateTime(2026));
      expect(op(TipoOperacion.cambiarVerificadora).ruta, '/transactions/T1:switch-verifier');
      expect(op(TipoOperacion.cancelarCompra).ruta, '/transactions/T1:cancel');
      expect(op(TipoOperacion.cambiarVerificadora).esSensible, isTrue);
      expect(op(TipoOperacion.cancelarCompra).esSensible, isTrue);
      expect(op(TipoOperacion.cancelarCompra).respondeVerificacion, isFalse);
      final v = Verificacion.desde(verificacionLista()
        ..['deadline_overdue'] = 'declined'
        ..['auto_cancel_at'] = '2026-10-09T14:00:00Z');
      expect(v.autoCancelacion, DateTime.parse('2026-10-09T14:00:00Z').toLocal());
      expect(v.plazoVencidoEtiqueta, 'La verificadora declinó la orden');
    });

    test('rutas nuevas, firmadas y con la verificación al día en la respuesta', () {
      OperacionPendiente op(TipoOperacion t) =>
          OperacionPendiente(tipo: t, recursoId: 'R1', cuerpoSerializado: '{}', claveIdempotencia: 'k', creadaEn: DateTime(2026));
      expect(op(TipoOperacion.responderOrden).ruta, '/verifications/R1:respond');
      expect(op(TipoOperacion.equipoApoyo).ruta, '/verifications/R1:support-technicians');
      expect(op(TipoOperacion.segundaOpinion).ruta, '/verifications/R1:second-opinion');
      for (final t in [TipoOperacion.responderOrden, TipoOperacion.equipoApoyo, TipoOperacion.segundaOpinion]) {
        expect(op(t).esSensible, isTrue, reason: t.name);
        expect(op(t).metodo, 'POST');
      }
      expect(op(TipoOperacion.responderOrden).respondeVerificacion, isTrue);
      expect(op(TipoOperacion.equipoApoyo).respondeVerificacion, isTrue);
      expect(op(TipoOperacion.segundaOpinion).respondeVerificacion, isFalse, reason: 'responde la verificación nueva');
      expect(esRutaSensible('GET', '/verifications/R1/second-opinion'), isFalse);
    });
  });

  group('fotos de evidencia', () {
    test('caben juntas en una fila de la cola, aunque sean pesadas', () {
      final fotos = [for (var i = 0; i < maximoFotosEvidencia; i++) ('e$i.jpg', fotoRuidosa(1800, 1200))];
      final listas = prepararFotosEvidencia(fotos);
      expect(listas, hasLength(maximoFotosEvidencia));
      final total = listas.fold<int>(0, (s, f) => s + (f['data'] as String).length);
      expect(total, lessThanOrEqualTo(maximoCuerpoEnCola));
      for (final f in listas) {
        expect(img.decodeJpg(base64.decode(f['data'] as String)), isNotNull);
      }
      expect(listas.first['name'], 'e0.jpg');
    });

    test('más de las permitidas o algo que no es foto: FormatException', () {
      final una = ('e.jpg', fotoRuidosa(40, 40));
      expect(() => prepararFotosEvidencia([una, una, una, una]), throwsFormatException);
      expect(() => prepararFotosEvidencia([('x', Uint8List.fromList([1, 2, 3]))]), throwsFormatException);
      expect(prepararFotosEvidencia(const []), isEmpty);
    });
  });

  group('cola firmada', () {
    late ServidorFirmas servidor;
    late BaseLocal bd;
    late Sesion sesion;
    late Sincronizador sinc;
    final ahora = DateTime.utc(2026, 10, 15, 12);

    setUpAll(sqfliteFfiInit);

    setUp(() async {
      servidor = ServidorFirmas()..ahora = ahora;
      bd = BaseLocal(
          abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
              options: OpenDatabaseOptions(
                  version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));
      sesion = Sesion(credenciales: AlmacenFalso(), bd: bd, clienteHttp: servidor.cliente, firmador: FirmadorFalso(), reloj: () => ahora);
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

    Aceptada aceptadaEn(String ruta) => servidor.aceptadas.singleWhere((a) => a.req.url.path == '/api/v1$ruta');

    test('producto no presentado: fotos dentro del veredicto firmado, sin exigir el informe', () async {
      final v = Verificacion.desde(verificacionLista()..['id'] = 'v-1');
      await sinc.rechazarNoPresentado(v,
          llegada: DateTime.utc(2026, 10, 15, 9, 30),
          notas: 'Esperé dos horas; el camión no llegó',
          fotos: ['/fotos/a.jpg', '/fotos/b.jpg'],
          leer: (_) async => fotoRuidosa(2000, 1500));
      await sinc.sincronizar();
      final a = aceptadaEn('/verifications/v-1:verdict');
      expect(a.cadena.split('\n').last, sha256.convert(a.req.bodyBytes).toString(), reason: 'firma sobre los bytes enviados');
      final cuerpo = jsonDecode(a.req.body) as Map<String, dynamic>;
      expect(cuerpo['verdict'], 'reject');
      expect(cuerpo['reject_reason'], 'not_presented');
      expect(cuerpo['arrival_at'], '2026-10-15T09:30:00Z');
      expect(cuerpo['notes'], 'Esperé dos horas; el camión no llegó');
      expect((cuerpo['photos'] as List).map((f) => f['name']), ['evidencia_1.jpg', 'evidencia_2.jpg']);
      expect(a.req.bodyBytes.length, lessThan(maximoCuerpoEnCola + 4096));
      expect(sinc.cola, isEmpty);
    });

    test('rechazar por el informe no pide el informe completo; aprobar sí', () async {
      final v = Verificacion.desde(verificacionLista()
        ..['id'] = 'v-2'
        ..['state'] = 'in_field');
      expect(await sinc.darVeredicto(v, 'approve', ''), isNotEmpty);
      expect(await sinc.darVeredicto(v, 'reject', 'Talla más chica que la publicada'), isEmpty);
      await sinc.sincronizar();
      final cuerpo = jsonDecode(aceptadaEn('/verifications/v-2:verdict').req.body) as Map<String, dynamic>;
      expect(cuerpo, {'verdict': 'reject', 'notes': 'Talla más chica que la publicada', 'reject_reason': 'field'});
    });

    test('aceptar o declinar la orden y el equipo de apoyo van firmados', () async {
      final v = Verificacion.desde(verificacionLista()..['id'] = 'v-3');
      final r1 = await sinc.responderOrden(v, 'decline', motivo: 'Sin capacidad esa semana');
      expect(r1.enviado, isTrue);
      expect(jsonDecode(aceptadaEn('/verifications/v-3:respond').req.body), {'decision': 'decline', 'reason': 'Sin capacidad esa semana'});
      final r2 = await sinc.cambiarEquipoApoyo(v, sumar: ['t2']);
      expect(r2.enviado, isTrue);
      expect(jsonDecode(aceptadaEn('/verifications/v-3:support-technicians').req.body), {
        'add': [
          {'technician': 't2'},
        ],
      });
    });

    test('cambiar de verificadora y cancelar la compra van firmados', () async {
      expect((await sinc.ejecutar(TipoOperacion.cambiarVerificadora, 't-1', cuerpoCambiarVerificadora('ver-3'))).enviado, isTrue);
      expect(jsonDecode(aceptadaEn('/transactions/t-1:switch-verifier').req.body), {'verifier': 'ver-3'});
      expect((await sinc.ejecutar(TipoOperacion.cancelarCompra, 't-2', cuerpoCancelarCompra(null))).enviado, isTrue);
      expect(aceptadaEn('/transactions/t-2:cancel').req.body, '{}');
    });

    test('el vendedor pide la segunda opinión por la cola firmada', () async {
      final r = await sinc.ejecutar(TipoOperacion.segundaOpinion, 'v-4', cuerpoSegundaOpinion('ver-2'));
      expect(r.enviado, isTrue);
      final a = aceptadaEn('/verifications/v-4:second-opinion');
      expect(a.req.method, 'POST');
      expect(jsonDecode(a.req.body), {'verifier': 'ver-2'});
    });
  });
}
