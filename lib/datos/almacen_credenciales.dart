import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Dirección del servidor, clave de API y dispositivo registrado, guardados
/// en el almacenamiento cifrado del teléfono.
///
/// La clave de API `trz_…` NO se guarda legible: se guarda cifrada con una
/// clave AES del Android Keystore que exige la huella o el PIN del teléfono
/// ([guardarClaveCifrada]); sin la persona no se puede descifrar. La entrada
/// `api_key` legible solo existe en teléfonos que vienen de la versión
/// anterior de la app, y se borra al pasar por "Protege tu cuenta".
class AlmacenCredenciales {
  static const _almacen = FlutterSecureStorage();
  static const _kUrl = 'servidor_url';
  static const _kClave = 'api_key'; // versión anterior: legible
  static const _kClaveCifrada = 'api_key_cifrada';
  static const _kDispositivo = 'dispositivo';

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

  /// Clave legible de la versión anterior (nula en instalaciones nuevas).
  Future<String?> clave() => _almacen.read(key: _kClave);
  Future<String?> claveCifrada() => _almacen.read(key: _kClaveCifrada);
  Future<String?> dispositivo() => _almacen.read(key: _kDispositivo);

  Future<void> guardarUrl(String url) => _almacen.write(key: _kUrl, value: normalizar(url));

  /// Guarda la clave cifrada y borra la legible (si venía de la versión anterior).
  Future<void> guardarClaveCifrada(String cifrada) async {
    await _almacen.write(key: _kClaveCifrada, value: cifrada);
    await _almacen.delete(key: _kClave);
  }

  Future<void> guardarDispositivo(String? json) =>
      json == null ? _almacen.delete(key: _kDispositivo) : _almacen.write(key: _kDispositivo, value: json);

  /// La clave (legible o cifrada) y su dispositivo: sesión caducada o
  /// teléfono revocado. El servidor se conserva para volver a entrar.
  Future<void> borrarClave() async {
    await _almacen.delete(key: _kClave);
    await _almacen.delete(key: _kClaveCifrada);
    await _almacen.delete(key: _kDispositivo);
  }

  Future<void> borrar() async {
    await _almacen.delete(key: _kUrl);
    await borrarClave();
  }
}
