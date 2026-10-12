import 'dart:convert';
import 'dart:io';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';

import '../modelos/perfil.dart';
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
///   cabecera `X-Shrimp-Role` de cada petición. El valor inicial es
///   `me.active_role`.
/// - Con un 401 (clave caducada o revocada) la sesión caduca: se vuelve a la
///   pantalla de entrada pero NO se borra lo capturado en el teléfono.
class Sesion extends ChangeNotifier {
  final AlmacenCredenciales _credenciales;
  final BaseLocal _bd;
  final _uuid = const Uuid();

  ApiCliente? api;
  Yo? yo;

  /// Perfil con el que se trabaja: un rol de la plataforma o [perfilTecnico].
  /// Nulo cuando hay varios aprobados y todavía no se ha elegido.
  String? perfil;
  bool cargando = true;

  /// Mensaje que se muestra en la pantalla de entrada tras caducar la sesión.
  String? avisoCaducidad;
  String url = 'https://demo.camaronmarket.com';

  Sesion({AlmacenCredenciales? credenciales, BaseLocal? bd})
      : _credenciales = credenciales ?? AlmacenCredenciales(),
        _bd = bd ?? BaseLocal();

  bool get conectado => api != null;
  bool get necesitaElegirPerfil => conectado && perfil == null;
  bool get esTecnico => perfil == perfilTecnico;
  String get nombre => yo?.nombre ?? 'Cuenta conectada';

  /// Al abrir la app: lee lo guardado y, si hay clave, refresca `me` en segundo plano.
  Future<void> iniciar() async {
    final urlGuardada = await _credenciales.url();
    final clave = await _credenciales.clave();
    final yoGuardado = await _bd.leerConfig('yo');
    if (yoGuardado != null) yo = Yo.desde(jsonDecode(yoGuardado) as Map<String, dynamic>);
    perfil = await _bd.leerConfig('perfil');
    if (urlGuardada != null) url = urlGuardada;
    if (urlGuardada != null && clave != null && clave.isNotEmpty) {
      api = _nuevoCliente(urlGuardada, clave);
      _aplicarRol();
      if (yo == null || perfil == null) {
        // Sesión guardada de la versión anterior (solo clave): se completa con /me.
        try {
          await _recibir(await api!.quienSoy());
        } catch (_) {
          // Sin señal o sin permiso: se sigue con lo guardado.
        }
      } else {
        _refrescarYo();
      }
    }
    cargando = false;
    notifyListeners();
  }

  ApiCliente _nuevoCliente(String url, String clave) =>
      ApiCliente(baseUrl: url, clave: clave)..alNoAutorizado = _caducar;

  /// El técnico de campo no "actúa como" ningún perfil: la cuenta ya es suya.
  void _aplicarRol() => api?.rolActivo = (perfil == null || perfil == perfilTecnico) ? null : perfil;

  Future<void> _refrescarYo() async {
    try {
      final datos = await api!.quienSoy();
      yo = Yo.desde(datos);
      await _bd.escribirConfig('yo', jsonEncode(datos));
      notifyListeners();
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
      idempotencia: _uuid.v4(),
    );
    final clave = resp['api_key'] as String?;
    if (clave == null || clave.isEmpty) {
      throw const ErrorApi(500, 'Respuesta inesperada', 'El servidor no devolvió la clave de sesión.', 'internal-error');
    }
    await _credenciales.guardar(url: limpia, clave: clave);
    this.url = limpia;
    api = _nuevoCliente(limpia, clave);
    final me = resp['me'];
    await _recibir(me is Map<String, dynamic> ? me : await api!.quienSoy());
  }

  /// Opción avanzada: entrar con una clave de API creada en el portal.
  Future<void> entrarConClave({required String url, required String clave}) async {
    final limpia = AlmacenCredenciales.normalizar(url);
    final cliente = _nuevoCliente(limpia, clave.trim());
    final me = await cliente.quienSoy();
    await _credenciales.guardar(url: limpia, clave: clave);
    this.url = limpia;
    api = cliente;
    await _recibir(me);
  }

  /// Guarda `me` y decide el perfil: técnico → directo; un aprobado → directo;
  /// varios → hay que elegir.
  Future<void> _recibir(Map<String, dynamic> me) async {
    yo = Yo.desde(me);
    avisoCaducidad = null;
    await _bd.escribirConfig('yo', jsonEncode(me));
    if (yo!.esTecnico) {
      perfil = perfilTecnico;
    } else {
      final aprobados = yo!.aprobados;
      final activo = yo!.perfilActivo;
      if (aprobados.length == 1) {
        perfil = aprobados.first.rol;
      } else if (perfil != null && !aprobados.any((p) => p.rol == perfil)) {
        perfil = null;
      } else if (perfil == null && aprobados.isEmpty) {
        // Sin perfiles aprobados: se deja entrar con el que diga el servidor
        // para que al menos vea el aviso.
        perfil = activo;
      }
      // Con varios aprobados y ninguno elegido todavía se pregunta; el que
      // la cuenta tiene activo en el portal aparece marcado como sugerido.
    }
    await _bd.escribirConfig('perfil', perfil);
    _aplicarRol();
    notifyListeners();
  }

  /// Elegir (o cambiar) el perfil de trabajo. Se guarda en el teléfono y se
  /// manda en `X-Shrimp-Role`; no se toca el perfil activo de la cuenta.
  Future<void> elegirPerfil(String rol) async {
    perfil = rol;
    await _bd.escribirConfig('perfil', rol);
    await _bd.borrarCache();
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
  /// todo lo guardado en el teléfono.
  Future<void> cerrarSesion() async {
    final cliente = api;
    api = null;
    if (cliente != null) {
      try {
        await cliente.cerrarSesion(idempotencia: _uuid.v4());
      } catch (_) {
        // Sin señal o clave ya inválida: igual se sale de la app.
      }
      cliente.cerrar();
    }
    await _credenciales.borrar();
    await _bd.borrarTodo();
    yo = null;
    perfil = null;
    avisoCaducidad = null;
    notifyListeners();
  }

  /// 401 del servidor: la clave ya no sirve. Se vuelve a la entrada sin borrar
  /// lo capturado (la cola se envía al volver a entrar).
  void _caducar(ErrorApi e) {
    if (api == null) return;
    api?.cerrar();
    api = null;
    avisoCaducidad = e.mensaje;
    _credenciales.borrarClave();
    notifyListeners();
  }

  @override
  void dispose() {
    api?.cerrar();
    super.dispose();
  }
}
