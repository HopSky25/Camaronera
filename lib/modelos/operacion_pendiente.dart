/// Una operación hecha en la app (con o sin internet) que se envía al
/// servidor cuando hay señal. Se guarda en la base local y se envía en orden;
/// cada una lleva su clave de idempotencia para poder reintentar sin duplicar
/// nada (la API lo garantiza durante 24 h).
library;

enum TipoOperacion {
  // Técnico / verificadora
  iniciarCampo,
  informe,
  veredicto,
  llegadaCamion,
  asignarTecnico,
  // Comprador y vendedor
  aceptacionInforme,
  programarDespacho,
  // Empacadora
  compraVerificada,
  comprarReserva,
  // Maquilador
  ofertaMaquila,
  recepcionMaquila,
  empaqueMaquila,
  firmaActa,
}

class OperacionPendiente {
  final int? idLocal;
  final TipoOperacion tipo;

  /// Id (uuid) del recurso principal: verificación, despacho, solicitud u orden.
  final String recursoId;

  /// Para la llegada del camión desde una verificación: id del despacho.
  final String? despachoId;
  final Map<String, dynamic> cuerpo;
  final String claveIdempotencia;
  final DateTime creadaEn;
  final int intentos;
  final String? ultimoError;

  /// Perfil (`X-Shrimp-Role`) con el que se hizo la operación. Se envía con
  /// ese perfil aunque después se cambie de perfil en la app.
  final String? rol;

  const OperacionPendiente({
    this.idLocal,
    required this.tipo,
    required this.recursoId,
    this.despachoId,
    required this.cuerpo,
    required this.claveIdempotencia,
    required this.creadaEn,
    this.intentos = 0,
    this.ultimoError,
    this.rol,
  });

  /// El servidor rechazó el dato (4xx definitivo): no se reintenta sola.
  bool get conError => ultimoError != null;

  String get metodo => switch (tipo) {
        TipoOperacion.informe || TipoOperacion.programarDespacho => 'PATCH',
        _ => 'POST',
      };

  String get ruta => switch (tipo) {
        TipoOperacion.iniciarCampo => '/verifications/$recursoId:start',
        TipoOperacion.informe => '/verifications/$recursoId/report',
        TipoOperacion.veredicto => '/verifications/$recursoId:verdict',
        TipoOperacion.llegadaCamion => '/dispatches/$despachoId/arrival',
        TipoOperacion.asignarTecnico => '/verifications/$recursoId:assign',
        TipoOperacion.aceptacionInforme => '/verifications/$recursoId/acceptance',
        TipoOperacion.programarDespacho => '/dispatches/$recursoId',
        TipoOperacion.compraVerificada => '/verifications',
        TipoOperacion.comprarReserva => '/harvest/commitments/$recursoId:buy',
        TipoOperacion.ofertaMaquila => '/copack/requests/$recursoId/offers',
        TipoOperacion.recepcionMaquila => '/copack/orders/$recursoId:reception',
        TipoOperacion.empaqueMaquila => '/copack/orders/$recursoId:packing',
        TipoOperacion.firmaActa => '/copack/orders/$recursoId:sign',
      };

  String get descripcion => switch (tipo) {
        TipoOperacion.iniciarCampo => 'Inicio de campo',
        TipoOperacion.informe => 'Informe de campo',
        TipoOperacion.veredicto => 'Veredicto',
        TipoOperacion.llegadaCamion => 'Llegada del camión',
        TipoOperacion.asignarTecnico => 'Asignación de técnico',
        TipoOperacion.aceptacionInforme => 'Decisión sobre el informe',
        TipoOperacion.programarDespacho => 'Programación del despacho',
        TipoOperacion.compraVerificada => 'Compra con verificación',
        TipoOperacion.comprarReserva => 'Compra del lote reservado',
        TipoOperacion.ofertaMaquila => 'Oferta de empaque',
        TipoOperacion.recepcionMaquila => 'Recepción en planta',
        TipoOperacion.empaqueMaquila => 'Empaque terminado',
        TipoOperacion.firmaActa => 'Firma del acta',
      };

  /// La respuesta de estas operaciones es la verificación al día: se guarda
  /// en la bandeja local.
  bool get respondeVerificacion => const {
        TipoOperacion.iniciarCampo,
        TipoOperacion.informe,
        TipoOperacion.veredicto,
        TipoOperacion.asignarTecnico,
        TipoOperacion.aceptacionInforme,
      }.contains(tipo);
}
