/// Enlaces directos (contrato C6) y destinos de los avisos (C3): de una
/// dirección o de un `target {type, id}` a la pantalla que hay que abrir.
/// Funciones puras para poder probarlas sin teléfono.
library;

import '../modelos/aviso.dart';

enum TipoDestino {
  /// `/marketplace/verifications/<uuid>/acceptance` → informe por aceptar.
  informePorAceptar,

  /// `/verifier/verifications/<uuid>` → verificación (técnico / verificadora).
  verificacion,

  /// `/marketplace/purchases/<uuid>` (y `/traceability`) → compra.
  compra,

  /// `/marketplace/sales/<uuid>` → venta.
  venta,

  /// `/marketplace/dispatch/<uuid>` → despacho.
  despacho,

  /// `/copacker/orders/<uuid>` → orden de empaque.
  ordenEmpaque,

  /// `/marketplace/products/<uuid>` → lote.
  lote,

  /// Solicitudes de empaque abiertas (aviso de solicitud publicada).
  solicitudesEmpaque,

  /// La bandeja de avisos (aviso genérico de la tarea en segundo plano).
  avisos,
}

/// Adónde lleva un enlace o un aviso.
class Destino {
  final TipoDestino tipo;
  final String? id;
  const Destino(this.tipo, [this.id]);

  @override
  bool operator ==(Object other) => other is Destino && other.tipo == tipo && other.id == id;
  @override
  int get hashCode => Object.hash(tipo, id);
  @override
  String toString() => 'Destino(${tipo.name}${id == null ? '' : ', $id'})';
}

/// Dominios que abre la app (https, con `android:autoVerify`).
const dominiosEnlaces = {
  'camaronmarket.com',
  'www.camaronmarket.com',
  'demo.camaronmarket.com',
  'verificadores.camaronmarket.com',
  'empaque.camaronmarket.com',
  'verificadores.demo.camaronmarket.com',
  'empaque.demo.camaronmarket.com',
};

/// Esquema propio: `camaronmarket://<ruta>` con la misma ruta que la web.
const esquemaApp = 'camaronmarket';

final _uuid = RegExp(r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$');

bool _esId(String s) => _uuid.hasMatch(s);

/// Destino de una ruta web del contrato C6. Nulo si la ruta no es de las
/// que abre la app (→ inicio).
Destino? destinoDeRuta(String ruta) {
  final partes = ruta.split('/').where((p) => p.isNotEmpty).toList();
  if (partes.length < 3) return null;
  final id = partes[2];
  if (!_esId(id)) return null;
  final resto = partes.sublist(3);
  switch ((partes[0], partes[1])) {
    case ('marketplace', 'verifications') when resto.length == 1 && resto.first == 'acceptance':
      return Destino(TipoDestino.informePorAceptar, id);
    case ('verifier', 'verifications') when resto.isEmpty:
      return Destino(TipoDestino.verificacion, id);
    case ('marketplace', 'purchases') when resto.isEmpty || (resto.length == 1 && resto.first == 'traceability'):
      return Destino(TipoDestino.compra, id);
    case ('marketplace', 'sales') when resto.isEmpty:
      return Destino(TipoDestino.venta, id);
    case ('marketplace', 'dispatch') when resto.isEmpty:
      return Destino(TipoDestino.despacho, id);
    case ('copacker', 'orders') when resto.isEmpty:
      return Destino(TipoDestino.ordenEmpaque, id);
    case ('marketplace', 'products') when resto.isEmpty:
      return Destino(TipoDestino.lote, id);
  }
  return null;
}

/// Destino de un enlace completo: https en los dominios de CamaronMarket o
/// `camaronmarket://`. Para el esquema propio vale `camaronmarket:///ruta`
/// y también `camaronmarket://ruta` (Android pone el primer segmento como
/// host). Nulo si no es de la app o la ruta no se conoce.
Destino? destinoDeEnlace(Uri uri) {
  final esquema = uri.scheme.toLowerCase();
  if (esquema == esquemaApp) {
    final host = uri.host;
    return destinoDeRuta(host.isEmpty ? uri.path : '/$host${uri.path}');
  }
  if (esquema == 'https' && dominiosEnlaces.contains(uri.host.toLowerCase())) return destinoDeRuta(uri.path);
  return null;
}

/// Destino de un aviso: primero su `link` (ruta web de C6, relativa o
/// completa); si no lo tiene o no se reconoce, su `target {type, id}`.
Destino? destinoDeAviso(Aviso a) {
  final enlace = a.enlace;
  if (enlace != null) {
    final uri = Uri.tryParse(enlace);
    if (uri != null) {
      final d = uri.hasScheme ? destinoDeEnlace(uri) : destinoDeRuta(uri.path);
      if (d != null) return d;
    }
  }
  return destinoDeObjetivo(a.destinoTipo, a.destinoId);
}

/// `target.type` del servidor → destino.
Destino? destinoDeObjetivo(String? tipo, String? id) {
  if (tipo == null) return null;
  final t = tipo.toLowerCase().replaceAll('.', '_').replaceAll('-', '_');
  if (t == 'copack_request' || t == 'copacking_request' || t == 'packing_request') {
    return const Destino(TipoDestino.solicitudesEmpaque);
  }
  if (id == null || id.isEmpty) return null;
  return switch (t) {
    'verification' || 'shrimp_verification' => Destino(TipoDestino.verificacion, id),
    'acceptance' || 'verification_acceptance' || 'report' => Destino(TipoDestino.informePorAceptar, id),
    'transaction' || 'purchase' || 'shrimp_transaction' => Destino(TipoDestino.compra, id),
    'sale' => Destino(TipoDestino.venta, id),
    'dispatch' || 'shrimp_dispatch' => Destino(TipoDestino.despacho, id),
    'copack_order' || 'copacking_order' || 'packing_order' => Destino(TipoDestino.ordenEmpaque, id),
    'product' || 'lot' || 'shrimp_product' => Destino(TipoDestino.lote, id),
    _ => null,
  };
}

/// Lo que viaja en la notificación local para abrir el destino al tocarla.
String cargaDeAviso(Aviso a) {
  final d = destinoDeAviso(a);
  return d == null ? 'aviso:${a.id}' : 'destino:${d.tipo.name}:${d.id ?? ''}';
}

/// Lee [cargaDeAviso]. Un aviso sin destino (o el aviso genérico) abre la
/// bandeja de avisos.
Destino destinoDeCarga(String? carga) {
  if (carga != null && carga.startsWith('destino:')) {
    final partes = carga.split(':');
    final tipo = TipoDestino.values.where((t) => t.name == partes[1]).firstOrNull;
    if (tipo != null) return Destino(tipo, partes.length > 2 && partes[2].isNotEmpty ? partes[2] : null);
  }
  return const Destino(TipoDestino.avisos);
}
