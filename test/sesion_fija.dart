import 'package:http/http.dart' as http;
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/sesion.dart';

import 'almacen_falso.dart';
import 'firmador_falso.dart';
import 'sesion_test.dart' show BaseFalsa;

/// Sesión ya abierta (protegida y con perfil) contra un cliente HTTP falso,
/// sin pasar por login ni huella: para probar servicios y pantallas.
class SesionFija extends Sesion {
  final ApiCliente cliente;
  bool supervisor;
  String? perfilFijo;
  bool abierta = true;

  SesionFija(http.Client http, {this.supervisor = false, this.perfilFijo = 'empacadora'})
      : cliente = ApiCliente(baseUrl: 'https://demo.camaronmarket.com', clave: 'trz_app', cliente: http, soloLectura: supervisor),
        super(credenciales: AlmacenFalso(), bd: BaseFalsa(), firmador: FirmadorFalso()) {
    cargando = false;
  }

  @override
  ApiCliente? get apiOperativa => abierta ? cliente : null;
  @override
  bool get conectado => abierta;
  @override
  bool get soloLectura => supervisor;
  @override
  bool get esSupervisor => supervisor;
  @override
  String? get perfil => perfilFijo;
  @override
  bool get necesitaProteger => false;
  @override
  bool get necesitaElegirPerfil => abierta && perfilFijo == null;

  /// Cierra la sesión sin tocar nada más (para probar "fuera").
  void cerrar() {
    abierta = false;
    notifyListeners();
  }

  void avisar() => notifyListeners();
}
