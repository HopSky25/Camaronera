/// Publicar un lote desde la app: qué publica cada perfil, el cuerpo de
/// `POST /products` según el tipo (nauplio, larva o camarón) y la validación,
/// con las mismas reglas que el formulario del portal
/// (shrimp_marketplace/controllers/product_portal.py y shrimp_product_form.js).
/// Funciones puras: se prueban sin teléfono.
library;

import '../datos/api_cliente.dart';
import '../pantallas/comunes/formato.dart';
import 'cuerpos.dart';

/// Tipos de producto que publica cada perfil (STAGE_TIPOS_POR_ROL del
/// portal): el naupliero (`semillero`) y el laboratorio, nauplio y larva; la
/// camaronera, camarón (juvenil y engorde).
List<String> tiposQuePublica(String? perfil) => switch (perfil) {
      'semillero' || 'laboratorio' => const ['nauplio', 'larva'],
      'camaronera' => const ['camaron'],
      _ => const [],
    };

/// El perfil puede publicar lotes desde la app.
bool puedePublicarLotes(String? perfil) => tiposQuePublica(perfil).isNotEmpty;

String nombreTipo(String tipo) => switch (tipo) {
      'nauplio' => 'Nauplio',
      'larva' => 'Larva',
      'camaron' => 'Camarón',
      _ => tipo,
    };

/// Tipo de un estadío del catálogo: el campo `type` de la API y, si un
/// servidor viejo no lo manda, la misma regla del modelo por el código
/// (NAUPLIO; ZOEA, MYSIS y PL*; lo demás es camarón).
String tipoDeEstadio(Map<String, dynamic> estadio) {
  final t = estadio['type'];
  if (t is String && t.isNotEmpty) return t;
  final code = ((estadio['code'] as String?) ?? '').trim().toUpperCase();
  if (code == 'NAUPLIO') return 'nauplio';
  if (code == 'ZOEA' || code == 'MYSIS' || code.startsWith('PL')) return 'larva';
  return 'camaron';
}

/// Solo el camarón de engorde exige presentación y talla (se cruzan con las
/// listas de precios de las empacadoras).
bool exigeTalla(Map<String, dynamic>? estadio) =>
    ((estadio?['code'] as String?) ?? '').trim().toUpperCase() == 'ENGORDE';

/// Código de la unidad por defecto según el perfil: millar en nauplio y
/// larva, libra en camarón.
String unidadDelPerfil(String? perfil) => perfil == 'camaronera' ? 'libra' : 'millar';

/// La unidad del catálogo que se marca por defecto (o la primera, si el
/// catálogo no tiene esa).
Map<String, dynamic>? unidadPorDefecto(String? perfil, List<Map<String, dynamic>> unidades) {
  final codigo = unidadDelPerfil(perfil);
  for (final u in unidades) {
    if (((u['code'] as String?) ?? '').trim().toLowerCase() == codigo) return u;
  }
  return unidades.isEmpty ? null : unidades.first;
}

/// Una instalación puede ser origen de un lote si produce: no una bodega ni
/// una oficina.
bool instalacionDeOrigen(Map<String, dynamic> f) => !const {'warehouse', 'office'}.contains(f['facility_type']);

/// Provincias del Ecuador, como el selector de «Ubicación» del portal.
const provincias = [
  'Azuay', 'Bolívar', 'Cañar', 'Carchi', 'Chimborazo', 'Cotopaxi', 'El Oro', 'Esmeraldas', 'Galápagos',
  'Guayas', 'Imbabura', 'Loja', 'Los Ríos', 'Manabí', 'Morona Santiago', 'Napo', 'Orellana', 'Pastaza',
  'Pichincha', 'Santa Elena', 'Santo Domingo de los Tsáchilas', 'Sucumbíos', 'Tungurahua', 'Zamora Chinchipe',
];

/// Aviso de verificación en campo del formulario del portal
/// (shrimp_verification/views/product_form_inherit.xml).
String avisoVerificacion(String? perfil) => perfil == 'camaronera'
    ? 'Este lote requiere verificación en campo. Todo camarón adulto se compra con verificación: '
        'un verificador revisa cantidad y calidad antes de cerrar la venta. La cantidad en '
        'verificación queda reservada.'
    : 'En larva y nauplio la verificación en campo es opcional: solo se hace si el comprador la solicita.';

/// Lo que se escribe en el formulario «Publicar lote». Los textos son tal
/// cual se escribieron; [errores] y [aCuerpoApi] los interpretan.
class LoteNuevo {
  String nombre = '';
  String codigoLote = '';
  String? especieId;
  Map<String, dynamic>? estadio;
  String? geneticaId;
  String supervivencia = '';
  String tamanoMg = '';
  String sanidad = '';
  String? provincia;
  DateTime? fechaEntrega;
  String? instalacionId;
  String? piscinaId;
  String? presentacion;
  String? tallaId;
  Map<String, dynamic>? unidad;
  String cantidad = '';
  String precio = '';

  /// Etapa con la que llegó el lote al editarlo: como en el portal, un lote
  /// antiguo conserva la suya aunque el perfil ya no la publique, mientras
  /// no se cambie.
  String? etapaOriginal;

  LoteNuevo();

  /// El formulario lleno con un lote propio (`GET /products/{id}`), para
  /// editarlo. Las relaciones llegan como `{id, name}`: la etapa y la
  /// unidad se buscan en sus catálogos (con el código y el tipo).
  factory LoteNuevo.desdeProducto(
    Map<String, dynamic> p, {
    List<Map<String, dynamic>> estadios = const [],
    List<Map<String, dynamic>> unidades = const [],
  }) {
    Map<String, dynamic>? delCatalogo(dynamic ref, List<Map<String, dynamic>> catalogo) {
      final id = idDe(ref);
      if (id == null) return null;
      return catalogo.where((c) => c['id'] == id).firstOrNull ?? Map<String, dynamic>.from(ref as Map);
    }

    String texto(dynamic v) => v is String ? v : '';
    final precio = p['price'];
    final l = LoteNuevo()
      ..nombre = texto(p['name'])
      ..codigoLote = texto(p['batch_code'])
      ..especieId = idDe(p['species'])
      ..estadio = delCatalogo(p['stage'], estadios)
      ..geneticaId = idDe(p['genetics_line'])
      ..supervivencia = textoNumero(p['survival_rate'])
      ..tamanoMg = textoNumero(p['avg_size_mg'])
      ..sanidad = texto(p['health_status'])
      ..provincia = p['location'] is String && (p['location'] as String).isNotEmpty ? p['location'] as String : null
      ..fechaEntrega = p['expected_delivery_date'] is String ? DateTime.tryParse(p['expected_delivery_date'] as String) : null
      ..instalacionId = idDe(p['origin_facility'])
      ..piscinaId = idDe(p['origin_pond'])
      ..presentacion = p['presentation'] is String ? p['presentation'] as String : null
      ..tallaId = idDe(p['size_grade'])
      ..unidad = delCatalogo(p['uom'], unidades)
      ..cantidad = textoNumero(p['initial_qty'])
      ..precio = textoNumero(precio is Map ? precio['amount'] : precio);
    l.etapaOriginal = idDe(l.estadio);
    return l;
  }

  String? get tipo => estadio == null ? null : tipoDeEstadio(estadio!);
  bool get esCamaron => tipo == 'camaron';

  /// Errores por campo (vacío = se puede guardar). Mismas reglas que el
  /// portal: nombre, etapa (la que el perfil puede vender), cantidad mayor a
  /// 0, precio, y presentación y talla en engorde.
  Map<String, String> errores(String? perfil) {
    final e = <String, String>{};
    if (nombre.trim().isEmpty) e['nombre'] = 'Escribe el nombre del producto.';
    if (estadio == null) {
      e['estadio'] = 'Elige la etapa del lote.';
    } else if (!tiposQuePublica(perfil).contains(tipo) && idDe(estadio) != etapaOriginal) {
      e['estadio'] = 'Tu perfil no publica ${nombreTipo(tipo!).toLowerCase()}.';
    }
    final qty = numeroEscrito(cantidad);
    if (qty == null || qty <= 0) e['cantidad'] = 'La cantidad debe ser mayor a 0.';
    final p = numeroEscrito(precio);
    if (precio.trim().isEmpty || p == null || p < 0) {
      final por = porUnidad(unidad);
      e['precio'] = 'Ingresa el precio${por.isEmpty ? '' : ' $por'} para poder publicar.';
    }
    if (esCamaron && exigeTalla(estadio)) {
      if (presentacion == null) e['presentacion'] = 'El camarón de engorde necesita presentación.';
      if (tallaId == null) e['talla'] = 'El camarón de engorde necesita talla.';
    }
    if (supervivencia.trim().isNotEmpty) {
      final s = numeroEscrito(supervivencia);
      if (s == null || s < 0 || s > 100) e['supervivencia'] = 'La supervivencia va de 0 a 100 %.';
    }
    if (tamanoMg.trim().isNotEmpty) {
      final t = numeroEscrito(tamanoMg);
      if (t == null || t < 0) e['tamano'] = 'Escribe el tamaño en mg (0 o más).';
    }
    return e;
  }

  /// Cuerpo de `POST /products`. Sin presentación ni talla fuera del
  /// camarón (el portal las descarta en nauplio y larva), sin piscina si no
  /// hay instalación, y sin los opcionales vacíos.
  Map<String, dynamic> aCuerpoApi() {
    String? limpio(String s) => s.trim().isEmpty ? null : s.trim();
    final camaron = esCamaron;
    return {
      'name': nombre.trim(),
      'price': numeroEscrito(precio) ?? 0,
      'initial_qty': numeroEscrito(cantidad) ?? 0,
      'uom': ?idDe(unidad),
      'species': ?especieId,
      'stage': ?idDe(estadio),
      'genetics_line': ?geneticaId,
      if (camaron) 'presentation': ?presentacion,
      if (camaron && presentacion != null) 'size_grade': ?tallaId,
      if (limpio(supervivencia) != null) 'survival_rate': numeroEscrito(supervivencia),
      if (limpio(tamanoMg) != null) 'avg_size_mg': numeroEscrito(tamanoMg),
      'health_status': ?limpio(sanidad),
      'location': ?provincia,
      if (fechaEntrega != null) 'expected_delivery_date': fechaApi(fechaEntrega!),
      'origin_facility': ?instalacionId,
      if (instalacionId != null) 'origin_pond': ?piscinaId,
      'batch_code': ?limpio(codigoLote),
    };
  }
}

/// Un número de la API como texto para un campo del formulario: sin «.0»
/// de más (1500.0 → «1500») y vacío si no hay valor.
String textoNumero(dynamic v) {
  if (v is! num) return '';
  if (v == v.roundToDouble()) return v.toInt().toString();
  return v.toString();
}

/// Qué le falta a un lote (tal como lo devuelve `GET /products/{id}`) para
/// publicarse. Vacío = se puede publicar. El lote trae la etapa como
/// `{id, name}`: con el catálogo de [estadios] se sabe si es engorde (sin
/// él, lo comprueba el servidor al publicar).
List<String> faltantesParaPublicar(Map<String, dynamic> p, {List<Map<String, dynamic>> estadios = const []}) {
  final faltan = <String>[];
  var estadio = p['stage'] is Map ? Map<String, dynamic>.from(p['stage'] as Map) : null;
  if (estadio != null && estadio['code'] == null) {
    estadio = estadios.where((e) => e['id'] == estadio!['id']).firstOrNull ?? estadio;
  }
  if (estadio == null) faltan.add('la etapa');
  final disponible = p['available_qty'] is num ? (p['available_qty'] as num).toDouble() : 0.0;
  if (disponible <= 0) faltan.add('cantidad disponible');
  if (exigeTalla(estadio)) {
    if (p['presentation'] == null) faltan.add('la presentación');
    if (p['size_grade'] is! Map) faltan.add('la talla');
  }
  return faltan;
}

/// Campo del formulario al que corresponde un campo de la API (los
/// `errors[].field` de un 422).
const _campoDeApi = {
  'name': 'nombre',
  'species': 'especie',
  'genetics_line': 'genetica',
  'expected_delivery_date': 'fechaEntrega',
  'stage': 'estadio',
  'stage_id': 'estadio',
  'initial_qty': 'cantidad',
  'price': 'precio',
  'presentation': 'presentacion',
  'size_grade': 'talla',
  'survival_rate': 'supervivencia',
  'avg_size_mg': 'tamano',
  'origin_facility': 'instalacion',
  'origin_pond': 'piscina',
  'uom': 'unidad',
  'batch_code': 'codigoLote',
};

/// Campo del formulario de un campo de la API (o nulo si no tiene).
String? campoDelFormulario(String campoApi) => _campoDeApi[campoApi];

/// Errores por campo de un 422 del servidor, con las claves del formulario.
Map<String, String> erroresDeCampo(ErrorApi e) {
  final out = <String, String>{};
  final lista = e.datos['errors'];
  if (lista is! List) return out;
  for (final item in lista.whereType<Map>()) {
    final campo = _campoDeApi[item['field']];
    final msg = item['message'];
    if (campo != null && msg is String && msg.isNotEmpty) out.putIfAbsent(campo, () => msg);
  }
  return out;
}

/// Texto de un rechazo al crear, publicar o retirar un lote: el `detail` del
/// servidor en los errores del dato (sin repetir la lista de campos) y el
/// texto habitual en el resto (permisos, firma, sesión).
String mensajeLote(ErrorApi e) {
  final detalle = e.datos['detail'];
  if ((e.tipo == 'validation-error' || e.tipo == 'business-rule') && detalle is String && detalle.isNotEmpty) {
    return detalle;
  }
  return e.mensaje;
}
