/// Textos y formatos para la gente de campo: fechas cortas, libras, dinero y
/// etiquetas en español de los estados que devuelve la API.
library;

import 'package:intl/intl.dart';

final _fecha = DateFormat('dd/MM/yyyy');
final _fechaHora = DateFormat('dd/MM/yyyy HH:mm');
final _hora = DateFormat('HH:mm');
final _numero = NumberFormat('#,##0.##');
final _entero = NumberFormat('#,##0');

DateTime? aFecha(dynamic v) => v is String && v.isNotEmpty ? DateTime.tryParse(v)?.toLocal() : null;

String fecha(dynamic v) {
  final d = aFecha(v);
  return d == null ? '—' : _fecha.format(d);
}

String fechaHora(dynamic v) {
  final d = aFecha(v);
  return d == null ? '—' : _fechaHora.format(d);
}

String hora(DateTime d) => _hora.format(d);

String fechaCorta(DateTime d) => _fecha.format(d);

String numero(dynamic v) => v is num ? _numero.format(v) : '—';

String entero(dynamic v) => v is num ? _entero.format(v) : '—';

String libras(dynamic v) => v is num ? '${_numero.format(v)} lb' : '—';

/// Cantidad con su unidad (`uom` es `{id, name}` en la API).
String cantidad(dynamic qty, dynamic uom) {
  if (qty is! num) return '—';
  final nombre = uom is Map ? (uom['name'] as String? ?? '') : '';
  return '${_numero.format(qty)} $nombre'.trim();
}

/// `{"amount": 2.85, "currency": "USD"}` → `$ 2.85`
String dinero(dynamic v) {
  if (v is! Map || v['amount'] is! num) return '—';
  final moneda = v['currency'] == 'USD' || v['currency'] == null ? r'$' : '${v['currency']}';
  return '$moneda ${NumberFormat('#,##0.00').format(v['amount'])}';
}

String porcentaje(dynamic v) => v is num ? '${NumberFormat('#,##0.#').format(v)} %' : '—';

String nombreDe(dynamic ref) => ref is Map ? (ref['name'] as String? ?? '—') : '—';

String? idDe(dynamic ref) => ref is Map ? ref['id'] as String? : null;

bool esHoy(DateTime? d) {
  if (d == null) return false;
  final ahora = DateTime.now();
  return d.year == ahora.year && d.month == ahora.month && d.day == ahora.day;
}

// ---------------- etiquetas de estado ----------------

String estadoTransaccion(String? s) => switch (s) {
      'draft' => 'Borrador',
      'pending_verification' => 'Pendiente de verificación',
      'pending_acceptance' => 'Esperando aceptación',
      'confirmed' => 'Confirmada',
      'done' => 'Completada',
      'cancel' => 'Cancelada',
      _ => s ?? '—',
    };

/// Una compra o venta sigue en curso mientras no esté completada ni cancelada.
bool transaccionEnCurso(String? s) => s != null && s != 'done' && s != 'cancel' && s != 'draft';

String estadoProducto(String? s) => switch (s) {
      'draft' => 'Borrador',
      'published' => 'Publicado',
      'sold' => 'Vendido',
      'cancel' => 'Eliminado',
      _ => s ?? '—',
    };

String estadoDespacho(String? s) => switch (s) {
      'sin_cita' => 'Sin cita',
      'programado' => 'Cita fijada',
      'en_ruta' => 'En camino',
      'llegado' => 'Llegó a planta',
      _ => s ?? '—',
    };

String estadoSolicitudMaquila(String? s) => switch (s) {
      'draft' => 'Borrador',
      'published' => 'Abierta',
      'assigned' => 'Adjudicada',
      'done' => 'Cerrada',
      'cancelled' => 'Cancelada',
      _ => s ?? '—',
    };

String estadoOferta(String? s) => switch (s) {
      'sent' => 'Enviada',
      'accepted' => 'Aceptada',
      'rejected' => 'Descartada',
      'withdrawn' => 'Retirada',
      _ => s ?? '—',
    };

String estadoOrdenMaquila(String? s) => switch (s) {
      'confirmed' => 'Adjudicada',
      'received' => 'Recibida en planta',
      'packed' => 'Empacada',
      'signed' => 'Acta firmada',
      'closed' => 'Cerrada',
      'cancelled' => 'Cancelada',
      _ => s ?? '—',
    };

String estadoActa(String? s) => switch (s) {
      'na' => 'Sin abrir',
      'open' => 'A la firma',
      'closed' => 'Firmada por los dos',
      'disputed' => 'En disputa',
      _ => s ?? '—',
    };

String estadoTarifa(String? s) => switch (s) {
      'draft' => 'Borrador',
      'published' => 'Publicada',
      'archived' => 'Archivada',
      _ => s ?? '—',
    };

String estadoListaPrecios(String? s) => estadoTarifa(s);

String presentacion(String? s) => switch (s) {
      'entero' => 'Entero',
      'cola' => 'Cola',
      'valor_agregado' => 'Valor agregado',
      _ => s ?? '—',
    };

String decisionEtiqueta(String? s) => switch (s) {
      'pending' => 'Pendiente',
      'accepted' => 'Aceptó',
      'counter' => 'Contraoferta',
      'rejected' => 'Rechazó',
      _ => s ?? '—',
    };
