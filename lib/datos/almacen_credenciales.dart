import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Dirección del servidor y clave de API, guardadas cifradas en el teléfono.
/// La clave la devuelve el inicio de sesión por cuenta (`POST /auth/login`)
/// o la pega el usuario si entra con una clave creada en el portal.
class AlmacenCredenciales {
  static const _almacen = FlutterSecureStorage();
  static const _kUrl = 'servidor_url';
  static const _kClave = 'api_key';

  /// `demo.camaronmarket.com/` → `https://demo.camaronmarket.com`
  static String normalizar(String url) {
    var limpia = url.trim();
    while (limpia.endsWith('/')) {
      limpia = limpia.substring(0, limpia.length - 1);
    }
    if (!limpia.startsWith('http')) limpia = 'https://$limpia';
    return limpia;
  }

  Future<String?> url() => _almacen.read(key: _kUrl);
  Future<String?> clave() => _almacen.read(key: _kClave);

  Future<void> guardar({required String url, required String clave}) async {
    await _almacen.write(key: _kUrl, value: normalizar(url));
    await _almacen.write(key: _kClave, value: clave.trim());
  }

  /// Solo la clave (sesión caducada): el servidor se conserva para volver a entrar.
  Future<void> borrarClave() => _almacen.delete(key: _kClave);

  Future<void> borrar() async {
    await _almacen.delete(key: _kUrl);
    await _almacen.delete(key: _kClave);
  }
}
