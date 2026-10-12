// Seguridad de punta a punta, sin teléfono: Sesion + Sincronizador + base
// local real (sqflite_common_ffi) + firmador falso ES256 + un servidor falso
// que verifica las firmas como shrimp_api (servidor_firmas.dart).
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/base_local.dart';
import 'package:verificador_campo/datos/sesion.dart';
import 'package:verificador_campo/datos/sincronizador.dart';
import 'package:verificador_campo/modelos/cuerpos.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/seguridad/dispositivo.dart';
import 'package:verificador_campo/seguridad/firma_operacion.dart';
import 'package:verificador_campo/seguridad/firmador.dart';

import 'almacen_falso.dart';
import 'datos_api.dart';
import 'firmador_falso.dart';
import 'servidor_firmas.dart';

void main() {
  late ServidorFirmas servidor;
  late FirmadorFalso firmador;
  late AlmacenFalso almacen;
  late BaseLocal bd;
  late Sesion sesion;
  late Sincronizador sinc;
  late DateTime ahora;

  setUpAll(sqfliteFfiInit);

  Sesion nuevaSesion() =>
      Sesion(credenciales: almacen, bd: bd, clienteHttp: servidor.cliente, firmador: firmador, reloj: () => ahora);

  setUp(() {
    servidor = ServidorFirmas();
    firmador = FirmadorFalso();
    almacen = AlmacenFalso();
    ahora = DateTime.utc(2026, 10, 12, 12);
    servidor.ahora = ahora;
    bd = BaseLocal(
        abrir: () => databaseFactoryFfiNoIsolate.openDatabase(inMemoryDatabasePath,
            options: OpenDatabaseOptions(
                version: BaseLocal.version, onCreate: BaseLocal.crearEsquema, onUpgrade: BaseLocal.migrar, singleInstance: false)));
    sesion = nuevaSesion();
    sinc = Sincronizador(sesion: sesion, bd: bd);
  });

  tearDown(() async {
    // Las sincronizaciones que lanzan los cambios de sesión terminan antes de cerrar.
    for (var i = 0; i < 3; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
      await sinc.sincronizar();
    }
    sinc.dispose();
    await (await bd.bd).close();
  });

  void avanzar(Duration d) {
    ahora = ahora.add(d);
    servidor.ahora = ahora;
  }

  /// Deja que terminen las sincronizaciones que lanzan los cambios de sesión.
  Future<void> asentar() async {
    for (var i = 0; i < 3; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
      await sinc.sincronizar();
    }
  }

  Future<void> entrar() => sesion.iniciarSesion(url: 'demo.camaronmarket.com', correo: 'socio@finca.ec', contrasena: 'x');

  Future<void> entrarYProteger() async {
    await entrar();
    await sesion.proteger();
    await asentar();
  }

  const motivo = 'Camarón pequeño: «ñ»';
  const cuerpoEsperado = '{"decision":"reject","reason":"Camarón pequeño: «ñ»"}';

  Future<ResultadoEnvio> rechazarInforme([String id = 'v-1']) =>
      sinc.ejecutar(TipoOperacion.aceptacionInforme, id, cuerpoAceptacion('reject', motivo));

  group('Protege tu cuenta', () {
    test('tras entrar: nada de datos hasta proteger; luego primer registro SIN firma y clave cifrada', () async {
      await entrar();
      expect(sesion.necesitaProteger, isTrue);
      expect(sesion.apiOperativa, isNull, reason: 'ni listas ni cola antes de proteger');
      expect(almacen.claveGuardada, isNull);

      await sesion.proteger();
      expect(sesion.necesitaProteger, isFalse);
      expect(sesion.apiOperativa, isNotNull);
      final reg = servidor.a('/me/devices').single;
      expect(reg.method, 'POST');
      expect(reg.headers['Idempotency-Key'], isNotEmpty);
      expect(reg.headers.containsKey('X-Signature'), isFalse, reason: 'el primer registro no se firma');
      final cuerpo = jsonDecode(reg.body) as Map<String, dynamic>;
      expect(cuerpo, {'name': 'Android', 'platform': 'android', 'public_key': await firmador.clavePublica(), 'alg': 'ES256'});
      expect(sesion.dispositivo?.id, 'dev-1');
      expect(sesion.dispositivo?.estado, EstadoRegistro.registrado);
      expect(Dispositivo.desdeJson(almacen.dispositivoGuardado)?.id, 'dev-1');
      expect(almacen.claveGuardada, isNull, reason: 'la clave nunca queda legible');
      expect(almacen.claveCifradaGuardada, isNotNull);
      expect(await firmador.descifrar(almacen.claveCifradaGuardada!, titulo: ''), 'trz_app');
      expect(firmador.confirmaciones, 2, reason: 'una huella al proteger (cifrar) y otra la del descifrado de esta comprobación');
    });

    test('sin bloqueo de pantalla no se puede seguir', () async {
      await entrar();
      firmador.bloqueo = false;
      await expectLater(sesion.proteger(), throwsA(isA<ErrorSeguridad>().having((e) => e.codigo, 'codigo', 'sin_bloqueo')));
      expect(servidor.a('/me/devices'), isEmpty);
      expect(sesion.necesitaProteger, isTrue);
      expect(almacen.claveCifradaGuardada, isNull);
    });

    test('409 not-an-app-key: no se pide la huella y se ofrece entrar con correo y contraseña', () async {
      servidor.claveDeApp = false;
      await entrar();
      await expectLater(sesion.proteger(), throwsA(isA<ErrorApi>().having((e) => e.tipo, 'tipo', 'not-an-app-key')));
      expect(firmador.confirmaciones, 0);
      expect(sesion.necesitaProteger, isTrue);
      await sesion.entrarConCuenta();
      expect(sesion.conectado, isFalse);
      expect(sesion.avisoCaducidad, contains('correo y contraseña'));
      expect(servidor.a('/auth/logout'), isEmpty, reason: 'una clave de integración no se revoca desde la app');
    });

    test('versión anterior (clave legible): pasa por Protege tu cuenta y la clave legible se borra', () async {
      almacen.urlGuardada = 'https://demo.camaronmarket.com';
      almacen.claveGuardada = 'trz_app';
      await bd.escribirConfig('yo', jsonEncode(meUnPerfil()));
      await bd.escribirConfig('perfil', 'camaronera');
      await sesion.iniciar();
      expect(sesion.bloqueada, isFalse);
      expect(sesion.necesitaProteger, isTrue);
      await sesion.proteger();
      expect(almacen.claveGuardada, isNull);
      expect(almacen.claveCifradaGuardada, isNotNull);
      expect(sesion.dispositivo?.listo, isTrue);
    });

    test('sin señal: queda pendiente, se firma igual y se registra al sincronizar', () async {
      await entrar();
      servidor.enLinea = false;
      await sesion.proteger();
      expect(sesion.necesitaProteger, isFalse);
      expect(sesion.dispositivo?.estado, EstadoRegistro.pendiente);

      final r = await rechazarInforme();
      expect(r.enEspera, isTrue);
      final op = (await bd.cola()).single;
      expect(op.firma, isNotNull, reason: 'se firma en campo aunque el registro espere');
      expect(op.firma!.dispositivoId, isNull);

      servidor.enLinea = true;
      await asentar();
      expect(sesion.dispositivo?.id, 'dev-1');
      expect(servidor.aceptadas.single.req.headers['X-Device-Id'], 'dev-1');
      expect(await bd.cola(), isEmpty);
    });
  });

  group('operaciones firmadas', () {
    test('el cuerpo enviado es byte a byte el firmado y el servidor verifica la firma', () async {
      await entrarYProteger();
      final antes = firmador.confirmaciones;
      final r = await rechazarInforme();
      expect(r.enviado, isTrue);
      expect(firmador.confirmaciones, antes + 1, reason: 'una huella por operación');
      final req = servidor.a('/verifications/v-1/acceptance').single;
      expect(req.bodyBytes, utf8.encode(cuerpoEsperado));
      expect(req.headers['X-Device-Id'], 'dev-1');
      expect(req.headers['X-Signature-Timestamp'], '${segundosUnix(ahora)}');
      final aceptada = servidor.aceptadas.single;
      expect(firmador.firmados.last, utf8.encode(aceptada.cadena), reason: 'lo firmado = lo que reconstruye el servidor');
      expect(aceptada.cadena.split('\n')[1], '/api/v1/verifications/v-1/acceptance');
    });

    test('PATCH firmado lleva Idempotency-Key', () async {
      await entrarYProteger();
      final r = await sinc.ejecutar(TipoOperacion.programarDespacho, 'd-1', cuerpoDespacho(placa: 'gbc-1234'));
      expect(r.enviado, isTrue);
      final req = servidor.a('/dispatches/d-1').single;
      expect(req.method, 'PATCH');
      expect(req.headers['Idempotency-Key'], isNotEmpty);
      expect(req.body, '{"vehicle_plate":"GBC-1234"}');
    });

    test('sin internet: se firma al hacerla y días después se envían esos mismos bytes y esa firma', () async {
      await entrarYProteger();
      servidor.enLinea = false;
      final r = await rechazarInforme();
      expect(r.enEspera, isTrue);
      final guardada = (await bd.cola()).single;
      final confirmaciones = firmador.confirmaciones;

      avanzar(const Duration(days: 3));
      servidor.enLinea = true;
      await asentar();
      expect(firmador.confirmaciones, confirmaciones, reason: 'al enviar no se vuelve a pedir la huella');
      final req = servidor.a('/verifications/v-1/acceptance').single;
      expect(req.bodyBytes, utf8.encode(guardada.cuerpoSerializado));
      expect(req.headers['X-Signature'], guardada.firma!.firma);
      expect(req.headers['X-Signature-Timestamp'], '${guardada.firma!.timestamp}');
      expect(req.headers['Idempotency-Key'], guardada.claveIdempotencia);
      expect(await bd.cola(), isEmpty);
    });

    test('una operación sensible sin firma (versión anterior) NO se envía hasta confirmarla', () async {
      await entrarYProteger();
      await bd.encolar(OperacionPendiente(
        tipo: TipoOperacion.veredicto,
        recursoId: 'v-9',
        cuerpoSerializado: '{"verdict":"approve"}',
        claveIdempotencia: 'idem-viejo',
        creadaEn: DateTime(2026, 10, 1),
      ));
      await asentar();
      expect(servidor.a('/verifications/v-9:verdict'), isEmpty);
      final op = (await bd.cola()).single;
      expect(op.ultimoErrorTipo, 'firma-pendiente');
      expect(op.intentos, 0);
      expect(sinc.porConfirmar.single.idLocal, op.idLocal);
      expect(sesion.estadoFirma(op), EstadoFirma.falta);

      await sinc.volverAFirmar(op);
      await asentar();
      final req = servidor.a('/verifications/v-9:verdict').single;
      expect(req.headers['Idempotency-Key'], 'idem-viejo');
      expect(req.body, '{"verdict":"approve"}');
      expect(servidor.aceptadas, hasLength(1));
      expect(await bd.cola(), isEmpty);
    });

    test('firma de más de 7 días: no se envía; se vuelve a firmar con la misma Idempotency-Key', () async {
      await entrarYProteger();
      servidor.enLinea = false;
      await rechazarInforme();
      final vieja = (await bd.cola()).single;
      avanzar(const Duration(days: 8));
      servidor.enLinea = true;
      await asentar();
      expect(servidor.a('/verifications/v-1/acceptance'), isEmpty);
      expect(sinc.porConfirmar, hasLength(1));
      expect(sesion.estadoFirma((await bd.cola()).single), EstadoFirma.caducada);

      await sinc.volverAFirmar((await bd.cola()).single);
      await asentar();
      final req = servidor.a('/verifications/v-1/acceptance').single;
      expect(req.headers['X-Signature-Timestamp'], '${segundosUnix(ahora)}');
      expect(req.headers['Idempotency-Key'], vieja.claveIdempotencia);
      expect(req.bodyBytes, utf8.encode(vieja.cuerpoSerializado));
    });

    test('signature-expired del servidor: queda en Pendientes para volver a firmar y no se reintenta solo', () async {
      await entrarYProteger();
      servidor.forzar = (_) => respuestaJson(403, problema(403, 'signature-expired', 'Fuera de la ventana.'));
      final r = await rechazarInforme();
      expect(r.error, contains('Vuelve a confirmarla en Pendientes'));
      servidor.forzar = null;
      await asentar();
      expect(servidor.a('/verifications/v-1/acceptance'), hasLength(1), reason: 'no se reenvía la firma rechazada');
      final op = (await bd.cola()).single;
      expect(op.ultimoErrorTipo, 'signature-expired');
      expect(sinc.necesitaConfirmar(op), isTrue);

      await sinc.volverAFirmar(op);
      await asentar();
      expect(servidor.aceptadas, hasLength(1));
      expect(await bd.cola(), isEmpty);
    });

    test('si no se confirma con la huella no se guarda nada', () async {
      await entrarYProteger();
      firmador.fallarCon = 'cancelado';
      final r = await rechazarInforme();
      expect(r.sinConfirmar, isTrue);
      expect(r.error, contains('no se hizo nada'));
      expect(await bd.cola(), isEmpty);
      expect(servidor.a('/verifications/v-1/acceptance'), isEmpty);
    });

    test('se firma con el PIN si la persona lo elige', () async {
      await entrarYProteger();
      servidor.enLinea = false;
      await rechazarInforme();
      final op = (await bd.cola()).single;
      avanzar(const Duration(days: 8));
      await sinc.volverAFirmar(op, conPin: true);
      expect(firmador.ultimoConPin, isTrue);
    });
  });

  group('errores del dispositivo', () {
    test('device-revoked: se borran las credenciales y se vuelve a la entrada; lo capturado sigue', () async {
      await entrarYProteger();
      servidor.activo!.activo = false;
      await rechazarInforme();
      await asentar();
      expect(sesion.conectado, isFalse);
      expect(sesion.avisoCaducidad, contains('desconectado'));
      expect(almacen.claveCifradaGuardada, isNull);
      expect(almacen.dispositivoGuardado, isNull);
      expect(await firmador.clavePublica(), isNull, reason: 'el par de firma del teléfono revocado se borra');
      expect(await bd.cola(), hasLength(1));
    });

    test('device-not-registered: vuelve a "Protege tu cuenta" y, al registrar, sale lo firmado', () async {
      await entrarYProteger();
      servidor.dispositivos.clear();
      await rechazarInforme();
      await asentar();
      expect(sesion.necesitaProteger, isTrue);
      expect(sesion.protegida, isTrue, reason: 'la clave sigue cifrada: solo falta registrar');
      final confirmaciones = firmador.confirmaciones;

      await sesion.proteger();
      expect(firmador.confirmaciones, confirmaciones, reason: 'registrar no pide huella');
      await asentar();
      expect(servidor.aceptadas.single.req.headers['X-Device-Id'], 'dev-1');
      expect(await bd.cola(), isEmpty, reason: 'la firma no incluye el device_id: sigue valiendo');
    });

    test('clave de firma invalidada (cambiaron las huellas): se cierra la clave y se vuelve a entrar', () async {
      await entrarYProteger();
      firmador.firmaInvalidada = true;
      final r = await rechazarInforme();
      expect(r.sinConfirmar, isTrue);
      await asentar();
      expect(sesion.conectado, isFalse);
      expect(sesion.avisoCaducidad, contains('correo y contraseña'));
      expect(servidor.a('/auth/logout'), hasLength(1));
    });
  });

  group('Registrar de nuevo este teléfono (contrato 1.1)', () {
    test('el POST /me/devices va firmado por el dispositivo ACTUAL y solo después cambia la clave', () async {
      await entrarYProteger();
      final vieja = await firmador.clavePublica();
      servidor.enLinea = false;
      await rechazarInforme(); // firmada con la clave vieja, sin enviar
      servidor.enLinea = true;
      servidor.forzar = (_) => respuestaJson(503, problema(503, 'unavailable', ''));
      await asentar();
      servidor.forzar = null;

      await sesion.registrarDeNuevo();
      final reg = servidor.a('/me/devices').last;
      expect(reg.headers['X-Device-Id'], 'dev-1');
      expect(reg.headers['X-Signature'], isNotEmpty);
      final cadena = servidor.aceptadas.last.cadena;
      expect(cadena.split('\n').take(2), ['POST', '/api/v1/me/devices']);
      expect(verificarEs256(vieja!, utf8.encode(cadena), reg.headers['X-Signature']!), isTrue,
          reason: 'la firma es de la clave VIEJA');
      expect(jsonDecode(reg.body)['public_key'], await firmador.clavePublica(), reason: 'registra la NUEVA');
      expect(await firmador.clavePublica(), isNot(vieja));
      expect(sesion.dispositivo?.id, 'dev-2');
      expect(servidor.dispositivos.first.activo, isFalse);

      // Lo firmado con la clave vieja hay que volver a confirmarlo.
      await asentar();
      final op = (await bd.cola()).single;
      expect(sesion.estadoFirma(op), EstadoFirma.otraClave);
      await sinc.volverAFirmar(op);
      await asentar();
      expect(servidor.aceptadas.last.req.headers['X-Device-Id'], 'dev-2');
      expect(await bd.cola(), isEmpty);
    });

    test('si no se confirma (o no hay señal) no cambia nada', () async {
      await entrarYProteger();
      final vieja = await firmador.clavePublica();
      firmador.fallarCon = 'cancelado';
      await expectLater(sesion.registrarDeNuevo(), throwsA(isA<ErrorSeguridad>()));
      expect(await firmador.clavePublica(), vieja);
      expect(firmador.hayClaveNueva, isFalse);
      expect(sesion.dispositivo?.id, 'dev-1');

      servidor.enLinea = false;
      await expectLater(sesion.registrarDeNuevo(), throwsA(isA<SinConexion>()));
      expect(await firmador.clavePublica(), vieja);
      expect(sesion.dispositivo?.id, 'dev-1');
      expect(servidor.dispositivos.single.activo, isTrue);
    });
  });

  group('bloqueo de la app', () {
    test('al abrir con la clave cifrada: bloqueada hasta descifrarla con la huella o el PIN', () async {
      await entrarYProteger();
      final otra = nuevaSesion();
      await otra.iniciar();
      expect(otra.bloqueada, isTrue);
      expect(otra.conectado, isFalse);
      expect(otra.apiOperativa, isNull);

      firmador.fallarCon = 'cancelado';
      await expectLater(otra.desbloquear(), throwsA(isA<ErrorSeguridad>()));
      expect(otra.bloqueada, isTrue);

      await otra.desbloquear(conPin: true);
      expect(firmador.ultimoConPin, isTrue);
      expect(otra.bloqueada, isFalse);
      expect(otra.api?.clave, 'trz_app');
      expect(otra.apiOperativa, isNotNull);
      otra.dispose();
    });

    test('más de 2 minutos en segundo plano bloquea; menos no', () async {
      await entrarYProteger();
      sesion.alSegundoPlano();
      avanzar(const Duration(minutes: 1));
      sesion.alVolver();
      expect(sesion.bloqueada, isFalse);

      sesion.alSegundoPlano();
      avanzar(const Duration(minutes: 2, seconds: 1));
      sesion.alVolver();
      expect(sesion.bloqueada, isTrue);
      expect(sesion.necesitaProteger, isFalse);
      await sesion.desbloquear();
      expect(sesion.bloqueada, isFalse);
    });

    test('clave invalidada al desbloquear: vuelta a la entrada sin borrar lo capturado', () async {
      await entrarYProteger();
      servidor.enLinea = false;
      await rechazarInforme();
      final otra = nuevaSesion();
      await otra.iniciar();
      firmador.fallarCon = 'clave_invalidada';
      await otra.desbloquear();
      expect(otra.bloqueada, isFalse);
      expect(otra.conectado, isFalse);
      expect(otra.avisoCaducidad, contains('Cambiaron las huellas'));
      expect(almacen.claveCifradaGuardada, isNull);
      expect(await bd.cola(), hasLength(1));
      otra.dispose();
    });
  });
}
