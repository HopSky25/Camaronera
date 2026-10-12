import 'package:verificador_campo/datos/almacen_credenciales.dart';

/// Credenciales en memoria (en el teléfono van en el almacenamiento cifrado).
class AlmacenFalso extends AlmacenCredenciales {
  String? urlGuardada;

  /// Clave legible: solo la deja la versión anterior de la app.
  String? claveGuardada;
  String? claveCifradaGuardada;
  String? dispositivoGuardado;
  @override
  Future<String?> url() async => urlGuardada;
  @override
  Future<String?> clave() async => claveGuardada;
  @override
  Future<String?> claveCifrada() async => claveCifradaGuardada;
  @override
  Future<String?> dispositivo() async => dispositivoGuardado;
  @override
  Future<void> guardarUrl(String url) async => urlGuardada = AlmacenCredenciales.normalizar(url);
  @override
  Future<void> guardarClaveCifrada(String cifrada) async {
    claveCifradaGuardada = cifrada;
    claveGuardada = null;
  }

  @override
  Future<void> guardarDispositivo(String? json) async => dispositivoGuardado = json;

  @override
  Future<void> borrarClave() async {
    claveGuardada = null;
    claveCifradaGuardada = null;
    dispositivoGuardado = null;
  }

  @override
  Future<void> borrar() async {
    urlGuardada = null;
    await borrarClave();
  }
}
