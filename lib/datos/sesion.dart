import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

import '../modelos/perfil.dart';
import '../modelos/verificacion.dart' show jsonTexto;
import 'almacen_credenciales.dart';
import 'api_cliente.dart';
import 'base_local.dart';

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
class Sesion extends ChangeNotifier {
  final AlmacenCredenciales _credenciales;
  final BaseLocal _bd;

  /// Cliente HTTP compartido (en pruebas, uno falso).
  final http.Client? _http;
  final _uuid = const Uuid();

  ApiCliente? api;
  Yo? yo;

  /// Perfil con el que se trabaja: un rol de la plataforma o [perfilTecnico].
  /// Nulo cuando hay varios aprobados y todavía no se ha elegido.
  String? perfil;
  bool cargando = true;

  /// Mensaje que se muestra en la pantalla de entrada tras caducar la sesión.
  String? avisoCaducidad;

  /// Cuándo caduca la clave de esta sesión (`expires_at` del login o de
  /// `me.api_key`). Nulo si no se sabe.
  DateTime? caducaEn;

  /// Aviso para la cabecera cuando quedan menos de 15 días de sesión.
  String? get avisoCaducidadProxima {
    final fin = caducaEn;
    if (fin == null || !conectado) return null;
    final dias = fin.difference(DateTime.now()).inDays;
    if (dias > 15) return null;
    if (dias <= 0) return 'La sesión caduca hoy: cierra sesión y vuelve a entrar para renovarla.';
    return 'La sesión caduca en $dias ${dias == 1 ? 'día' : 'días'}: cierra sesión y vuelve a entrar para renovarla.';
  }
  String url = 'https://demo.camaronmarket.com';

  Sesion({AlmacenCredenciales? credenciales, BaseLocal? bd, http.Client? clienteHttp})
      : _credenciales = credenciales ?? AlmacenCredenciales(),
        _bd = bd ?? BaseLocal(),
        _http = clienteHttp;

  bool get conectado => api != null;
  bool get necesitaElegirPerfil => conectado && perfil == null;
  bool get esTecnico => perfil == perfilTecnico;
  String get nombre => yo?.nombre ?? 'Cuenta conectada';

  /// Valor de `X-Shrimp-Role`: el perfil elegido o, para el técnico de
  /// campo, el perfil activo de su cuenta (siempre válido para el servidor).
  String? get rolParaApi => perfil == perfilTecnico ? yo?.perfilActivo : perfil;

  /// Al abrir la app: lee lo guardado y, si hay clave, refresca `me`.
  Future<void> iniciar() async {
    try {
      final urlGuardada = await _credenciales.url();
      final clave = await _credenciales.clave();
      final yoGuardado = await _bd.leerConfig('yo');
      if (yoGuardado != null) {
        try {
          yo = Yo.desde(jsonDecode(yoGuardado) as Map<String, dynamic>);
        } catch (_) {
          yo = null; // guardado dañado: se vuelve a pedir
        }
      }
      perfil = await _bd.leerConfig('perfil');
      caducaEn = DateTime.tryParse(await _bd.leerConfig('caduca_en') ?? '')?.toLocal();
      if (urlGuardada != null) url = urlGuardada;
      if (urlGuardada != null && clave != null && clave.isNotEmpty) {
        api = _nuevoCliente(urlGuardada, clave);
        _aplicarRol();
        if (yo == null || perfil == null) {
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
      notifyListeners();
    }
  }

  ApiCliente _nuevoCliente(String url, String clave) {
    final cliente = ApiCliente(baseUrl: url, clave: clave, cliente: _http);
    cliente.alNoAutorizado = (e) => _caducar(cliente, e);
    return cliente;
  }

  void _aplicarRol() => api?.rolActivo = rolParaApi;

  Future<void> _refrescarYo() async {
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
    await _abrir(limpia, clave, me is Map<String, dynamic> ? me : null, caduca: DateTime.tryParse(jsonTexto(resp['expires_at']) ?? ''));
  }

  /// Opción avanzada: entrar con una clave de API creada en el portal.
  Future<void> entrarConClave({required String url, required String clave}) =>
      _abrir(AlmacenCredenciales.normalizar(url), clave.trim(), null);

  /// Comprueba la clave (si no vino `me`, lo pide), guarda las credenciales
  /// y deja la sesión abierta.
  Future<void> _abrir(String url, String clave, Map<String, dynamic>? me, {DateTime? caduca}) async {
    final cliente = _nuevoCliente(url, clave);
    final datos = me ?? await cliente.quienSoy();
    await _siCambiaDeCuenta(datos);
    await _credenciales.guardar(url: url, clave: clave);
    this.url = url;
    api?.cerrar();
    api = cliente;
    caducaEn = caduca?.toLocal();
    await _recibir(datos);
  }

  /// Si entra una cuenta distinta de la que dejó datos en el teléfono, se
  /// borra todo lo de la anterior (su cola no se puede enviar con otra clave).
  Future<void> _siCambiaDeCuenta(Map<String, dynamic> me) async {
    final anterior = yo?.id ?? '';
    final nuevo = jsonTexto(me['id']) ?? '';
    if (anterior.isNotEmpty && nuevo.isNotEmpty && anterior != nuevo) {
      await _bd.borrarTodo();
      yo = null;
      perfil = null;
    }
  }

  /// Guarda `me` y decide el perfil: técnico → directo; un aprobado → directo;
  /// varios → hay que elegir (o se conserva el ya elegido si sigue aprobado).
  Future<void> _recibir(Map<String, dynamic> me) async {
    yo = Yo.desde(me);
    avisoCaducidad = null;
    await _bd.escribirConfig('yo', jsonEncode(me));
    final claveApi = me['api_key'];
    final expira = claveApi is Map ? DateTime.tryParse(jsonTexto(claveApi['expires_at']) ?? '') : null;
    if (expira != null) caducaEn = expira.toLocal();
    await _bd.escribirConfig('caduca_en', caducaEn?.toUtc().toIso8601String());
    if (yo!.esTecnico) {
      perfil = perfilTecnico;
    } else {
      final aprobados = yo!.aprobados;
      if (aprobados.length == 1) {
        perfil = aprobados.first.rol;
      } else if (perfil != null && !aprobados.any((p) => p.rol == perfil)) {
        perfil = null; // ya no está aprobado (o era "técnico"): se vuelve a elegir
      } else if (perfil == null && aprobados.isEmpty) {
        // Sin perfiles aprobados: se deja entrar con el que diga el servidor
        // para que al menos vea el aviso.
        perfil = yo!.perfilActivo;
      }
      // Con varios aprobados y ninguno elegido todavía se pregunta; el que
      // la cuenta tiene activo en el portal aparece marcado como sugerido.
    }
    await _bd.escribirConfig('perfil', perfil);
    _aplicarRol();
    notifyListeners();
  }

  /// Elegir (o cambiar) el perfil de trabajo. Se guarda en el teléfono, se
  /// manda en `X-Shrimp-Role` y se vacía la caché de listas del perfil
  /// anterior; no se toca el perfil activo de la cuenta en el portal.
  Future<void> elegirPerfil(String rol) async {
    if (rol != perfil) await _bd.borrarCache();
    perfil = rol;
    await _bd.escribirConfig('perfil', rol);
    _aplicarRol();
    notifyListeners();
  }

  /// Quita el perfil elegido para volver a la pantalla de elección.
  Future<void> volverAElegirPerfil() async {
    perfil = null;
    await _bd.escribirConfig('perfil', null);
    _aplicarRol();
    notifyListeners();
  }

  /// Cierra la sesión: revoca la clave en el servidor (si se puede) y borra
  /// todo lo guardado en el teléfono (credenciales, bandeja, cola, fotos).
  Future<void> cerrarSesion() async {
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
    await _credenciales.borrar();
    await _bd.borrarTodo();
    yo = null;
    perfil = null;
    caducaEn = null;
    avisoCaducidad = null;
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
    avisoCaducidad = e.mensaje;
    unawaited(_credenciales.borrarClave());
    notifyListeners();
  }

  @override
  void dispose() {
    api?.cerrar();
    super.dispose();
  }
}
