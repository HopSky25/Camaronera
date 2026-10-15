import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

import '../modelos/operacion_pendiente.dart';
import '../modelos/perfil.dart';
import '../modelos/supervisor.dart';
import '../modelos/verificacion.dart' show jsonTexto;
import '../seguridad/dispositivo.dart';
import '../seguridad/firma_operacion.dart';
import '../seguridad/firmador.dart';
import 'almacen_credenciales.dart';
import 'api_cliente.dart';
import 'base_local.dart';
import 'version_app.dart';

/// La cuenta con la que se entró y el perfil con el que se trabaja.
///
/// - Entrar por cuenta: `POST /auth/login` devuelve la clave de API y `me`.
/// - Entrar con clave de API (opción avanzada): `GET /me` la comprueba.
/// - La clave queda cifrada en el teléfono (AlmacenCredenciales); `me` y el
///   perfil elegido se guardan en la base local para abrir la app sin señal.
/// - "Actuar como": el perfil elegido NO se cambia en el servidor (eso
///   movería los menús del portal de la misma cuenta); se manda en la
///   cabecera `X-Shrimp-Role` de cada petición privada. El técnico de campo
///   manda el perfil activo de su cuenta (`me.active_role`).
/// - Con un 401 (clave caducada o revocada) la sesión caduca: se vuelve a la
///   pantalla de entrada pero NO se borra lo capturado en el teléfono. Si
///   después entra OTRA cuenta, lo de la anterior sí se borra (su cola no se
///   puede enviar con otra clave).
///
/// Seguridad del teléfono (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md):
/// - Tras entrar, "Protege tu cuenta" ([necesitaProteger]): se exige bloqueo
///   de pantalla, se crean las claves del Keystore, la clave de API se guarda
///   cifrada bajo la huella / PIN y se registra el teléfono (`POST /me/devices`).
/// - Al abrir la app, y al volver tras más de [tiempoBloqueo] en segundo
///   plano, la app queda [bloqueada] hasta desbloquear con huella o PIN.
/// - Las operaciones sensibles se firman al hacerlas ([firmarOperacion]).
/// - `device-revoked` → se borran las credenciales y se vuelve a la entrada;
///   `device-not-registered` → se vuelve a "Protege tu cuenta".
///
/// Modo supervisor (contrato, Anexo B): si el login responde
/// `"supervisor": true`, la clave es de supervisor ([esSupervisor]). Pasa
/// igual por "Protege tu cuenta" y por el desbloqueo, y después la app
/// muestra el directorio de socios ([necesitaDirectorio]). "Actuar como"
/// ([actuarComo]) pide una clave de solo lectura del socio con
/// `POST /supervisor/act-as` FIRMADO por este teléfono; esa clave vive solo
/// en memoria ([comoSocio]) y con ella se leen los datos del socio sin firma.
/// Todo es de solo lectura ([soloLectura]): la cola no encola ni envía nada.
/// Un 401 de la clave del socio vuelve al directorio; uno de la clave de
/// supervisor, a la entrada.

/// "Actuar como" un socio en modo supervisor: su clave de solo lectura (solo
/// en memoria, caduca a las 8 h), su `me` y el perfil con el que se ve.
class ComoSocio {
  final SocioDirectorio socio;
  final ApiCliente cliente;
  final Yo yo;
  final DateTime? caducaEn;

  /// Nombre del supervisor según el servidor (`me.viewed_by.name`).
  final String? vistoPor;

  /// Perfil del socio con el que se ve su app (`X-Shrimp-Role`). Nulo si
  /// tiene varios aprobados y todavía no se eligió.
  String? perfil;

  ComoSocio({required this.socio, required this.cliente, required this.yo, this.caducaEn, this.perfil, this.vistoPor});

  String get nombre => yo.nombre.isNotEmpty ? yo.nombre : socio.nombre;
}

/// Si la firma guardada de una operación de la cola se puede enviar.
enum EstadoFirma {
  /// No hace falta firma (ruta no sensible o clave de integración).
  noHaceFalta,

  /// Firmada, vigente y con el teléfono registrado: se envía.
  lista,

  /// Firmada, pero el registro del teléfono espera señal: se envía después.
  esperaRegistro,

  /// Sin firma (versión anterior de la app): hay que confirmarla.
  falta,

  /// Firmada con otro par de claves (teléfono registrado de nuevo).
  otraClave,

  /// Más de 7 días: el servidor la rechazaría.
  caducada,

  /// El servidor rechazó la firma (signature-expired, invalid-signature...).
  rechazada,
}

class Sesion extends ChangeNotifier {
  final AlmacenCredenciales _credenciales;
  final BaseLocal _bd;

  /// Huella, PIN y claves del Keystore (en pruebas, uno falso).
  final Firmador firmador;
  final DateTime Function() _reloj;

  /// Tras este tiempo en segundo plano la app pide desbloquear.
  static const tiempoBloqueo = Duration(minutes: 2);

  /// Cliente HTTP compartido (en pruebas, uno falso).
  final http.Client? _http;
  final _uuid = const Uuid();

  /// Cliente con la clave de API en memoria. Para leer o escribir datos usa
  /// [apiOperativa]: esta existe también antes de proteger la cuenta.
  ApiCliente? api;
  Yo? _yo;

  /// Quién se ve en la app: el socio en modo supervisor ("actuar como") o la
  /// cuenta con la que se entró.
  Yo? get yo => comoSocio?.yo ?? _yo;

  /// La cuenta con la que se entró (en modo supervisor, el supervisor).
  Yo? get yoPropio => _yo;

  /// Modo supervisor: el socio que se está viendo (solo lectura).
  ComoSocio? comoSocio;

  /// Aviso para el directorio (p. ej. caducó el acceso a un socio).
  String? avisoDirectorio;

  /// La clave de API está guardada cifrada bajo la huella / PIN.
  bool protegida = false;

  /// Hay que desbloquear con huella o PIN antes de ver ningún dato.
  bool bloqueada = false;

  /// El teléfono registrado (o pendiente de registrar) para firmar.
  Dispositivo? dispositivo;
  DateTime? _segundoPlanoDesde;

  String? _perfil;

  /// Perfil con el que se trabaja: un rol de la plataforma o [perfilTecnico].
  /// Nulo cuando hay varios aprobados y todavía no se ha elegido. En modo
  /// supervisor, el perfil del socio que se está viendo.
  String? get perfil => comoSocio != null ? comoSocio!.perfil : _perfil;
  bool cargando = true;

  /// Mensaje que se muestra en la pantalla de entrada tras caducar la sesión.
  String? avisoCaducidad;

  /// Cuándo caduca la clave de esta sesión (`expires_at` del login o de
  /// `me.api_key`). Nulo si no se sabe.
  DateTime? caducaEn;

  /// Aviso para la cabecera cuando quedan menos de 15 días de sesión.
  String? get avisoCaducidadProxima {
    final fin = caducaEn;
    // La clave de supervisor dura 8 h: no se avisa por días.
    if (fin == null || !conectado || esSupervisor) return null;
    final dias = fin.difference(DateTime.now()).inDays;
    if (dias > 15) return null;
    if (dias <= 0) return 'La sesión caduca hoy: cierra sesión y vuelve a entrar para renovarla.';
    return 'La sesión caduca en $dias ${dias == 1 ? 'día' : 'días'}: cierra sesión y vuelve a entrar para renovarla.';
  }
  String url = 'https://demo.camaronmarket.com';

  Sesion({
    AlmacenCredenciales? credenciales,
    BaseLocal? bd,
    http.Client? clienteHttp,
    Firmador? firmador,
    DateTime Function()? reloj,
  })  : _credenciales = credenciales ?? AlmacenCredenciales(),
        _bd = bd ?? BaseLocal(),
        _http = clienteHttp,
        firmador = firmador ?? FirmadorAndroid(),
        _reloj = reloj ?? DateTime.now;

  bool get conectado => api != null;

  /// Falta "Protege tu cuenta": la clave no está cifrada o el teléfono no
  /// está registrado (tras el login, tras actualizar desde la versión
  /// anterior o tras un `device-not-registered`).
  bool get necesitaProteger => conectado && !bloqueada && (!protegida || dispositivo == null);

  /// La clave propia, ya protegida y con el teléfono registrado.
  ApiCliente? get _apiPropiaOperativa => protegida && dispositivo != null ? api : null;

  /// El cliente con el que se leen y envían datos: solo con la cuenta
  /// protegida (con la app bloqueada sigue sincronizando en segundo plano,
  /// pero la pantalla no muestra nada). En modo supervisor es el de la clave
  /// de solo lectura del socio (nulo en el directorio).
  ApiCliente? get apiOperativa {
    final propia = _apiPropiaOperativa;
    if (propia == null) return null;
    return esSupervisor ? comoSocio?.cliente : propia;
  }

  /// La cuenta es de supervisor (Anexo B): directorio y "actuar como".
  bool get esSupervisor => _yo?.esSupervisor ?? false;

  /// Nada de escritura: ni botones, ni cola, ni firma de operaciones.
  bool get soloLectura => esSupervisor;

  /// Supervisor sin socio elegido: pantalla "Elige un socio".
  bool get necesitaDirectorio => esSupervisor && conectado && !necesitaProteger && comoSocio == null;

  /// Las operaciones sensibles llevan firma del teléfono.
  bool get firmaRequerida => dispositivo != null;

  bool get necesitaElegirPerfil => conectado && perfil == null && (!esSupervisor || comoSocio != null);
  bool get esTecnico => perfil == perfilTecnico;
  String get nombre => yo?.nombre ?? 'Cuenta conectada';

  /// Valor de `X-Shrimp-Role`: el perfil elegido o, para el técnico de
  /// campo, el perfil activo de su cuenta (siempre válido para el servidor).
  String? get rolParaApi => perfil == perfilTecnico ? yo?.perfilActivo : perfil;

  final _listo = Completer<void>();

  /// Se completa cuando [iniciar] terminó de leer lo guardado (servidor incluido).
  Future<void> get listo => _listo.future;

  /// Al abrir la app: lee lo guardado y, si hay clave, refresca `me`.
  Future<void> iniciar() async {
    try {
      final urlGuardada = await _credenciales.url();
      final cifrada = await _credenciales.claveCifrada();
      final clave = cifrada == null ? await _credenciales.clave() : null;
      dispositivo = Dispositivo.desdeJson(await _credenciales.dispositivo());
      final yoGuardado = await _bd.leerConfig('yo');
      if (yoGuardado != null) {
        try {
          _yo = Yo.desde(jsonDecode(yoGuardado) as Map<String, dynamic>);
        } catch (_) {
          _yo = null; // guardado dañado: se vuelve a pedir
        }
      }
      _perfil = await _bd.leerConfig('perfil');
      caducaEn = DateTime.tryParse(await _bd.leerConfig('caduca_en') ?? '')?.toLocal();
      if (urlGuardada != null) url = urlGuardada;
      if (urlGuardada != null && cifrada != null && cifrada.isNotEmpty) {
        // Clave cifrada: no se lee hasta que la persona desbloquee.
        protegida = true;
        bloqueada = true;
      } else if (urlGuardada != null && clave != null && clave.isNotEmpty) {
        // Versión anterior (clave legible): pasa por "Protege tu cuenta".
        protegida = false;
        dispositivo = null;
        api = _nuevoCliente(urlGuardada, clave);
        _aplicarRol();
        if (_yo == null || _perfil == null) {
          // Sesión guardada sin `me` (versión anterior): se completa con /me,
          // con un tope corto para no dejar la pantalla de carga colgada.
          try {
            await _recibir(await api!.quienSoy().timeout(const Duration(seconds: 10)));
          } catch (_) {
            // Sin señal o sin permiso: se sigue con lo guardado.
          }
        } else {
          unawaited(_refrescarYo());
        }
      }
    } catch (e) {
      debugPrint('No se pudo leer la sesión guardada: $e');
    } finally {
      cargando = false;
      if (!_listo.isCompleted) _listo.complete();
      notifyListeners();
    }
  }

  ApiCliente _nuevoCliente(String url, String clave) {
    final cliente = ApiCliente(baseUrl: url, clave: clave, cliente: _http);
    cliente.alNoAutorizado = (e) => _caducar(cliente, e);
    cliente.alErrorDispositivo = (e) => _errorDispositivo(cliente, e);
    return cliente;
  }

  /// La clave de supervisor no lleva `X-Shrimp-Role` (directorio y act-as);
  /// la del socio lleva el perfil con el que se ve su app.
  void _aplicarRol() {
    api?.rolActivo = esSupervisor ? null : rolParaApi;
    comoSocio?.cliente.rolActivo = rolParaApi;
  }

  Future<void> _refrescarYo() async {
    // La clave de supervisor no tiene scopes de datos: no se pide /me.
    if (esSupervisor) return;
    try {
      await _recibir(await api!.quienSoy());
    } catch (_) {
      // Sin señal: se usa el `me` guardado.
    }
  }

  static Future<String> _dispositivo() async {
    try {
      if (Platform.isAndroid) {
        final info = await DeviceInfoPlugin().androidInfo;
        return '${info.manufacturer} ${info.model}'.trim();
      }
      if (Platform.isIOS) return (await DeviceInfoPlugin().iosInfo).utsname.machine;
    } catch (_) {
      // Sin información del equipo: se manda el sistema.
    }
    return 'Android';
  }

  /// Entrar por cuenta: `POST /auth/login` → `{api_key, expires_at, me}`.
  Future<void> iniciarSesion({required String url, required String correo, required String contrasena}) async {
    final limpia = AlmacenCredenciales.normalizar(url);
    final resp = await ApiCliente.iniciarSesion(
      baseUrl: limpia,
      correo: correo.trim(),
      contrasena: contrasena,
      dispositivo: await _dispositivo(),
      cliente: _http,
    );
    final clave = jsonTexto(resp['api_key']);
    if (clave == null || clave.isEmpty) {
      throw const ErrorApi(500, 'Respuesta inesperada', 'El servidor no devolvió la clave de sesión.', 'internal-error');
    }
    final me = resp['me'];
    var datos = me is Map<String, dynamic> ? me : null;
    // Modo supervisor (Anexo B): se marca en `me` para recordarlo al abrir la app.
    if (esLoginSupervisor(resp)) datos = {...?datos, 'supervisor': true};
    await _abrir(limpia, clave, datos, caduca: DateTime.tryParse(jsonTexto(resp['expires_at']) ?? ''));
  }

  /// Entrar con una clave de API creada en el portal (ya no se ofrece en la
  /// app: esas claves no se pueden ligar al teléfono —409 not-an-app-key— y
  /// "Protege tu cuenta" pide entrar con correo y contraseña).
  Future<void> entrarConClave({required String url, required String clave}) =>
      _abrir(AlmacenCredenciales.normalizar(url), clave.trim(), null);

  /// `GET /me/devices`: el dispositivo de esta sesión según el servidor
  /// (con `last_used_at`). Nulo si no se encuentra o no hay señal.
  Future<Map<String, dynamic>?> dispositivoEnServidor() async {
    final cliente = _apiPropiaOperativa;
    final id = dispositivo?.id;
    if (cliente == null) return null;
    try {
      final r = await cliente.get('/me/devices');
      final lista = (r['data'] is List ? r['data'] as List : const []).whereType<Map<String, dynamic>>();
      return lista.where((d) => d['id'] == id).firstOrNull ??
          lista.where((d) => d['current'] == true && d['active'] == true).firstOrNull;
    } catch (_) {
      return null;
    }
  }

  /// Comprueba la clave (si no vino `me`, lo pide), guarda las credenciales
  /// y deja la sesión abierta.
  Future<void> _abrir(String url, String clave, Map<String, dynamic>? me, {DateTime? caduca}) async {
    final cliente = _nuevoCliente(url, clave);
    final datos = me ?? await cliente.quienSoy();
    await _siCambiaDeCuenta(datos);
    _soltarComoSocioYa();
    // La clave NO se guarda legible: queda en memoria hasta "Protege tu
    // cuenta", que la guarda cifrada bajo la huella / PIN. Una clave nueva
    // necesita registrar el teléfono otra vez (un dispositivo por clave).
    await _credenciales.borrarClave();
    await _credenciales.guardarUrl(url);
    this.url = url;
    api?.cerrar();
    api = cliente;
    protegida = false;
    bloqueada = false;
    dispositivo = null;
    caducaEn = caduca?.toLocal();
    await _recibir(datos);
  }

  // ---------------------------------------------------------------- seguridad

  Future<String> _nombreDispositivo() => _dispositivo();

  /// "Protege tu cuenta": exige bloqueo de pantalla, crea las claves del
  /// Keystore, registra el teléfono (`POST /me/devices`, primer registro sin
  /// firma) y guarda la clave de API cifrada bajo la huella / PIN (una
  /// confirmación). Si no hay señal para registrar, queda pendiente y se
  /// registra en la próxima sincronización. Con una clave que no es de la app
  /// el servidor responde 409 not-an-app-key: la pantalla ofrece
  /// [entrarConCuenta].
  Future<void> proteger({bool conPin = false}) async {
    final cliente = api;
    if (cliente == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    final estado = await firmador.estado();
    if (!estado.bloqueoPantalla) throw const ErrorSeguridad('sin_bloqueo');
    final spki = await firmador.prepararClaves();
    // Primero el registro (sin huella): si la clave no es de la app no se
    // pide nada a la persona.
    if (dispositivo == null) await _registrar(cliente, spki);
    if (!protegida) {
      try {
        await _credenciales.guardarClaveCifrada(await _cifrarClave(cliente.clave, conPin));
      } on ErrorSeguridad catch (e) {
        if (e.claveInvalidada) {
          // Cambiaron las huellas / el bloqueo desde que se crearon las
          // claves: se empieza de cero (claves nuevas, clave de API nueva).
          await _perderClave(e.mensaje, borrarClaves: true, cerrarEnServidor: true);
        }
        notifyListeners();
        rethrow;
      }
      protegida = true;
    }
    notifyListeners();
  }

  Future<String> _cifrarClave(String clave, bool conPin) => firmador.cifrar(clave,
      titulo: 'Proteger tu cuenta', subtitulo: 'Confirma con tu huella o el PIN del teléfono', conPin: conPin);

  /// Primer registro del teléfono: `POST /me/devices` SIN firma (la clave
  /// todavía no tiene dispositivo activo).
  Future<void> _registrar(ApiCliente cliente, String spki) async {
    final huella = huellaDeClave(spki);
    final nombre = await _nombreDispositivo();
    Dispositivo nuevo;
    try {
      final r = await cliente.registrarDispositivo(nombre: nombre, clavePublica: spki, idempotencia: _uuid.v4());
      nuevo = _dispositivoDe(r, nombre, huella);
    } on SinConexion {
      nuevo = Dispositivo(nombre: nombre, huellaClave: huella, estado: EstadoRegistro.pendiente);
    } on ErrorApi catch (e) {
      if (e.tipo == 'signature-required') {
        // La clave ya tiene OTRO dispositivo activo y este teléfono no tiene
        // con qué firmar la sustitución: solo queda entrar de nuevo (el login
        // emite una clave nueva, sin dispositivo).
        await _perderClave('Tu cuenta ya tiene otro teléfono registrado con esta sesión. '
            'Vuelve a entrar con tu correo y contraseña para registrar este.', cerrarEnServidor: true);
      }
      rethrow;
    }
    dispositivo = nuevo;
    await _credenciales.guardarDispositivo(nuevo.aJson());
  }

  static Dispositivo _dispositivoDe(Map<String, dynamic> r, String nombre, String huella) {
    final id = jsonTexto(r['id']);
    if (id == null || id.isEmpty) {
      throw const ErrorApi(500, 'Respuesta inesperada', 'El servidor no devolvió el id del dispositivo.', 'internal-error');
    }
    return Dispositivo(
      id: id,
      nombre: jsonTexto(r['name']) ?? nombre,
      creadoEn: DateTime.tryParse(jsonTexto(r['created_at']) ?? '')?.toLocal(),
      huellaClave: huella,
      estado: EstadoRegistro.registrado,
    );
  }

  /// La clave con la que se entró no es de la app (409 not-an-app-key: clave
  /// de integración pegada a mano, o una sesión de antes de la firma por
  /// dispositivo). Se olvida en el teléfono —sin revocarla en el servidor:
  /// puede ser la de una integración— y se vuelve a la entrada para entrar
  /// con correo y contraseña. Lo capturado en el teléfono se conserva.
  Future<void> entrarConCuenta() => _perderClave(
      'Para proteger tu cuenta con este teléfono entra con tu correo y contraseña '
      '(las claves de API creadas a mano en el portal no se pueden ligar al teléfono).');

  /// Registro que quedó pendiente por falta de señal: se completa al
  /// sincronizar, antes de enviar nada firmado. Nunca lanza.
  Future<void> reintentarRegistro() async {
    final d = dispositivo;
    final cliente = api;
    if (d == null || d.estado != EstadoRegistro.pendiente || cliente == null) return;
    try {
      final spki = await firmador.clavePublica();
      if (spki == null || huellaDeClave(spki) != d.huellaClave) {
        // El par cambió (o se perdió): hay que proteger de nuevo.
        dispositivo = null;
        await _credenciales.guardarDispositivo(null);
      } else {
        await _registrar(cliente, spki);
      }
      notifyListeners();
    } catch (e) {
      debugPrint('No se pudo completar el registro del teléfono: $e');
    }
  }

  /// "Registrar de nuevo este teléfono" (pantalla Seguridad, contrato 1.1).
  /// Con el teléfono ya registrado, sustituirlo exige la firma del
  /// dispositivo ACTUAL: se crea un par nuevo sin borrar el viejo, el viejo
  /// firma el `POST /me/devices` (huella / PIN) con su X-Device-Id, y solo
  /// tras el 201 se activa el nuevo y se borra el viejo. Si falla, sigue el
  /// viejo. Necesita señal. Lo firmado con el par viejo que siga en la cola
  /// hay que volver a confirmarlo.
  Future<void> registrarDeNuevo({bool conPin = false}) async {
    final cliente = api;
    final d = dispositivo;
    if (cliente == null || d == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    final estado = await firmador.estado();
    if (!estado.bloqueoPantalla) throw const ErrorSeguridad('sin_bloqueo');
    if (d.estado == EstadoRegistro.pendiente) {
      // El servidor aún no tiene ningún dispositivo de esta clave: el primer
      // registro va sin firma.
      final spki = await firmador.clavePublica() ?? await firmador.prepararClaves();
      try {
        final r = await cliente.registrarDispositivo(
            nombre: await _nombreDispositivo(), clavePublica: spki, idempotencia: _uuid.v4());
        dispositivo = _dispositivoDe(r, d.nombre, huellaDeClave(spki));
        await _credenciales.guardarDispositivo(dispositivo!.aJson());
      } finally {
        notifyListeners();
      }
      return;
    }
    final vieja = await firmador.clavePublica();
    if (vieja == null || huellaDeClave(vieja) != d.huellaClave) {
      throw const ErrorSeguridad('sin_claves', 'La clave de este teléfono ya no existe.');
    }
    final nueva = await firmador.prepararClaveNueva();
    final nombre = await _nombreDispositivo();
    try {
      final cuerpo = jsonEncode({'name': nombre, 'platform': plataformaApp, 'public_key': nueva, 'alg': 'ES256'});
      final idem = _uuid.v4();
      final ts = segundosUnix(_reloj());
      final cadena = cadenaCanonica(
          metodo: 'POST', ruta: '/me/devices', timestamp: ts, idempotencia: idem, cuerpo: bytesDelCuerpo(cuerpo));
      final firma = await firmador.firmar(Uint8List.fromList(utf8.encode(cadena)),
          titulo: 'Confirma: registrar de nuevo este teléfono', subtitulo: 'Con tu huella o el PIN del teléfono', conPin: conPin);
      final r = await cliente.enviarSerializado('POST', '/me/devices', cuerpo,
          idempotencia: idem,
          firma: FirmaOperacion(timestamp: ts, firma: firma, huellaClave: d.huellaClave).cabeceras(d.id!));
      final nuevo = _dispositivoDe(r, nombre, huellaDeClave(nueva));
      await firmador.activarClaveNueva();
      dispositivo = nuevo;
      await _credenciales.guardarDispositivo(nuevo.aJson());
    } catch (e) {
      try {
        await firmador.descartarClaveNueva();
      } catch (_) {}
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  /// Firma una operación sensible en el momento de hacerla: pide la huella
  /// (o el PIN) y firma la cadena canónica del contrato con la hora actual.
  /// Nula si la operación no se firma (ruta no sensible o clave de
  /// integración). Lanza [ErrorSeguridad] si la persona no confirma.
  Future<FirmaOperacion?> firmarOperacion(OperacionPendiente op, {bool conPin = false}) async {
    final d = dispositivo;
    if (!op.esSensible || d == null) return null;
    final ts = segundosUnix(_reloj());
    try {
      final firma = await firmador.firmar(
        Uint8List.fromList(utf8.encode(op.cadenaParaFirmar(ts))),
        titulo: 'Confirma: ${op.descripcion}',
        subtitulo: 'Con tu huella o el PIN del teléfono',
        conPin: conPin,
      );
      return FirmaOperacion(timestamp: ts, firma: firma, huellaClave: d.huellaClave, dispositivoId: d.id);
    } on ErrorSeguridad catch (e) {
      if (e.claveInvalidada) {
        // El par de firma ya no sirve y sin él no se puede sustituir el
        // dispositivo (contrato 1.1): se cierra esta clave en el servidor
        // (revoca también el dispositivo) y se vuelve a entrar. Lo capturado
        // se conserva y habrá que volver a confirmarlo.
        await _perderClave(e.mensaje, borrarFirma: true, cerrarEnServidor: true);
      }
      rethrow;
    }
  }

  /// ¿Se puede enviar ya la firma guardada de [op]? (ver [EstadoFirma]).
  EstadoFirma estadoFirma(OperacionPendiente op) {
    final d = dispositivo;
    if (!op.esSensible || d == null) return EstadoFirma.noHaceFalta;
    final f = op.firma;
    if (f == null) return EstadoFirma.falta;
    if (f.huellaClave != d.huellaClave) return EstadoFirma.otraClave;
    if (op.errorDeFirma && op.ultimoErrorTipo != 'firma-pendiente') return EstadoFirma.rechazada;
    if (!f.vigente(_reloj())) return EstadoFirma.caducada;
    if (!d.listo) return EstadoFirma.esperaRegistro;
    return EstadoFirma.lista;
  }

  /// Desbloquear: descifra la clave de API con la huella (o el PIN).
  Future<void> desbloquear({bool conPin = false}) async {
    final cifrada = await _credenciales.claveCifrada();
    if (cifrada == null) {
      bloqueada = false;
      notifyListeners();
      return;
    }
    String clave;
    try {
      clave = await firmador.descifrar(cifrada,
          titulo: 'Desbloquear CamaronMarket', subtitulo: 'Con tu huella o el PIN del teléfono', conPin: conPin);
    } on ErrorSeguridad catch (e) {
      if (!e.claveInvalidada) rethrow;
      await _perderClave(e.mensaje, borrarClaves: true);
      return;
    }
    if (api == null || api!.clave != clave) {
      api?.cerrar();
      api = _nuevoCliente(url, clave);
      _aplicarRol();
      unawaited(_refrescarYo());
    }
    bloqueada = false;
    _segundoPlanoDesde = null;
    notifyListeners();
  }

  /// La app pasa a segundo plano.
  void alSegundoPlano() => _segundoPlanoDesde ??= _reloj();

  /// La app vuelve: si estuvo fuera más de [tiempoBloqueo], se bloquea.
  void alVolver() {
    final desde = _segundoPlanoDesde;
    _segundoPlanoDesde = null;
    final c = comoSocio;
    final fin = c?.caducaEn;
    if (c != null && fin != null && !_reloj().isBefore(fin)) {
      _caducarComoSocio(c.cliente, const ErrorApi(401, 'Caducada', '', 'expired-api-key'));
    }
    if (desde != null && protegida && !bloqueada && _reloj().difference(desde) > tiempoBloqueo) {
      bloqueada = true;
      notifyListeners();
    }
  }

  /// La clave cifrada ya no se puede usar: vuelta a la entrada sin borrar lo
  /// capturado en el teléfono.
  Future<void> _perderClave(String aviso,
      {bool borrarClaves = false, bool borrarFirma = false, bool cerrarEnServidor = false}) async {
    final cliente = api;
    api = null;
    _soltarComoSocioYa();
    if (cliente != null) {
      if (cerrarEnServidor) {
        try {
          await cliente.cerrarSesion(idempotencia: _uuid.v4()).timeout(const Duration(seconds: 10));
        } catch (_) {
          // Sin señal: la clave caduca sola; en el teléfono ya no queda.
        }
      }
      cliente.cerrar();
    }
    protegida = false;
    bloqueada = false;
    dispositivo = null;
    avisoCaducidad = aviso;
    await _credenciales.borrarClave();
    try {
      if (borrarClaves) {
        await firmador.borrarClaves();
      } else if (borrarFirma) {
        await firmador.borrarClaveFirma();
      }
    } catch (e) {
      debugPrint('No se pudieron borrar las claves: $e');
    }
    notifyListeners();
  }

  /// 403 device-revoked / device-not-registered del cliente vigente.
  void _errorDispositivo(ApiCliente cliente, ErrorApi e) {
    if (!identical(api, cliente)) return;
    if (e.dispositivoRevocado) {
      unawaited(_perderClave(e.mensaje, borrarFirma: true));
    } else if (e.dispositivoNoRegistrado) {
      dispositivo = null;
      unawaited(_credenciales.guardarDispositivo(null));
      notifyListeners();
    }
  }

  /// Si entra una cuenta distinta de la que dejó datos en el teléfono, se
  /// borra todo lo de la anterior (su cola no se puede enviar con otra clave).
  Future<void> _siCambiaDeCuenta(Map<String, dynamic> me) async {
    final anterior = _yo == null ? '' : _identidad(_yo!.crudo);
    final nuevo = _identidad(me);
    if (anterior.isNotEmpty && nuevo.isNotEmpty && anterior != nuevo) {
      await _bd.borrarTodo();
      _yo = null;
      _perfil = null;
    }
  }

  /// Con qué se compara si es la misma cuenta. El `me` del supervisor puede
  /// no traer `id`: se distingue igual de cualquier socio.
  static String _identidad(Map<String, dynamic> me) {
    final id = jsonTexto(me['id']) ?? '';
    if (me['supervisor'] != true) return id;
    return 'supervisor:${id.isNotEmpty ? id : (jsonTexto(me['email']) ?? jsonTexto(me['name']) ?? '')}';
  }

  /// Perfil con el que se entra: técnico → directo; un aprobado → directo;
  /// varios → hay que elegir (o se conserva [actual] si sigue aprobado).
  static String? _perfilPara(Yo y, String? actual) {
    if (y.esSupervisor) return null;
    if (y.esTecnico) return perfilTecnico;
    final aprobados = y.aprobados;
    if (aprobados.length == 1) return aprobados.first.rol;
    // Ya no está aprobado (o era "técnico"): se vuelve a elegir.
    if (actual != null && !aprobados.any((p) => p.rol == actual)) return null;
    // Sin perfiles aprobados: se deja entrar con el que diga el servidor
    // para que al menos vea el aviso.
    if (actual == null && aprobados.isEmpty) return y.perfilActivo;
    // Con varios aprobados y ninguno elegido todavía se pregunta; el que la
    // cuenta tiene activo en el portal aparece marcado como sugerido.
    return actual;
  }

  /// Guarda `me` y decide el perfil: técnico → directo; un aprobado → directo;
  /// varios → hay que elegir (o se conserva el ya elegido si sigue aprobado).
  Future<void> _recibir(Map<String, dynamic> me) async {
    _yo = Yo.desde(me);
    avisoCaducidad = null;
    await _bd.escribirConfig('yo', jsonEncode(me));
    final claveApi = me['api_key'];
    final expira = claveApi is Map ? DateTime.tryParse(jsonTexto(claveApi['expires_at']) ?? '') : null;
    if (expira != null) caducaEn = expira.toLocal();
    await _bd.escribirConfig('caduca_en', caducaEn?.toUtc().toIso8601String());
    _perfil = _perfilPara(_yo!, _perfil);
    await _bd.escribirConfig('perfil', _perfil);
    _aplicarRol();
    notifyListeners();
  }

  /// Elegir (o cambiar) el perfil de trabajo. Se guarda en el teléfono, se
  /// manda en `X-Shrimp-Role` y se vacía la caché de listas del perfil
  /// anterior; no se toca el perfil activo de la cuenta en el portal.
  Future<void> elegirPerfil(String rol) async {
    if (rol != perfil) await _bd.borrarCache();
    final c = comoSocio;
    if (c != null) {
      // Modo supervisor: solo en memoria, y solo la cabecera (nunca
      // POST /me/active-role, que cambiaría el portal del socio).
      c.perfil = rol;
      _aplicarRol();
      notifyListeners();
      return;
    }
    _perfil = rol;
    await _bd.escribirConfig('perfil', rol);
    _aplicarRol();
    notifyListeners();
  }

  /// Quita el perfil elegido para volver a la pantalla de elección.
  Future<void> volverAElegirPerfil() async {
    final c = comoSocio;
    if (c != null) {
      c.perfil = null;
      _aplicarRol();
      notifyListeners();
      return;
    }
    _perfil = null;
    await _bd.escribirConfig('perfil', null);
    _aplicarRol();
    notifyListeners();
  }

  /// Cierra la sesión: revoca la clave en el servidor (si se puede) y borra
  /// todo lo guardado en el teléfono (credenciales, bandeja, cola, fotos).
  Future<void> cerrarSesion() async {
    final socio = comoSocio;
    comoSocio = null;
    if (socio != null) {
      try {
        await _revocarComoSocio(socio).timeout(const Duration(seconds: 12));
      } catch (_) {}
    }
    final cliente = api;
    api = null;
    if (cliente != null) {
      try {
        await cliente.cerrarSesion(idempotencia: _uuid.v4()).timeout(const Duration(seconds: 10));
      } catch (_) {
        // Sin señal o clave ya inválida: igual se sale de la app.
      }
      cliente.cerrar();
    }
    // Cada paso por separado y con tiempo límite: cerrar sesión SIEMPRE tiene
    // que terminar, aunque falle uno (p. ej. datos de una versión anterior).
    try {
      await _credenciales.borrar().timeout(const Duration(seconds: 5));
    } catch (e) {
      debugPrint('No se pudieron borrar las credenciales: $e');
    }
    try {
      await firmador.borrarClaves().timeout(const Duration(seconds: 5));
    } catch (e) {
      debugPrint('No se pudieron borrar las claves del teléfono: $e');
    }
    try {
      await _bd.borrarTodo().timeout(const Duration(seconds: 10));
    } catch (e) {
      debugPrint('No se pudo vaciar la base local: $e');
    }
    protegida = false;
    bloqueada = false;
    dispositivo = null;
    _yo = null;
    _perfil = null;
    caducaEn = null;
    avisoCaducidad = null;
    avisoDirectorio = null;
    notifyListeners();
  }

  /// 401 del servidor: la clave ya no sirve. Se vuelve a la entrada sin borrar
  /// lo capturado (la cola se envía al volver a entrar con la misma cuenta).
  /// Solo cuenta si viene del cliente vigente: una petición rezagada de una
  /// sesión anterior no debe tumbar la nueva.
  void _caducar(ApiCliente cliente, ErrorApi e) {
    if (!identical(api, cliente)) return;
    api = null;
    cliente.cerrar();
    if (_soltarComoSocioYa()) unawaited(_borrarDatosDeSocio());
    avisoCaducidad = e.mensaje;
    // La clave (cifrada) y su dispositivo ya no sirven; el par de firma se
    // conserva: lo firmado en la cola sigue valiendo al registrarlo de nuevo.
    protegida = false;
    bloqueada = false;
    dispositivo = null;
    unawaited(_credenciales.borrarClave());
    notifyListeners();
  }

  // ------------------------------------------------------- modo supervisor

  /// `GET /supervisor/partners?q&type&limit&cursor` con la clave de
  /// supervisor (sin `X-Shrimp-Role`): socios aprobados con usuario de portal.
  Future<PaginaSocios> buscarSocios({String? texto, String? tipo, String? cursor, int limite = 30}) async {
    final cliente = _apiPropiaOperativa;
    if (cliente == null || !esSupervisor) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    final q = texto?.trim() ?? '';
    final p = await cliente.pagina('/supervisor/partners',
        params: {if (q.isNotEmpty) 'q': q, if (tipo != null && tipo.isNotEmpty) 'type': tipo}, cursor: cursor, limite: limite);
    return PaginaSocios(p.datos.map(SocioDirectorio.desde).whereType<SocioDirectorio>().toList(), p.siguienteCursor);
  }

  /// Ruta de "actuar como" (Anexo B.3).
  static const rutaActuarComo = '/supervisor/act-as';

  /// "Actuar como" [socio] en solo lectura: `POST /supervisor/act-as
  /// {"partner": id}` FIRMADO por este teléfono (huella / PIN, cadena
  /// canónica de la sección 3, Idempotency-Key). La clave que devuelve se
  /// guarda solo en memoria. Lanza [ErrorSeguridad] si no se confirma y
  /// [ErrorApi] si el servidor no lo acepta (necesita señal).
  Future<void> actuarComo(SocioDirectorio socio, {bool conPin = false}) async {
    final cliente = _apiPropiaOperativa;
    if (cliente == null || !esSupervisor) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    // Registro del teléfono pendiente por falta de señal: se completa antes.
    await reintentarRegistro();
    final d = dispositivo;
    if (d == null || !d.listo) {
      throw const ErrorApi(403, 'Teléfono sin registrar', '', 'device-not-registered');
    }
    var r = await _pedirActuarComo(cliente, d, socio, conPin);
    if (AccesoComoSocio.sinClave(r)) {
      // Reintento idempotente: el servidor no repite el secreto. Se pide
      // un act-as NUEVO (otra Idempotency-Key, otra firma).
      r = await _pedirActuarComo(cliente, d, socio, conPin);
    }
    final AccesoComoSocio acceso;
    try {
      acceso = AccesoComoSocio.desde(r);
    } on FormatException {
      throw const ErrorApi(500, 'Respuesta inesperada', 'El servidor no devolvió la clave de solo lectura.', 'internal-error');
    }
    final nuevo = ApiCliente(baseUrl: url, clave: acceso.clave, cliente: _http, soloLectura: true);
    nuevo.alNoAutorizado = (e) => _caducarComoSocio(nuevo, e);
    if (!identical(api, cliente)) {
      // La sesión del supervisor cambió mientras tanto: no se usa.
      await _revocarComoSocio(ComoSocio(socio: socio, cliente: nuevo, yo: Yo.desde(acceso.me)));
      return;
    }
    // Lo bajado con un socio anterior no se mezcla con este.
    final anterior = comoSocio;
    comoSocio = null;
    if (anterior != null) await _revocarComoSocio(anterior);
    await _borrarDatosDeSocio();
    final yoSocio = Yo.desde(acceso.me);
    // Vale lo que diga el servidor (8 h, nunca más que la de supervisor);
    // si no lo dice, lo mismo calculado aquí.
    var fin = acceso.caducaEn ?? _reloj().add(const Duration(hours: 8));
    final finSupervisor = caducaEn;
    if (acceso.caducaEn == null && finSupervisor != null && finSupervisor.isBefore(fin)) fin = finSupervisor;
    comoSocio = ComoSocio(
      socio: socio,
      cliente: nuevo,
      yo: yoSocio,
      caducaEn: fin,
      perfil: _perfilPara(yoSocio, null),
      vistoPor: acceso.vistoPor ?? _yo?.nombre,
    );
    avisoDirectorio = null;
    _aplicarRol();
    notifyListeners();
  }

  /// Un `POST /supervisor/act-as` firmado: Idempotency-Key y firma nuevas
  /// (huella / PIN) en cada llamada.
  Future<Map<String, dynamic>> _pedirActuarComo(ApiCliente cliente, Dispositivo d, SocioDirectorio socio, bool conPin) async {
    final cuerpo = jsonEncode({'partner': socio.id});
    final idem = _uuid.v4();
    final ts = segundosUnix(_reloj());
    final cadena =
        cadenaCanonica(metodo: 'POST', ruta: rutaActuarComo, timestamp: ts, idempotencia: idem, cuerpo: bytesDelCuerpo(cuerpo));
    final String firma;
    try {
      firma = await firmador.firmar(Uint8List.fromList(utf8.encode(cadena)),
          titulo: 'Confirma: ver la app de ${socio.nombre}',
          subtitulo: 'Solo lectura · con tu huella o el PIN del teléfono',
          conPin: conPin);
    } on ErrorSeguridad catch (e) {
      if (e.claveInvalidada) await _perderClave(e.mensaje, borrarFirma: true, cerrarEnServidor: true);
      rethrow;
    }
    return cliente.enviarSerializado('POST', rutaActuarComo, cuerpo,
        idempotencia: idem, firma: FirmaOperacion(timestamp: ts, firma: firma, huellaClave: d.huellaClave).cabeceras(d.id!));
  }

  /// "Cambiar de socio": vuelve al directorio y revoca la clave del socio
  /// (`POST /auth/logout` con esa clave; sin señal caduca sola a las 8 h).
  Future<void> cambiarDeSocio() async {
    final c = comoSocio;
    if (c == null) return;
    comoSocio = null;
    avisoDirectorio = null;
    _aplicarRol();
    notifyListeners();
    await _revocarComoSocio(c);
    await _borrarDatosDeSocio();
  }

  /// "Salir del modo supervisor": cierra la sesión del supervisor (revoca
  /// la clave del socio y la de supervisor) y borra lo del teléfono.
  Future<void> salirModoSupervisor() => cerrarSesion();

  Future<void> _revocarComoSocio(ComoSocio c) async {
    try {
      await c.cliente.cerrarSesion(idempotencia: _uuid.v4()).timeout(const Duration(seconds: 10));
    } catch (_) {
      // Sin señal o ya caducada: igual se olvida (caduca sola).
    }
    c.cliente.cerrar();
  }

  /// Suelta el socio sin revocar (la sesión del supervisor ya no sirve).
  /// Devuelve si había uno.
  bool _soltarComoSocioYa() {
    final c = comoSocio;
    if (c == null) return false;
    comoSocio = null;
    c.cliente.cerrar();
    _aplicarRol();
    return true;
  }

  Future<void> _borrarDatosDeSocio() async {
    try {
      await _bd.borrarDatosDeSocio();
    } catch (e) {
      debugPrint('No se pudo borrar lo bajado del socio: $e');
    }
  }

  /// 401 de la clave del socio (caducó a las 8 h o se revocó): vuelta al
  /// directorio, no a la entrada. Si la que caducó es la de supervisor, el
  /// directorio recibirá su propio 401 y llevará a la entrada.
  void _caducarComoSocio(ApiCliente cliente, ErrorApi e) {
    final c = comoSocio;
    if (c == null || !identical(c.cliente, cliente)) return;
    comoSocio = null;
    cliente.cerrar();
    avisoDirectorio = 'Terminó el acceso de solo lectura a ${c.nombre} (dura 8 horas). '
        'Elígelo de nuevo para seguir viendo su app.';
    _aplicarRol();
    unawaited(_borrarDatosDeSocio());
    notifyListeners();
  }

  @override
  void dispose() {
    comoSocio?.cliente.cerrar();
    api?.cerrar();
    super.dispose();
  }
}
