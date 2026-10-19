/// Lo de la cuenta del vendedor que antes solo se hacía en el portal:
/// instalaciones y piscinas o tanques (`/facilities`, `/ponds`) y «mis
/// certificados» (`/me/certificates`). Son en línea y sin firma: no cambian
/// qué se vende ni a qué precio (los certificados, además, los revisa la
/// plataforma antes de que cuenten). Sin señal lanzan [SinConexion].
library;

import 'dart:typed_data';

import 'package:uuid/uuid.dart';

import '../modelos/gestion_lote.dart';
import 'api_cliente.dart';
import 'sincronizador.dart';

const _uuid = Uuid();

extension CuentaVendedor on Sincronizador {
  ApiCliente _lectura() {
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    return api;
  }

  ApiCliente _escritura() {
    if (sesion.soloLectura) throw const ErrorSoloLectura();
    return _lectura();
  }

  // ---------------- instalaciones y piscinas ----------------

  Future<List<Map<String, dynamic>>> instalaciones({bool archivadas = false}) async =>
      _lectura().listarTodo('/facilities', params: archivadas ? const {'include_archived': 'true'} : null);

  Future<List<Map<String, dynamic>>> piscinas({bool archivadas = false}) async =>
      _lectura().listarTodo('/ponds', params: archivadas ? const {'include_archived': 'true'} : null);

  Future<Map<String, dynamic>> crearInstalacion(Map<String, dynamic> cuerpo, {required String idempotencia}) async =>
      _escritura().post('/facilities', cuerpo, idempotencia: idempotencia, rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> editarInstalacion(String id, Map<String, dynamic> cambios) async =>
      _escritura().patch('/facilities/$id', cambios, idempotencia: _uuid.v4(), rol: sesion.rolParaApi);

  /// Archiva la instalación y sus piscinas o tanques (siguen en el historial).
  Future<Map<String, dynamic>> archivarInstalacion(String id) async =>
      _escritura().delete('/facilities/$id', rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> crearPiscina(Map<String, dynamic> cuerpo, {required String idempotencia}) async =>
      _escritura().post('/ponds', cuerpo, idempotencia: idempotencia, rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> editarPiscina(String id, Map<String, dynamic> cambios) async =>
      _escritura().patch('/ponds/$id', cambios, idempotencia: _uuid.v4(), rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> archivarPiscina(String id) async => _escritura().delete('/ponds/$id', rol: sesion.rolParaApi);

  Future<Uint8List> bajarFotoPiscina(String id) async => _lectura().bytes('/ponds/$id/image');

  // ---------------- mis certificados ----------------

  /// `{data, catalog}`: los de la cuenta (con su revisión) y el catálogo.
  Future<Map<String, dynamic>> misCertificados() async => _lectura().get('/me/certificates');

  Future<Map<String, dynamic>> subirMiCertificado(CertificadoNuevo c, {required String idempotencia}) async =>
      _escritura().post('/me/certificates', c.aCuerpoApi(), idempotencia: idempotencia, rol: sesion.rolParaApi);

  /// Renovar o corregir: vuelve a revisión.
  Future<Map<String, dynamic>> renovarMiCertificado(String id, CertificadoNuevo c) async =>
      _escritura().patch('/me/certificates/$id', c.aCuerpoEdicion(), idempotencia: _uuid.v4(), rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> quitarMiCertificado(String id) async =>
      _escritura().delete('/me/certificates/$id', rol: sesion.rolParaApi);
}
