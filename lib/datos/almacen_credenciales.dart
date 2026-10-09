import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Dirección del servidor y clave de API del técnico, guardadas cifradas en el
/// teléfono. La clave la entrega la empresa verificadora (una por técnico).
class AlmacenCredenciales {
  static const _almacen = FlutterSecureStorage();
  static const _kUrl = 'servidor_url';
  static const _kClave = 'api_key';

  Future<String?> url() => _almacen.read(key: _kUrl);
  Future<String?> clave() => _almacen.read(key: _kClave);

  Future<void> guardar({required String url, required String clave}) async {
    var limpia = url.trim();
    if (limpia.endsWith('/')) limpia = limpia.substring(0, limpia.length - 1);
    if (!limpia.startsWith('http')) limpia = 'https://$limpia';
    await _almacen.write(key: _kUrl, value: limpia);
    await _almacen.write(key: _kClave, value: clave.trim());
  }

  Future<void> borrar() async {
    await _almacen.delete(key: _kUrl);
    await _almacen.delete(key: _kClave);
  }
}
