/// Modelo de la verificación tal como la devuelve la API de CamaronMarket
/// (`GET /verifications/{id}`), más el informe que el técnico captura en campo.
///
/// Solo se modelan los campos que la app usa. Lo demás viaja en `crudo`
/// (el JSON completo) para no perder nada al volver a guardarlo.
library;

class Referencia {
  final String? id;
  final String nombre;
  const Referencia({this.id, required this.nombre});

  factory Referencia.desde(dynamic json) {
    if (json is Map) {
      return Referencia(id: json['id'] as String?, nombre: (json['name'] ?? '') as String);
    }
    return const Referencia(nombre: '');
  }
}

class LineaClasificacion {
  /// a, b o c
  String clase;
  /// Talla, p. ej. "21/25" o "30/40"
  String talla;
  double libras;
  LineaClasificacion({required this.clase, required this.talla, required this.libras});

  Map<String, dynamic> aJson() => {'quality_class': clase, 'size_code': talla, 'weight_lb': libras};
  factory LineaClasificacion.desde(Map json) => LineaClasificacion(
        clase: (json['quality_class'] ?? 'a') as String,
        talla: (json['size_code'] ?? '') as String,
        libras: ((json['weight_lb'] ?? 0) as num).toDouble(),
      );
}

class Conteo {
  /// Camarones por libra
  double valor;
  String? nota;
  Conteo({required this.valor, this.nota});
  Map<String, dynamic> aJson() => {'value': valor, if (nota != null && nota!.isNotEmpty) 'note': nota};
  factory Conteo.desde(Map json) =>
      Conteo(valor: ((json['value'] ?? 0) as num).toDouble(), nota: json['note'] as String?);
}

/// Lo que el técnico captura en campo. Se guarda en el teléfono y se envía
/// con `PATCH /verifications/{id}/report` cuando hay internet.
class InformeCampo {
  String? plantaNombre;
  double? librasEnviadas;
  double? librasPlanta;
  double? librasBasura;
  /// entero | cola
  String? presentacion;
  double? metabisulfitoPpm;
  String? metabisulfitoNotas;
  /// excellent | good | acceptable | rejected
  String? sabor;
  String? saborNotas;
  double? gramosFinca;
  double? gramosPlanta1;
  double? gramosPlanta2;
  String? incidencias;
  double? latitud;
  double? longitud;
  List<LineaClasificacion> lineas;
  List<Conteo> conteos;

  InformeCampo({
    this.plantaNombre,
    this.librasEnviadas,
    this.librasPlanta,
    this.librasBasura,
    this.presentacion,
    this.metabisulfitoPpm,
    this.metabisulfitoNotas,
    this.sabor,
    this.saborNotas,
    this.gramosFinca,
    this.gramosPlanta1,
    this.gramosPlanta2,
    this.incidencias,
    this.latitud,
    this.longitud,
    List<LineaClasificacion>? lineas,
    List<Conteo>? conteos,
  })  : lineas = lineas ?? [],
        conteos = conteos ?? [];

  /// Cuerpo del PATCH: solo lo que tiene valor (la API rechaza campos
  /// desconocidos y acepta envíos parciales).
  Map<String, dynamic> aCuerpoApi() {
    final m = <String, dynamic>{};
    void pon(String k, dynamic v) {
      if (v == null) return;
      if (v is String && v.trim().isEmpty) return;
      m[k] = v;
    }

    pon('plant_name', plantaNombre);
    pon('weight_sent_lb', librasEnviadas);
    pon('weight_plant_lb', librasPlanta);
    pon('trash_lb', librasBasura);
    pon('presentation', presentacion);
    pon('metabisulfite_ppm', metabisulfitoPpm);
    pon('metabisulfite_notes', metabisulfitoNotas);
    pon('taste_result', sabor);
    pon('taste_notes', saborNotas);
    pon('grams_farm', gramosFinca);
    pon('grams_plant_1', gramosPlanta1);
    pon('grams_plant_2', gramosPlanta2);
    pon('incident_notes', incidencias);
    pon('gps_latitude', latitud);
    pon('gps_longitude', longitud);
    if (lineas.isNotEmpty) m['lines'] = lineas.map((l) => l.aJson()).toList();
    if (conteos.isNotEmpty) m['counts'] = conteos.map((c) => c.aJson()).toList();
    return m;
  }

  /// Para guardar en la base local (todo, incluso vacíos).
  Map<String, dynamic> aJsonLocal() => {
        'plantaNombre': plantaNombre,
        'librasEnviadas': librasEnviadas,
        'librasPlanta': librasPlanta,
        'librasBasura': librasBasura,
        'presentacion': presentacion,
        'metabisulfitoPpm': metabisulfitoPpm,
        'metabisulfitoNotas': metabisulfitoNotas,
        'sabor': sabor,
        'saborNotas': saborNotas,
        'gramosFinca': gramosFinca,
        'gramosPlanta1': gramosPlanta1,
        'gramosPlanta2': gramosPlanta2,
        'incidencias': incidencias,
        'latitud': latitud,
        'longitud': longitud,
        'lineas': lineas.map((l) => l.aJson()).toList(),
        'conteos': conteos.map((c) => c.aJson()).toList(),
      };

  factory InformeCampo.desdeJsonLocal(Map json) {
    double? d(dynamic v) => v == null ? null : (v as num).toDouble();
    return InformeCampo(
      plantaNombre: json['plantaNombre'] as String?,
      librasEnviadas: d(json['librasEnviadas']),
      librasPlanta: d(json['librasPlanta']),
      librasBasura: d(json['librasBasura']),
      presentacion: json['presentacion'] as String?,
      metabisulfitoPpm: d(json['metabisulfitoPpm']),
      metabisulfitoNotas: json['metabisulfitoNotas'] as String?,
      sabor: json['sabor'] as String?,
      saborNotas: json['saborNotas'] as String?,
      gramosFinca: d(json['gramosFinca']),
      gramosPlanta1: d(json['gramosPlanta1']),
      gramosPlanta2: d(json['gramosPlanta2']),
      incidencias: json['incidencias'] as String?,
      latitud: d(json['latitud']),
      longitud: d(json['longitud']),
      lineas: ((json['lineas'] ?? []) as List).map((e) => LineaClasificacion.desde(e as Map)).toList(),
      conteos: ((json['conteos'] ?? []) as List).map((e) => Conteo.desde(e as Map)).toList(),
    );
  }

  /// Arranca el informe con lo que el servidor ya tiene (si el técnico
  /// empezó desde otro equipo o la empresa cargó algo).
  factory InformeCampo.desdeServidor(Map<String, dynamic> detalle) {
    final pesos = (detalle['weights'] ?? {}) as Map;
    final meta = (detalle['metabisulfite'] ?? {}) as Map;
    final sabor = (detalle['taste'] ?? {}) as Map;
    final gramos = (detalle['grams'] ?? {}) as Map;
    final clasif = (detalle['classification'] ?? {}) as Map;
    double? d(dynamic v) => v == null ? null : (v as num).toDouble();
    return InformeCampo(
      plantaNombre: detalle['plant_name'] as String?,
      librasEnviadas: d(pesos['sent_lb']),
      librasPlanta: d(pesos['plant_lb']),
      librasBasura: d(pesos['trash_lb']),
      presentacion: detalle['presentation'] as String?,
      metabisulfitoPpm: d(meta['ppm']),
      metabisulfitoNotas: meta['notes'] as String?,
      sabor: sabor['result'] as String?,
      saborNotas: sabor['notes'] as String?,
      gramosFinca: d(gramos['farm']),
      gramosPlanta1: d(gramos['plant_1']),
      gramosPlanta2: d(gramos['plant_2']),
      incidencias: detalle['incident_notes'] as String?,
      lineas: ((clasif['lines'] ?? []) as List).map((e) => LineaClasificacion.desde(e as Map)).toList(),
      conteos: ((detalle['counts'] ?? []) as List).map((e) => Conteo.desde(e as Map)).toList(),
    );
  }
}

class Verificacion {
  final String id;
  final String referencia;
  /// received | assigned | in_field | done | approved | approved_obs | rejected ...
  final String estado;
  final String? miRol;
  final Referencia producto;
  final Referencia comprador;
  final Referencia vendedor;
  final Referencia verificadora;
  /// Técnico asignado (solo lo ve la verificadora y el propio técnico).
  final Referencia? tecnico;
  final bool tecnicoAsignado;
  /// platform | declared
  final String? modo;
  final String? transaccionId;
  /// na | waiting | closed | broken
  final String estadoAceptacion;
  final DateTime? plazoAceptacion;
  final String? despachoId;
  final DateTime? despachoEta;
  final String? loteCodigo;
  final String? piscina;
  final String? instalacion;
  final DateTime? actualizadoEn;
  /// JSON completo del servidor, por si una pantalla necesita algo más.
  final Map<String, dynamic> crudo;

  const Verificacion({
    required this.id,
    required this.referencia,
    required this.estado,
    required this.miRol,
    required this.producto,
    required this.comprador,
    required this.vendedor,
    required this.verificadora,
    required this.tecnico,
    required this.tecnicoAsignado,
    required this.modo,
    required this.transaccionId,
    required this.estadoAceptacion,
    required this.plazoAceptacion,
    required this.despachoId,
    required this.despachoEta,
    required this.loteCodigo,
    required this.piscina,
    required this.instalacion,
    required this.actualizadoEn,
    required this.crudo,
  });

  factory Verificacion.desde(Map<String, dynamic> json) {
    DateTime? fecha(dynamic v) => v == null ? null : DateTime.tryParse(v as String)?.toLocal();
    return Verificacion(
      id: json['id'] as String,
      referencia: (json['reference'] ?? '') as String,
      estado: (json['state'] ?? '') as String,
      miRol: json['my_role'] as String?,
      producto: Referencia.desde(json['product']),
      comprador: Referencia.desde(json['buyer']),
      vendedor: Referencia.desde(json['seller']),
      verificadora: Referencia.desde(json['verifier']),
      tecnico: json['technician'] is Map ? Referencia.desde(json['technician']) : null,
      tecnicoAsignado: json['technician_assigned'] == true || json['technician'] is Map,
      modo: json['verification_mode'] as String?,
      transaccionId: json['transaction'] as String?,
      estadoAceptacion: (json['acceptance_state'] ?? 'na') as String,
      plazoAceptacion: fecha(json['acceptance_deadline']),
      despachoId: json['dispatch'] as String?,
      despachoEta: fecha(json['dispatch_eta']),
      loteCodigo: json['batch_code'] as String?,
      piscina: json['pond'] is Map ? (json['pond']['name'] as String?) : null,
      instalacion: json['facility'] is Map ? (json['facility']['name'] as String?) : null,
      actualizadoEn: fecha(json['updated_at']),
      crudo: json,
    );
  }

  bool get tieneDetalle => crudo.containsKey('weights');
  bool get esperaAceptacion => estadoAceptacion == 'waiting';
  bool get soyVerificadora => miRol == 'verifier';

  /// Postura de la parte (`buyer` / `seller`) en el detalle: pending |
  /// accepted | counter | rejected. Nulo si el detalle no está cargado.
  String? decisionDe(String rol) {
    final lista = crudo['acceptances'];
    if (lista is! List) return null;
    for (final a in lista) {
      if (a is Map && a['role'] == rol) return a['decision'] as String?;
    }
    return null;
  }

  /// Mi decisión está pendiente y la ronda sigue abierta.
  bool get puedoDecidir =>
      esperaAceptacion && (miRol == 'buyer' || miRol == 'seller') && (decisionDe(miRol!) ?? 'pending') == 'pending';
  bool get puedeIniciarCampo => estado == 'received' || estado == 'assigned';
  bool get enCampo => estado == 'in_field';
  bool get cerrada => const {'done', 'approved', 'approved_obs', 'rejected', 'cancelled'}.contains(estado);

  String get estadoEtiqueta => switch (estado) {
        'received' => 'Recibida',
        'assigned' => 'Asignada',
        'in_field' => 'En campo',
        'done' => 'Informe listo',
        'approved' => 'Aprobada',
        'approved_obs' => 'Aprobada con observaciones',
        'rejected' => 'Rechazada',
        'cancelled' => 'Cancelada',
        'declared_draft' => 'Informe en preparación',
        'declared' => 'Informe declarado',
        _ => estado,
      };

  String get aceptacionEtiqueta => switch (estadoAceptacion) {
        'waiting' => 'Esperando a las partes',
        'closed' => 'Aceptada por ambas',
        'broken' => 'Trato caído',
        _ => 'No corresponde',
      };
}
