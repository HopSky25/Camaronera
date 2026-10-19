/// Lotes del vendedor (naupliero, laboratorio, camaronera) desde la app:
/// crear el borrador y retirarlo del marketplace. Ninguna de las dos rutas
/// exige firma (solo publicar: Sincronizador.firmarPublicacionLote). Son en
/// línea: sin señal lanzan [SinConexion] y no se guarda nada en el teléfono.
library;

import 'api_cliente.dart';
import 'sincronizador.dart';

extension LotesVendedor on Sincronizador {
  ApiCliente _apiEscritura() {
    if (sesion.soloLectura) throw const ErrorSoloLectura();
    final api = sesion.apiOperativa;
    if (api == null) throw const ErrorApi(401, 'Sin sesión', 'Vuelve a entrar.', 'expired-api-key');
    return api;
  }

  /// `POST /products`: el lote nace en borrador. [idempotencia] se repite
  /// en el reintento del MISMO cuerpo (no se crean dos borradores si se
  /// cortó la señal con la respuesta en camino).
  Future<Map<String, dynamic>> crearBorradorLote(Map<String, dynamic> cuerpo, {required String idempotencia}) async =>
      _apiEscritura().post('/products', cuerpo, idempotencia: idempotencia, rol: sesion.rolParaApi);

  /// `POST /products/{id}:archive`: sale del marketplace y queda archivado
  /// (se reactiva desde el portal). El servidor lo impide con compras,
  /// verificaciones o reservas abiertas (422 con el motivo).
  Future<Map<String, dynamic>> archivarLote(String productoId, {required String idempotencia}) async =>
      _apiEscritura().post('/products/$productoId:archive', const {}, idempotencia: idempotencia, rol: sesion.rolParaApi);
}
