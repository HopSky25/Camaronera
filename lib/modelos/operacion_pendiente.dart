/// Una operación hecha en la app (con o sin internet) que se envía al
/// servidor cuando hay señal. Se guarda en la base local y se envía en orden;
/// cada una lleva su clave de idempotencia para poder reintentar sin duplicar
/// nada (la API lo garantiza durante 24 h).
///
/// Operaciones sensibles (todas las de la app, ver `esRutaSensible`): el
/// cuerpo se serializa UNA vez al hacer la acción, se firma con la huella o
/// el PIN en ese momento (en campo, sin internet) y se guarda tal cual junto
/// con la firma. Al enviar se mandan exactamente esos bytes y esas cabeceras.
library;

import 'dart:convert';

import '../seguridad/firma_operacion.dart';

enum TipoOperacion {
  // Técnico / verificadora
  iniciarCampo,
  informe,
  veredicto,
  llegadaCamion,
  asignarTecnico,
  fotoCampo,
  // Comprador y vendedor
  aceptacionInforme,
  programarDespacho,
  // Empacadora
  compraVerificada,
  comprarReserva,
  // Compra directa (larva, nauplio): no pasa por la cola, se firma y se envía
  // en el momento (Sincronizador.firmarCompraDirecta).
  compraDirecta,
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

  /// Cuerpo JSON YA SERIALIZADO: son los bytes (UTF-8) que se firmaron y los
  /// que se envían. Nunca se vuelve a serializar.
  final String cuerpoSerializado;
  final String claveIdempotencia;
  final DateTime creadaEn;
  final int intentos;
  final String? ultimoError;

  /// Tipo del último error (slug RFC 9457, p. ej. `signature-expired`, o
  /// `firma-pendiente` cuando la app no la envía por falta de firma).
  final String? ultimoErrorTipo;

  /// Firma hecha en el momento de la acción (nula en operaciones de una
  /// versión anterior de la app o de una clave de integración).
  final FirmaOperacion? firma;

  /// Perfil (`X-Shrimp-Role`) con el que se hizo la operación. Se envía con
  /// ese perfil aunque después se cambie de perfil en la app.
  final String? rol;

  const OperacionPendiente({
    this.idLocal,
    required this.tipo,
    required this.recursoId,
    this.despachoId,
    required this.cuerpoSerializado,
    required this.claveIdempotencia,
    required this.creadaEn,
    this.intentos = 0,
    this.ultimoError,
    this.ultimoErrorTipo,
    this.rol,
    this.firma,
  });

  /// La misma operación con su firma (o sin ella si [f] es nula).
  OperacionPendiente conFirma(FirmaOperacion? f) => OperacionPendiente(
        idLocal: idLocal,
        tipo: tipo,
        recursoId: recursoId,
        despachoId: despachoId,
        cuerpoSerializado: cuerpoSerializado,
        claveIdempotencia: claveIdempotencia,
        creadaEn: creadaEn,
        intentos: intentos,
        ultimoError: ultimoError,
        ultimoErrorTipo: ultimoErrorTipo,
        rol: rol,
        firma: f,
      );

  /// El cuerpo como mapa (solo para leerlo; lo que se envía es [cuerpoSerializado]).
  Map<String, dynamic> get cuerpo {
    try {
      final v = jsonDecode(cuerpoSerializado);
      return v is Map<String, dynamic> ? v : const {};
    } catch (_) {
      return const {};
    }
  }

  /// La operación exige firma del dispositivo (sección 4 del contrato).
  bool get esSensible => esRutaSensible(metodo, ruta);

  /// Ruta completa tal como se firma (`/api/v1/...`).
  String get rutaCompleta => rutaFirmada(ruta);

  /// Cadena canónica que se firma para esta operación con el [timestamp] dado.
  String cadenaParaFirmar(int timestamp) => cadenaCanonica(
        metodo: metodo,
        ruta: ruta,
        timestamp: timestamp,
        idempotencia: claveIdempotencia,
        cuerpo: bytesDelCuerpo(cuerpoSerializado),
      );

  /// El servidor la rechazó por la firma y hay que volver a firmarla.
  bool get errorDeFirma => const {
        'signature-expired',
        'signature-required',
        'invalid-signature',
        'firma-pendiente',
      }.contains(ultimoErrorTipo);

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
        TipoOperacion.fotoCampo => '/verifications/$recursoId/photos',
        TipoOperacion.aceptacionInforme => '/verifications/$recursoId/acceptance',
        TipoOperacion.programarDespacho => '/dispatches/$recursoId',
        TipoOperacion.compraVerificada => '/verifications',
        TipoOperacion.compraDirecta => '/purchases',
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
        TipoOperacion.fotoCampo => 'Foto de campo',
        TipoOperacion.aceptacionInforme => 'Decisión sobre el informe',
        TipoOperacion.programarDespacho => 'Programación del despacho',
        TipoOperacion.compraVerificada => 'Compra con verificación',
        TipoOperacion.compraDirecta => 'Compra directa',
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
