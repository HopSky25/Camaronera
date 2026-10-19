/// Instalaciones del vendedor (fincas, laboratorios, centros de maduración,
/// bodegas…) y sus piscinas o tanques, con las reglas de «Instalaciones» en
/// Mi cuenta del portal (shrimp_marketplace/controllers/my_account_facilities.py):
/// tipos de la lista, números dentro de rango y el vocabulario de cada
/// perfil. Funciones puras: se prueban sin teléfono.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'cuerpos.dart';

/// Límites del portal (paso «Instalaciones» del alta).
const maximoMetros = 100000.0;
const maximoProfundidad = 50.0;
const maximoVolumen = 10000000.0;

const tiposInstalacion = {
  'hatchery': 'Centro de maduración (naupliero)',
  'laboratory': 'Laboratorio',
  'farm': 'Finca',
  'warehouse': 'Bodega',
  'office': 'Oficina',
  'other': 'Otro',
};

const tiposPiscina = {
  'earth': 'Piscina de tierra',
  'geomembrane': 'Geomembrana',
  'tank': 'Tanque',
  'raceway': 'Canal',
  'other': 'Otro',
};

/// Cómo llama cada perfil a sus instalaciones y piscinas, y qué tipos
/// ofrece primero (no se restringe ninguno), como el portal.
class Vocabulario {
  final String instalaciones;
  final String instalacion;
  final String piscinas;
  final String piscina;
  final String tipoInstalacion;
  final String tipoPiscina;

  /// `dimensions` (camaronera: área) o `volume` (laboratorio y naupliero).
  final String modo;
  final List<String> ordenInstalacion;
  final List<String> ordenPiscina;
  const Vocabulario({
    required this.instalaciones,
    required this.instalacion,
    required this.piscinas,
    required this.piscina,
    required this.tipoInstalacion,
    required this.tipoPiscina,
    required this.modo,
    required this.ordenInstalacion,
    required this.ordenPiscina,
  });
}

Vocabulario vocabularioDe(String? perfil) => switch (perfil) {
      'laboratorio' => const Vocabulario(
          instalaciones: 'Laboratorios',
          instalacion: 'laboratorio',
          piscinas: 'Tanques',
          piscina: 'tanque',
          tipoInstalacion: 'laboratory',
          tipoPiscina: 'tank',
          modo: 'volume',
          ordenInstalacion: ['laboratory', 'hatchery', 'farm', 'warehouse', 'office', 'other'],
          ordenPiscina: ['tank', 'raceway', 'geomembrane', 'earth', 'other'],
        ),
      'semillero' => const Vocabulario(
          instalaciones: 'Centros de maduración',
          instalacion: 'centro de maduración',
          piscinas: 'Tanques',
          piscina: 'tanque',
          tipoInstalacion: 'hatchery',
          tipoPiscina: 'tank',
          modo: 'volume',
          ordenInstalacion: ['hatchery', 'laboratory', 'farm', 'warehouse', 'office', 'other'],
          ordenPiscina: ['tank', 'raceway', 'geomembrane', 'earth', 'other'],
        ),
      _ => const Vocabulario(
          instalaciones: 'Fincas',
          instalacion: 'finca',
          piscinas: 'Piscinas',
          piscina: 'piscina',
          tipoInstalacion: 'farm',
          tipoPiscina: 'earth',
          modo: 'dimensions',
          ordenInstalacion: ['farm', 'warehouse', 'office', 'laboratory', 'hatchery', 'other'],
          ordenPiscina: ['earth', 'geomembrane', 'raceway', 'tank', 'other'],
        ),
    };

String _texto(dynamic v) => v is String ? v : '';
String _numeroTexto(dynamic v) {
  if (v is! num || v == 0) return '';
  return v == v.roundToDouble() ? v.toInt().toString() : v.toString();
}

/// Lo que se escribe en «Instalación».
class InstalacionForm {
  String nombre = '';
  String codigo = '';
  String tipo;
  String ciudad = '';
  String provincia = '';
  String direccion = '';
  String notas = '';

  InstalacionForm({this.tipo = 'farm'});

  factory InstalacionForm.desde(Map<String, dynamic> f) => InstalacionForm(tipo: _texto(f['facility_type']).isEmpty ? 'farm' : f['facility_type'] as String)
    ..nombre = _texto(f['name'])
    ..codigo = _texto(f['code'])
    ..ciudad = _texto(f['city'])
    ..provincia = _texto(f['province'])
    ..direccion = _texto(f['address'])
    ..notas = _texto(f['notes']);

  Map<String, String> errores() => {
        if (nombre.trim().isEmpty) 'nombre': 'El nombre es obligatorio.',
        if (!tiposInstalacion.containsKey(tipo)) 'tipo': 'Elige un tipo de instalación de la lista.',
      };

  /// Cuerpo de `POST /facilities` (y base del `PATCH`: vacío = null).
  Map<String, dynamic> aCuerpoApi() {
    String? l(String s) => s.trim().isEmpty ? null : s.trim();
    return {
      'name': nombre.trim(),
      'code': l(codigo),
      'facility_type': tipo,
      'city': l(ciudad),
      'province': l(provincia),
      'address': l(direccion),
      'notes': l(notas),
    };
  }
}

/// Lo que se escribe en «Piscina» o «Tanque».
class PiscinaForm {
  String nombre = '';
  String codigo = '';
  String tipo;
  String? instalacionId;

  /// `dimensions` o `volume`.
  String modo;
  String largo = '';
  String ancho = '';
  String profundidad = '';
  String volumen = '';
  String ubicacion = '';
  String notas = '';

  /// Foto nueva (JPEG ya comprimido) o [quitarFoto].
  Uint8List? foto;
  bool quitarFoto = false;

  PiscinaForm({this.tipo = 'earth', this.modo = 'dimensions', this.instalacionId});

  factory PiscinaForm.desde(Map<String, dynamic> p) => PiscinaForm(
        tipo: _texto(p['pond_type']).isEmpty ? 'earth' : p['pond_type'] as String,
        modo: p['capacity_mode'] == 'volume' ? 'volume' : 'dimensions',
        instalacionId: p['facility'] is Map ? (p['facility'] as Map)['id'] as String? : null,
      )
        ..nombre = _texto(p['name'])
        ..codigo = _texto(p['code'])
        ..largo = _numeroTexto(p['length_m'])
        ..ancho = _numeroTexto(p['width_m'])
        ..profundidad = _numeroTexto(p['depth_m'])
        ..volumen = _numeroTexto(p['manual_volume_m3'])
        ..ubicacion = _texto(p['location'])
        ..notas = _texto(p['notes']);

  Map<String, String> errores() {
    final e = <String, String>{};
    if (nombre.trim().isEmpty) e['nombre'] = 'El nombre es obligatorio.';
    if (!tiposPiscina.containsKey(tipo)) e['tipo'] = 'Elige un tipo de piscina o tanque de la lista.';
    void revisar(String campo, String etiqueta, String valor, double maximo) {
      if (valor.trim().isEmpty) return;
      final n = numeroEscrito(valor);
      if (n == null || !n.isFinite || n < 0 || n > maximo) e[campo] = '$etiqueta: el valor «${valor.trim()}» no es válido.';
    }

    revisar('largo', 'Largo', largo, maximoMetros);
    revisar('ancho', 'Ancho', ancho, maximoMetros);
    revisar('profundidad', 'Profundidad', profundidad, maximoProfundidad);
    revisar('volumen', 'Volumen', volumen, maximoVolumen);
    return e;
  }

  /// Área en m² con las dimensiones (lo que el portal suma solo).
  double? get area {
    final l = numeroEscrito(largo), a = numeroEscrito(ancho);
    return l != null && a != null ? l * a : null;
  }

  /// Cuerpo de `POST /ponds` (la foto aparte: ver [conFoto]).
  Map<String, dynamic> aCuerpoApi() {
    String? l(String s) => s.trim().isEmpty ? null : s.trim();
    double n(String s) => numeroEscrito(s) ?? 0;
    return {
      'name': nombre.trim(),
      'code': l(codigo),
      'pond_type': tipo,
      'facility': instalacionId,
      'capacity_mode': modo,
      'length_m': n(largo),
      'width_m': n(ancho),
      'depth_m': n(profundidad),
      'manual_volume_m3': n(volumen),
      'location': l(ubicacion),
      'notes': l(notas),
    };
  }

  /// El cuerpo con la foto nueva o la orden de quitarla.
  Map<String, dynamic> conFoto(Map<String, dynamic> cuerpo) => {
        ...cuerpo,
        if (foto != null) 'image': base64.encode(foto!) else if (quitarFoto) 'image': null,
      };
}

/// Campo del formulario de un campo de la API (los `errors[].field` de un 422).
const campoInstalacionDeApi = {
  'name': 'nombre',
  'code': 'codigo',
  'facility_type': 'tipo',
  'pond_type': 'tipo',
  'city': 'ciudad',
  'province': 'provincia',
  'length_m': 'largo',
  'width_m': 'ancho',
  'depth_m': 'profundidad',
  'manual_volume_m3': 'volumen',
  'image': 'foto',
  'facility': 'instalacion',
};
