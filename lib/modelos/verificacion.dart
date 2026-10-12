/// Modelo de la verificación tal como la devuelve la API de CamaronMarket
/// (`GET /verifications/{id}`), más el informe que el técnico captura en campo.
///
/// Solo se modelan los campos que la app usa. Lo demás viaja en `crudo`
/// (el JSON completo) para no perder nada al volver a guardarlo.
library;

/// Cadena de un valor JSON, o nulo si no es cadena (Odoo manda `false` en
/// los campos de texto vacíos y `null` en los que no aplican).
String? jsonTexto(dynamic v) => v is String ? v : null;

/// Número de un valor JSON, o nulo si no es número (`false`, `null`, texto).
double? jsonNumero(dynamic v) => v is num ? v.toDouble() : null;

/// Fecha ISO-8601 de la API (UTC con `Z`, o solo fecha) pasada a hora local.
DateTime? fechaIso(dynamic v) => v is String && v.isNotEmpty ? DateTime.tryParse(v)?.toLocal() : null;

class Referencia {
  final String? id;
  final String nombre;
  const Referencia({this.id, required this.nombre});

  factory Referencia.desde(dynamic json) {
    if (json is Map) {
      return Referencia(id: jsonTexto(json['id']), nombre: jsonTexto(json['name']) ?? '');
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
        clase: jsonTexto(json['quality_class']) ?? 'a',
        talla: jsonTexto(json['size_code']) ?? '',
        libras: jsonNumero(json['weight_lb']) ?? 0,
      );
}

class Conteo {
  /// Camarones por libra
  double valor;
  String? nota;
  Conteo({required this.valor, this.nota});
  Map<String, dynamic> aJson() => {'value': valor, if (nota != null && nota!.isNotEmpty) 'note': nota};
  factory Conteo.desde(Map json) => Conteo(valor: jsonNumero(json['value']) ?? 0, nota: jsonTexto(json['note']));
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

  // Solo para verificaciones de larva (`scope == "larvae"`).
  double? larvaCantidad;
  /// 0-100
  double? larvaSupervivencia;
  double? larvaTamanoMg;
  /// excellent | good | acceptable | rejected
  String? larvaSanidad;
  String? larvaSanidadNotas;

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
    this.larvaCantidad,
    this.larvaSupervivencia,
    this.larvaTamanoMg,
    this.larvaSanidad,
    this.larvaSanidadNotas,
  })  : lineas = lineas ?? [],
        conteos = conteos ?? [];

  /// Qué le falta al informe para que el servidor admita un veredicto
  /// (`_missing_report_fields` del modelo). Vacío = se puede dictaminar.
  List<String> faltantesParaVeredicto({required bool larva}) {
    bool hay(double? d) => d != null && d > 0;
    if (larva) {
      return [
        if (!hay(larvaCantidad)) 'cantidad verificada',
        if (!hay(larvaSupervivencia)) 'supervivencia medida',
        if (!hay(larvaTamanoMg)) 'tamaño promedio',
        if ((larvaSanidad ?? '').isEmpty) 'estado sanitario',
      ];
    }
    return [
      if (!hay(librasPlanta)) 'peso en planta',
      if ((presentacion ?? '').isEmpty) 'presentación (entero o cola)',
      if (!hay(metabisulfitoPpm)) 'metabisulfito (ppm)',
      if (lineas.isEmpty) 'clasificación por tallas',
      if ((sabor ?? '').isEmpty) 'resultado de sabor',
    ];
  }

  /// Cuerpo del PATCH: los campos con valor más las listas de clasificación
  /// y conteos tal como quedaron (vacías si el técnico las borró: la API
  /// reemplaza lo que tenía).
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
    pon('larvae_qty_verified', larvaCantidad);
    pon('larvae_survival_rate', larvaSupervivencia);
    pon('larvae_avg_size_mg', larvaTamanoMg);
    pon('larvae_health_status', larvaSanidad);
    pon('larvae_health_notes', larvaSanidadNotas);
    pon('incident_notes', incidencias);
    pon('gps_latitude', latitud);
    pon('gps_longitude', longitud);
    m['lines'] = lineas.map((l) => l.aJson()).toList();
    m['counts'] = conteos.map((c) => c.aJson()).toList();
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
        'larvaCantidad': larvaCantidad,
        'larvaSupervivencia': larvaSupervivencia,
        'larvaTamanoMg': larvaTamanoMg,
        'larvaSanidad': larvaSanidad,
        'larvaSanidadNotas': larvaSanidadNotas,
      };

  factory InformeCampo.desdeJsonLocal(Map json) {
    return InformeCampo(
      plantaNombre: jsonTexto(json['plantaNombre']),
      librasEnviadas: jsonNumero(json['librasEnviadas']),
      librasPlanta: jsonNumero(json['librasPlanta']),
      librasBasura: jsonNumero(json['librasBasura']),
      presentacion: jsonTexto(json['presentacion']),
      metabisulfitoPpm: jsonNumero(json['metabisulfitoPpm']),
      metabisulfitoNotas: jsonTexto(json['metabisulfitoNotas']),
      sabor: jsonTexto(json['sabor']),
      saborNotas: jsonTexto(json['saborNotas']),
      gramosFinca: jsonNumero(json['gramosFinca']),
      gramosPlanta1: jsonNumero(json['gramosPlanta1']),
      gramosPlanta2: jsonNumero(json['gramosPlanta2']),
      incidencias: jsonTexto(json['incidencias']),
      latitud: jsonNumero(json['latitud']),
      longitud: jsonNumero(json['longitud']),
      lineas: _lista(json['lineas']).map(LineaClasificacion.desde).toList(),
      conteos: _lista(json['conteos']).map(Conteo.desde).toList(),
      larvaCantidad: jsonNumero(json['larvaCantidad']),
      larvaSupervivencia: jsonNumero(json['larvaSupervivencia']),
      larvaTamanoMg: jsonNumero(json['larvaTamanoMg']),
      larvaSanidad: jsonTexto(json['larvaSanidad']),
      larvaSanidadNotas: jsonTexto(json['larvaSanidadNotas']),
    );
  }

  /// Arranca el informe con lo que el servidor ya tiene (si el técnico
  /// empezó desde otro equipo o la empresa cargó algo). Los campos
  /// numéricos en 0 son "sin dato" en Odoo: no se precargan como ceros.
  factory InformeCampo.desdeServidor(Map<String, dynamic> detalle) {
    final pesos = _mapa(detalle['weights']);
    final meta = _mapa(detalle['metabisulfite']);
    final sabor = _mapa(detalle['taste']);
    final gramos = _mapa(detalle['grams']);
    final clasif = _mapa(detalle['classification']);
    final larva = _mapa(detalle['larvae']);
    double? d(dynamic v) {
      final n = jsonNumero(v);
      return n == null || n == 0 ? null : n;
    }

    return InformeCampo(
      plantaNombre: jsonTexto(detalle['plant_name']),
      librasEnviadas: d(pesos['sent_lb']),
      librasPlanta: d(pesos['plant_lb']),
      librasBasura: d(pesos['trash_lb']),
      presentacion: jsonTexto(detalle['presentation']),
      metabisulfitoPpm: d(meta['ppm']),
      metabisulfitoNotas: jsonTexto(meta['notes']),
      sabor: jsonTexto(sabor['result']),
      saborNotas: jsonTexto(sabor['notes']),
      gramosFinca: d(gramos['farm']),
      gramosPlanta1: d(gramos['plant_1']),
      gramosPlanta2: d(gramos['plant_2']),
      incidencias: jsonTexto(detalle['incident_notes']),
      lineas: _lista(clasif['lines']).map(LineaClasificacion.desde).toList(),
      conteos: _lista(detalle['counts']).map(Conteo.desde).toList(),
      larvaCantidad: d(larva['qty_verified']),
      larvaSupervivencia: d(larva['survival_rate']),
      larvaTamanoMg: d(larva['avg_size_mg']),
      larvaSanidad: jsonTexto(larva['health_status']),
      larvaSanidadNotas: jsonTexto(larva['health_notes']),
    );
  }
}

Map _mapa(dynamic v) => v is Map ? v : const {};
Iterable<Map> _lista(dynamic v) => v is List ? v.whereType<Map>() : const [];

class Verificacion {
  final String id;
  final String referencia;
  /// received | assigned | in_field | done | approved | approved_obs | rejected ...
  final String estado;
  /// shrimp | larvae: a la larva no se le piden tallas, metabisulfito ni sabor.
  final String? alcance;
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
    required this.alcance,
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

  /// `true` si el JSON trae un identificador utilizable.
  static bool tieneId(Map json) => (jsonTexto(json['id']) ?? '').isNotEmpty;

  factory Verificacion.desde(Map<String, dynamic> json) {
    return Verificacion(
      id: jsonTexto(json['id']) ?? '',
      referencia: jsonTexto(json['reference']) ?? '',
      estado: jsonTexto(json['state']) ?? '',
      alcance: jsonTexto(json['scope']),
      miRol: jsonTexto(json['my_role']),
      producto: Referencia.desde(json['product']),
      comprador: Referencia.desde(json['buyer']),
      vendedor: Referencia.desde(json['seller']),
      verificadora: Referencia.desde(json['verifier']),
      tecnico: json['technician'] is Map ? Referencia.desde(json['technician']) : null,
      tecnicoAsignado: json['technician_assigned'] == true || json['technician'] is Map,
      modo: jsonTexto(json['verification_mode']),
      transaccionId: jsonTexto(json['transaction']),
      estadoAceptacion: jsonTexto(json['acceptance_state']) ?? 'na',
      plazoAceptacion: fechaIso(json['acceptance_deadline']),
      despachoId: jsonTexto(json['dispatch']),
      despachoEta: fechaIso(json['dispatch_eta']),
      loteCodigo: jsonTexto(json['batch_code']),
      piscina: json['pond'] is Map ? jsonTexto(json['pond']['name']) : null,
      instalacion: json['facility'] is Map ? jsonTexto(json['facility']['name']) : null,
      actualizadoEn: fechaIso(json['updated_at']),
      crudo: json,
    );
  }

  bool get tieneDetalle => crudo.containsKey('weights');
  bool get esLarva => alcance == 'larvae';
  bool get esperaAceptacion => estadoAceptacion == 'waiting';
  bool get soyVerificadora => miRol == 'verifier';

  /// Postura de la parte (`buyer` / `seller`) en el detalle: pending |
  /// accepted | counter | rejected. Nulo si el detalle no está cargado.
  String? decisionDe(String rol) {
    final lista = crudo['acceptances'];
    if (lista is! List) return null;
    for (final a in lista) {
      if (a is Map && a['role'] == rol) return jsonTexto(a['decision']);
    }
    return null;
  }

  /// Mi decisión está pendiente y la ronda sigue abierta.
  bool get puedoDecidir =>
      esperaAceptacion && (miRol == 'buyer' || miRol == 'seller') && (decisionDe(miRol!) ?? 'pending') == 'pending';
  /// El servidor solo admite `:start` con técnico asignado (422 en `received`).
  bool get puedeIniciarCampo => estado == 'assigned';
  bool get esperaTecnico => estado == 'received';
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
