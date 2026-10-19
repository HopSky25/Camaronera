/// Lotes del vendedor (naupliero, laboratorio, camaronera) desde la app:
/// crear el borrador, editarlo, sus fotos y certificados, retirarlo,
/// reactivarlo y eliminar un borrador. Son en línea: sin señal lanzan
/// [SinConexion] y no se guarda nada en el teléfono.
///
/// Van firmadas con la huella (como publicar, Sincronizador.firmarPublicacionLote)
/// solo las operaciones comerciales: cambiar el precio, la cantidad o la
/// unidad de un lote publicado ([firmarEdicionLote]) y reactivar uno
/// retirado ([firmarReactivacionLote]). Las fotos, los certificados, el
/// borrador y retirarlo no se firman.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:uuid/uuid.dart';

import '../modelos/gestion_lote.dart';
import '../modelos/operacion_pendiente.dart';
import 'api_cliente.dart';
import 'sincronizador.dart';

const _uuid = Uuid();

extension LotesVendedor on Sincronizador {
  ApiCliente _apiEscritura() {
    if (sesion.soloLectura) throw const ErrorSoloLectura();
    return _apiLectura();
  }

  ApiCliente _apiLectura() {
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    return api;
  }

  /// `POST /products`: el lote nace en borrador. [idempotencia] se repite
  /// en el reintento del MISMO cuerpo (no se crean dos borradores si se
  /// cortó la señal con la respuesta en camino).
  Future<Map<String, dynamic>> crearBorradorLote(Map<String, dynamic> cuerpo, {required String idempotencia}) async =>
      _apiEscritura().post('/products', cuerpo, idempotencia: idempotencia, rol: sesion.rolParaApi);

  /// `PATCH /products/{id}` con solo lo que cambió: un borrador, o lo que
  /// no es comercial de un lote publicado (sin firma). El servidor responde
  /// 422 con el campo si algo está bloqueado por compras.
  Future<Map<String, dynamic>> editarLote(String productoId, Map<String, dynamic> cambios, {required String idempotencia}) async =>
      _apiEscritura().patch('/products/$productoId', cambios, idempotencia: idempotencia, rol: sesion.rolParaApi);

  /// Cambiar precio, cantidad o unidad de un lote publicado
  /// (`POST /products/{id}:update-terms`, firmado). Como publicar: no va a
  /// la cola y el reintento manda la MISMA operación.
  Future<OperacionPendiente> firmarEdicionLote(String productoId, Map<String, dynamic> cambios, {bool conPin = false}) =>
      _firmar(TipoOperacion.editarLote, productoId, jsonEncode(cambios), conPin: conPin);

  /// Reactivar un lote retirado (`POST /products/{id}:unarchive`, firmado).
  /// Vuelve en borrador; para venderlo hay que publicarlo otra vez.
  Future<OperacionPendiente> firmarReactivacionLote(String productoId, {bool conPin = false}) =>
      _firmar(TipoOperacion.reactivarLote, productoId, '{}', conPin: conPin);

  Future<OperacionPendiente> _firmar(TipoOperacion tipo, String productoId, String cuerpo, {bool conPin = false}) async {
    if (sesion.soloLectura) throw const ErrorSoloLectura();
    final op = OperacionPendiente(
      tipo: tipo,
      recursoId: productoId,
      cuerpoSerializado: cuerpo,
      claveIdempotencia: _uuid.v4(),
      creadaEn: DateTime.now(),
      rol: sesion.rolParaApi,
    );
    return op.conFirma(await sesion.firmarOperacion(op, conPin: conPin));
  }

  /// `POST /products/{id}:archive`: sale del marketplace y queda archivado
  /// (se reactiva desde «Mis lotes › Archivados»). El servidor lo impide con
  /// compras, verificaciones o reservas abiertas (422 con el motivo).
  Future<Map<String, dynamic>> archivarLote(String productoId, {required String idempotencia}) async =>
      _apiEscritura().post('/products/$productoId:archive', const {}, idempotencia: idempotencia, rol: sesion.rolParaApi);

  /// `DELETE /products/{id}`: elimina un borrador (borrado lógico: queda en
  /// el histórico, pero no se reactiva).
  Future<Map<String, dynamic>> eliminarBorradorLote(String productoId) async =>
      _apiEscritura().delete('/products/$productoId', rol: sesion.rolParaApi);

  // ---------------- fotos ----------------

  Future<List<Map<String, dynamic>>> fotosLote(String productoId) async =>
      fotosDeLote(await _apiLectura().get('/products/$productoId/photos'));

  /// El archivo de una foto ([miniatura]: 480 px como máximo).
  Future<Uint8List> bajarFotoLote(String productoId, String fotoId, {bool miniatura = true}) async =>
      _apiLectura().bytes('/products/$productoId/photos/$fotoId', params: miniatura ? const {'thumb': '1'} : null);

  /// Sube una foto YA comprimida ([prepararFotoLote]). Devuelve las fotos
  /// en su orden.
  Future<List<Map<String, dynamic>>> subirFotoLote(String productoId, Uint8List jpeg,
          {required String nombreArchivo, bool portada = false, required String idempotencia}) async =>
      fotosDeLote(await _apiEscritura().post('/products/$productoId/photos',
          cuerpoFotoLote(nombreArchivo: nombreArchivo, jpeg: jpeg, portada: portada),
          idempotencia: idempotencia, rol: sesion.rolParaApi));

  /// Nuevo orden (la primera es la portada).
  Future<List<Map<String, dynamic>>> ordenarFotosLote(String productoId, List<String> orden, {required String idempotencia}) async =>
      fotosDeLote(await _apiEscritura().post('/products/$productoId/photos:reorder', {'order': orden},
          idempotencia: idempotencia, rol: sesion.rolParaApi));

  Future<List<Map<String, dynamic>>> quitarFotoLote(String productoId, String fotoId) async =>
      fotosDeLote(await _apiEscritura().delete('/products/$productoId/photos/$fotoId', rol: sesion.rolParaApi));

  // ---------------- certificados ----------------

  /// `{data, available, catalog}`: los del lote, los vigentes de la cuenta
  /// que se pueden ligar y el catálogo para subir uno propio.
  Future<Map<String, dynamic>> certificadosLote(String productoId) async => _apiLectura().get('/products/$productoId/certificates');

  Future<Map<String, dynamic>> ligarCertificadoLote(String productoId, String certificadoCuentaId, {required String idempotencia}) async =>
      _apiEscritura().post('/products/$productoId/certificates', {'account_certificate': certificadoCuentaId},
          idempotencia: idempotencia, rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> subirCertificadoLote(String productoId, CertificadoNuevo c, {required String idempotencia}) async =>
      _apiEscritura().post('/products/$productoId/certificates', c.aCuerpoApi(), idempotencia: idempotencia, rol: sesion.rolParaApi);

  /// Corrige número, fechas o archivo de un certificado PROPIO del lote
  /// (vuelve a revisión).
  Future<Map<String, dynamic>> editarCertificadoLote(String productoId, String lineaId, CertificadoNuevo c) async =>
      _apiEscritura().patch('/products/$productoId/certificates/$lineaId', c.aCuerpoEdicion(),
          idempotencia: _uuid.v4(), rol: sesion.rolParaApi);

  Future<Map<String, dynamic>> quitarCertificadoLote(String productoId, String lineaId) async =>
      _apiEscritura().delete('/products/$productoId/certificates/$lineaId', rol: sesion.rolParaApi);
}
