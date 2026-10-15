/// Cuerpos JSON de las operaciones que la app manda a la API. Son funciones
/// puras para poder probarlas sin pantalla: lo que sale de aquí es
/// exactamente lo que viaja en el POST / PATCH de la cola.
library;

import 'package:intl/intl.dart';

final _soloFecha = DateFormat('yyyy-MM-dd');

/// Fecha sin hora (`2026-10-01`), en el día local del teléfono.
String fechaApi(DateTime d) => _soloFecha.format(d);

/// Fecha y hora en UTC con `Z` y sin microsegundos, como pide la API.
String fechaHoraApi(DateTime d) {
  final u = d.toUtc();
  String dd(int n) => n.toString().padLeft(2, '0');
  return '${u.year.toString().padLeft(4, '0')}-${dd(u.month)}-${dd(u.day)}T${dd(u.hour)}:${dd(u.minute)}:${dd(u.second)}Z';
}

String? _limpio(String? s) {
  final t = s?.trim() ?? '';
  return t.isEmpty ? null : t;
}

/// `POST /verifications/{id}:verdict`
Map<String, dynamic> cuerpoVeredicto(String veredicto, String? notas) => {
      'verdict': veredicto,
      'notes': ?_limpio(notas),
    };

/// Estado local que corresponde a cada veredicto mientras no se sincroniza.
String estadoDeVeredicto(String veredicto) => switch (veredicto) {
      'approve_obs' => 'approved_obs',
      'reject' => 'rejected',
      _ => 'approved',
    };

/// `POST /dispatches/{id}/arrival`
Map<String, dynamic> cuerpoLlegadaCamion(DateTime cuando) => {'actual_arrival': fechaHoraApi(cuando)};

/// `POST /verifications/{id}:assign`
Map<String, dynamic> cuerpoAsignarTecnico(String tecnicoId) => {'technician': tecnicoId.trim()};

/// `POST /verifications/{id}/acceptance` (comprador o vendedor).
Map<String, dynamic> cuerpoAceptacion(String decision, String? motivo) => {
      'decision': decision,
      'reason': ?_limpio(motivo),
    };

/// `PATCH /dispatches/{id}`: solo lo que se rellenó. Vacío si no hay nada.
Map<String, dynamic> cuerpoDespacho({
  DateTime? cosecha,
  DateTime? salida,
  DateTime? eta,
  String? transportista,
  String? placa,
  String? telefono,
  String? notas,
}) =>
    {
      if (cosecha != null) 'harvest_date': fechaApi(cosecha),
      if (salida != null) 'farm_departure': fechaHoraApi(salida),
      if (eta != null) 'eta': fechaHoraApi(eta),
      'carrier_name': ?_limpio(transportista),
      'vehicle_plate': ?_limpio(placa)?.toUpperCase(),
      'carrier_phone': ?_limpio(telefono),
      'notes': ?_limpio(notas),
    };

/// `POST /verifications` (compra con verificación).
Map<String, dynamic> cuerpoCompra({
  required String productoId,
  required double cantidad,
  required String modo,
  String? verificadoraId,
}) =>
    {
      'product': productoId,
      'qty': cantidad,
      'verification_mode': modo,
      if (modo == 'platform') 'verifier': verificadoraId,
    };

/// `POST /purchases` (compra directa, C5).
Map<String, dynamic> cuerpoCompraDirecta({required String productoId, required double cantidad, String? notas}) => {
      'product': productoId,
      'qty': cantidad,
      'notes': ?_limpio(notas),
    };

/// `POST /harvest/commitments/{id}:buy`
Map<String, dynamic> cuerpoCompraReserva({required String modo, String? verificadoraId}) => {
      'verification_mode': modo,
      if (modo == 'platform') 'verifier': verificadoraId,
    };

/// `POST /copack/requests/{id}/offers`
Map<String, dynamic> cuerpoOfertaMaquila({
  required double tarifaPorLb,
  required double capacidadLb,
  required DateTime desde,
  required DateTime hasta,
  String? notas,
}) =>
    {
      'rate_per_lb': tarifaPorLb,
      'capacity_lb': capacidadLb,
      'available_from': fechaApi(desde),
      'available_to': fechaApi(hasta),
      'notes': ?_limpio(notas),
    };

/// `POST /copack/orders/{id}:reception`
Map<String, dynamic> cuerpoRecepcionMaquila({required double librasRecibidas, required bool insumos, String? problema}) => {
      'received_lb': librasRecibidas,
      'supplies_received': insumos,
      if (!insumos) 'supplies_issue': ?_limpio(problema),
    };

/// `POST /copack/orders/{id}:packing`
Map<String, dynamic> cuerpoEmpaqueMaquila({required double librasEmpacadas, int? cajas}) => {
      'packed_lb': librasEmpacadas,
      'boxes': ?cajas,
    };

/// `POST /copack/orders/{id}:sign`
Map<String, dynamic> cuerpoFirmaActa(String decision, String? motivo) => {
      'decision': decision,
      'reason': ?_limpio(motivo),
    };

/// Número escrito por una persona: admite coma decimal y espacios.
double? numeroEscrito(String? s) => s == null ? null : double.tryParse(s.replaceAll(',', '.').trim());
