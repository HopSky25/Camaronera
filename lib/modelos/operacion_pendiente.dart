/// Una operación que el técnico hizo sin internet y que la app enviará al
/// servidor cuando vuelva la señal. Se guarda en la base local y se envía en
/// orden; cada una lleva su clave de idempotencia para poder reintentar sin
/// duplicar nada (la API lo garantiza durante 24 h).
library;

enum TipoOperacion { iniciarCampo, informe, veredicto, llegadaCamion }

class OperacionPendiente {
  final int? idLocal;
  final TipoOperacion tipo;
  final String verificacionId;
  /// Para la llegada del camión: id del despacho.
  final String? despachoId;
  final Map<String, dynamic> cuerpo;
  final String claveIdempotencia;
  final DateTime creadaEn;
  final int intentos;
  final String? ultimoError;

  const OperacionPendiente({
    this.idLocal,
    required this.tipo,
    required this.verificacionId,
    this.despachoId,
    required this.cuerpo,
    required this.claveIdempotencia,
    required this.creadaEn,
    this.intentos = 0,
    this.ultimoError,
  });

  String get metodo => tipo == TipoOperacion.informe ? 'PATCH' : 'POST';

  String get ruta => switch (tipo) {
        TipoOperacion.iniciarCampo => '/verifications/$verificacionId:start',
        TipoOperacion.informe => '/verifications/$verificacionId/report',
        TipoOperacion.veredicto => '/verifications/$verificacionId:verdict',
        TipoOperacion.llegadaCamion => '/dispatches/$despachoId/arrival',
      };

  String get descripcion => switch (tipo) {
        TipoOperacion.iniciarCampo => 'Inicio de campo',
        TipoOperacion.informe => 'Informe de campo',
        TipoOperacion.veredicto => 'Veredicto',
        TipoOperacion.llegadaCamion => 'Llegada del camión',
      };

  OperacionPendiente conError(String error) => OperacionPendiente(
        idLocal: idLocal,
        tipo: tipo,
        verificacionId: verificacionId,
        despachoId: despachoId,
        cuerpo: cuerpo,
        claveIdempotencia: claveIdempotencia,
        creadaEn: creadaEn,
        intentos: intentos + 1,
        ultimoError: error,
      );
}
