// Cadena canónica y rutas sensibles del contrato de firma
// (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md, secciones 3 y 4). Los hash de
// referencia se calcularon aparte con Python (hashlib), no con esta librería.
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:verificador_campo/modelos/cuerpos.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';
import 'package:verificador_campo/seguridad/firmador.dart';

import 'firmador_falso.dart';

const hashVacio = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

void main() {
  group('cadena canónica', () {
    test('cuerpo vacío: hash de "" y sin salto final', () {
      final c = cadenaCanonica(
          metodo: 'post', ruta: '/verifications/abc:start', timestamp: 1760280000, idempotencia: 'k-1', cuerpo: const []);
      expect(c, 'POST\n/api/v1/verifications/abc:start\n1760280000\nk-1\n$hashVacio');
      expect(c.endsWith('\n'), isFalse);
      expect(c.split('\n'), hasLength(5));
    });

    test('cuerpo {} (iniciar campo) no es lo mismo que cuerpo vacío', () {
      final c = cadenaCanonica(metodo: 'POST', ruta: '/x', timestamp: 1, idempotencia: 'k', cuerpo: bytesDelCuerpo('{}'));
      expect(c.split('\n').last, '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a');
    });

    test('tildes y eñes: SHA-256 de los bytes UTF-8 exactos', () {
      final cuerpo = jsonEncode(cuerpoAceptacion('reject', 'Camarón pequeño: «ñ»'));
      expect(cuerpo, '{"decision":"reject","reason":"Camarón pequeño: «ñ»"}', reason: 'jsonEncode no escapa lo no ASCII');
      final c = cadenaCanonica(
          metodo: 'POST', ruta: '/verifications/v1/acceptance', timestamp: 1760280000, idempotencia: 'k', cuerpo: bytesDelCuerpo(cuerpo));
      expect(c.split('\n').last, '9ea73693547ab917c542e430412dbd32350067ecd22dfbb8cb7cf930b9ff60a3');
      expect(c.split('\n').last, isNot('2198ea6d97ed9e6c8dbbcba6ddb225af270a06891c0cf310715ea32622bfda68'),
          reason: 'ese sería el hash en Latin-1');
    });

    test('":" en la ruta: se firma tal cual y coincide con el path que se envía', () {
      expect(rutaFirmada('/verifications/abc:verdict'), '/api/v1/verifications/abc:verdict');
      expect(rutaFirmada('/api/v1/verifications/abc:verdict'), '/api/v1/verifications/abc:verdict', reason: 'no se duplica');
      final uri = Uri.parse('https://demo.camaronmarket.com/api/v1/copack/orders/o-9:revert-signature');
      expect(uri.path, rutaFirmada('/copack/orders/o-9:revert-signature'));
      final c = cadenaCanonica(metodo: 'POST', ruta: '/verifications/abc:verdict', timestamp: 5, idempotencia: 'id:con:dos', cuerpo: const []);
      expect(c.split('\n'), ['POST', '/api/v1/verifications/abc:verdict', '5', 'id:con:dos', hashVacio]);
    });

    test('la operación de la cola firma su cuerpo serializado guardado', () {
      final op = OperacionPendiente(
        tipo: TipoOperacion.informe,
        recursoId: 'v-1',
        cuerpoSerializado: '{"notes":"pH 7,5 — ok"}',
        claveIdempotencia: 'idem-1',
        creadaEn: DateTime(2026),
      );
      expect(op.metodo, 'PATCH');
      expect(op.cadenaParaFirmar(1760280000),
          cadenaCanonica(metodo: 'PATCH', ruta: '/verifications/v-1/report', timestamp: 1760280000, idempotencia: 'idem-1',
              cuerpo: utf8.encode('{"notes":"pH 7,5 — ok"}')));
    });

    test('segundos Unix enteros, en UTC', () {
      expect(segundosUnix(DateTime.utc(2026, 10, 12, 15)), 1791817200);
      expect(segundosUnix(DateTime.utc(2026, 10, 12, 15, 0, 0, 999)), 1791817200);
    });
  });

  group('rutas sensibles (sección 4)', () {
    test('todas las operaciones de la app se firman', () {
      for (final t in TipoOperacion.values) {
        final op = OperacionPendiente(
            tipo: t, recursoId: 'R1', despachoId: 'D1', cuerpoSerializado: '{}', claveIdempotencia: 'k', creadaEn: DateTime(2026));
        expect(op.esSensible, isTrue, reason: '${t.name} → ${op.metodo} ${op.ruta}');
      }
    });

    test('las rutas firmadas del servidor (19 + fotos C4 + compra directa C5 + publicar un lote + decisiones de la verificación) y su forma alternativa /accion', () {
      const firmadas = [
        ('POST', '/verifications'),
        ('POST', '/verifications/v1/acceptance'),
        ('POST', '/verifications/v1/acceptance:revert'),
        ('POST', '/verifications/v1:assign'),
        ('POST', '/verifications/v1:start'),
        ('POST', '/verifications/v1:verdict'),
        ('PATCH', '/verifications/v1/report'),
        ('PATCH', '/dispatches/d1'),
        ('POST', '/dispatches/d1/arrival'),
        ('POST', '/transactions/t1:complete'),
        ('POST', '/harvest/commitments/c1:buy'),
        ('POST', '/copack/requests/r1/offers'),
        ('POST', '/copack/orders/o1:reception'),
        ('POST', '/copack/orders/o1:packing'),
        ('POST', '/copack/orders/o1:sign'),
        ('POST', '/copack/orders/o1:revert-signature'),
        ('POST', '/copack/orders/o1:reopen'),
        ('POST', '/copack/orders/o1:close'),
        ('POST', '/me/active-role'),
        ('POST', '/verifications/v1/photos'),
        ('POST', '/purchases'),
        ('POST', '/products/p1:publish'),
        // Decisiones de la verificación.
        ('POST', '/verifications/v1:respond'),
        ('POST', '/verifications/v1:support-technicians'),
        ('POST', '/verifications/v1:second-opinion'),
      ];
      expect(firmadas, hasLength(25));
      for (final (m, r) in firmadas) {
        expect(esRutaSensible(m, r), isTrue, reason: '$m $r');
        expect(esRutaSensible(m, '/api/v1$r'), isTrue, reason: 'con prefijo: $m $r');
        if (r.contains(':')) expect(esRutaSensible(m, r.replaceFirst(':', '/')), isTrue, reason: 'forma alternativa de $r');
      }
    });

    test('lecturas, login, logout y registro del dispositivo no se firman', () {
      expect(esRutaSensible('GET', '/verifications'), isFalse);
      expect(esRutaSensible('GET', '/verifications/v1'), isFalse);
      expect(esRutaSensible('POST', '/auth/login'), isFalse);
      expect(esRutaSensible('POST', '/auth/logout'), isFalse);
      expect(esRutaSensible('POST', '/me/devices'), isFalse);
      expect(esRutaSensible('POST', '/me/devices/x:revoke'), isFalse);
      expect(esRutaSensible('PATCH', '/me'), isFalse);
      expect(esRutaSensible('POST', '/copack/requests'), isFalse);
      expect(esRutaSensible('POST', '/verifications/v1/report'), isFalse, reason: 'el informe es PATCH');
    });
  });

  group('vigencia de la firma', () {
    final t0 = DateTime.utc(2026, 10, 12, 12);
    FirmaOperacion firmadaEn(DateTime t) => FirmaOperacion(timestamp: segundosUnix(t), firma: 'x', huellaClave: 'h');

    test('hasta 7 días atrás (con una hora de margen) y 10 minutos adelante', () {
      expect(firmadaEn(t0).vigente(t0), isTrue);
      expect(firmadaEn(t0).vigente(t0.add(const Duration(days: 6, hours: 22))), isTrue);
      expect(firmadaEn(t0).vigente(t0.add(const Duration(days: 6, hours: 23, minutes: 1))), isFalse);
      expect(firmadaEn(t0).vigente(t0.add(const Duration(days: 8))), isFalse);
      expect(firmadaEn(t0.add(const Duration(minutes: 5))).vigente(t0), isTrue, reason: 'reloj del teléfono adelantado');
      expect(firmadaEn(t0.add(const Duration(minutes: 11))).vigente(t0), isFalse);
    });

    test('cabeceras del contrato', () {
      expect(firmadaEn(t0).cabeceras('dev-1'),
          {'X-Device-Id': 'dev-1', 'X-Signature-Timestamp': '${segundosUnix(t0)}', 'X-Signature': 'x'});
    });
  });

  group('ES256 (firmador falso con pointycastle)', () {
    test('clave pública SPKI DER P-256 y firma DER que verifica', () async {
      final f = FirmadorFalso();
      final spki = await f.prepararClaves();
      final der = base64.decode(spki);
      expect(der.length, 91);
      expect(der.sublist(0, 2), [0x30, 0x59]);
      expect(der[26], 0x04, reason: 'punto sin comprimir');
      final datos = Uint8List.fromList(utf8.encode('POST\n/api/v1/x\n1\nk\n$hashVacio'));
      final firma = await f.firmar(datos, titulo: 't');
      final sig = base64.decode(firma);
      expect(sig[0], 0x30, reason: 'SEQUENCE DER, no r||s crudo');
      expect(verificarEs256(spki, datos, firma), isTrue);
      expect(verificarEs256(spki, utf8.encode('POST\n/api/v1/x\n2\nk\n$hashVacio'), firma), isFalse);
      expect(huellaDeClave(spki), hasLength(16));
    });

    test('cancelar la huella no firma', () async {
      final f = FirmadorFalso()..fallarCon = 'cancelado';
      await f.prepararClaves();
      await expectLater(f.firmar(Uint8List(0), titulo: 't'),
          throwsA(isA<ErrorSeguridad>().having((e) => e.cancelado, 'cancelado', isTrue)));
      expect(f.firmados, isEmpty);
    });
  });
}
