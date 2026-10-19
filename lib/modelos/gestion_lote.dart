/// Gestionar un lote propio desde la app (shrimp_api 19.0.1.17.0): qué
/// cambió al editar y si va firmado, los campos bloqueados por compras, el
/// cuerpo de las fotos y de los certificados, y su validación. Las mismas
/// reglas que el formulario de editar producto del portal. Funciones puras:
/// se prueban sin teléfono.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'cuerpos.dart';
import 'foto_campo.dart';

/// El portal y la API admiten hasta 20 fotos por lote.
const maximoFotosLote = 20;

/// Tope de un archivo (foto o certificado): 5 MB.
const maximoBytesArchivo = 5 * 1024 * 1024;

/// Lo que fija cuánto paga el comprador y cuánto hay: en un lote que no
/// está en borrador se cambia con la huella (`POST /products/{id}:update-terms`).
const camposComerciales = {'price', 'initial_qty', 'uom'};

/// Lo que cambió entre el cuerpo del lote tal como estaba ([antes]) y el
/// del formulario ([ahora]): solo esos campos van en el PATCH. Un campo que
/// estaba y ya no (un opcional que se borró) va en `null` para vaciarlo.
Map<String, dynamic> cambiosLote(Map<String, dynamic> antes, Map<String, dynamic> ahora) {
  final out = <String, dynamic>{};
  for (final e in ahora.entries) {
    if (!_igual(antes[e.key], e.value)) out[e.key] = e.value;
  }
  for (final k in antes.keys) {
    if (!ahora.containsKey(k) && antes[k] != null) out[k] = null;
  }
  return out;
}

bool _igual(dynamic a, dynamic b) {
  if (a is num && b is num) return (a - b).abs() < 0.005;
  return a == b;
}

/// Los campos comerciales que cambian (precio, cantidad, unidad).
List<String> comercialesCambiados(Map<String, dynamic> cambios) =>
    [for (final k in cambios.keys) if (camposComerciales.contains(k)) k];

/// Guardar los cambios va firmado: el lote ya no está en borrador
/// (publicado o vendido) y cambia el precio, la cantidad o la unidad.
bool edicionVaFirmada(Map<String, dynamic> p, Map<String, dynamic> cambios) =>
    p['state'] != 'draft' && comercialesCambiados(cambios).isNotEmpty;

/// Campos de la API que el lote ya no deja cambiar (`locks.fields`).
Set<String> camposBloqueados(Map<String, dynamic> p) {
  final locks = p['locks'];
  final campos = locks is Map ? locks['fields'] : null;
  return campos is List ? {...campos.whereType<String>()} : const {};
}

/// Por qué están bloqueados (o nulo).
String? motivoBloqueo(Map<String, dynamic> p) {
  final locks = p['locks'];
  final r = locks is Map ? locks['reason'] : null;
  return r is String && r.isNotEmpty ? r : null;
}

/// Las fotos del lote en su orden (la primera es la portada).
List<Map<String, dynamic>> fotosDeLote(Map<String, dynamic> respuesta) {
  final d = respuesta['data'] ?? respuesta['photos'];
  return d is List ? [for (final f in d.whereType<Map>()) Map<String, dynamic>.from(f)] : [];
}

/// Comprime una foto para el lote (lado mayor 1600 px, JPEG 75, como las
/// fotos de campo). Lanza [FormatException] si no es una imagen o si aun
/// así pasa de 5 MB.
Uint8List prepararFotoLote(Uint8List original) {
  final jpeg = comprimirFoto(original);
  if (jpeg.length > maximoBytesArchivo) throw const FormatException('La foto es demasiado grande.');
  return jpeg;
}

/// Cuerpo de `POST /products/{id}/photos`.
Map<String, dynamic> cuerpoFotoLote({required String nombreArchivo, required Uint8List jpeg, bool portada = false}) => {
      'filename': nombreArchivo,
      'mime': 'image/jpeg',
      'data': base64.encode(jpeg),
      if (portada) 'cover': true,
    };

/// El orden de las fotos tras mover la de la posición [desde] a la
/// posición [hasta] (la final, como `onReorderItem` de ReorderableListView).
List<String> moverFoto(List<String> ids, int desde, int hasta) {
  final out = [...ids];
  final id = out.removeAt(desde);
  out.insert(hasta.clamp(0, out.length), id);
  return out;
}

/// Tipo REAL de un archivo por su cabecera (como el servidor): PDF, JPG o
/// PNG; nulo si es otra cosa.
String? tipoDeArchivo(Uint8List b) {
  if (b.length >= 5 && b[0] == 0x25 && b[1] == 0x50 && b[2] == 0x44 && b[3] == 0x46 && b[4] == 0x2D) {
    return 'application/pdf';
  }
  if (b.length >= 3 && b[0] == 0xFF && b[1] == 0xD8 && b[2] == 0xFF) return 'image/jpeg';
  if (b.length >= 8 && b[0] == 0x89 && b[1] == 0x50 && b[2] == 0x4E && b[3] == 0x47) return 'image/png';
  return null;
}

/// Estado de revisión de un certificado del lote.
String estadoCertificado(String? s) => switch (s) {
      'approved' => 'Aprobado',
      'pending' => 'En revisión',
      'rejected' => 'Rechazado',
      _ => s ?? '—',
    };

/// Un certificado propio del lote que se sube desde la app (el «Agregar
/// certificado» del formulario del portal): tipo del catálogo, número,
/// fechas y el archivo (PDF o imagen).
class CertificadoNuevo {
  Map<String, dynamic>? tipo;
  String numero = '';
  DateTime? emision;
  DateTime? vence;
  String? nombreArchivo;
  Uint8List? archivo;

  bool get exigeVencimiento => tipo?['expires'] == true;

  /// Errores por campo (vacío = se puede subir).
  Map<String, String> errores() {
    final e = <String, String>{};
    if (tipo == null) e['tipo'] = 'Elige el certificado.';
    if (exigeVencimiento && vence == null) e['vence'] = 'Este certificado exige fecha de vencimiento.';
    if (emision != null && vence != null && vence!.isBefore(emision!)) {
      e['vence'] = 'El vencimiento no puede ser antes de la emisión.';
    }
    final a = archivo;
    if (a == null || a.isEmpty) {
      e['archivo'] = 'Adjunta el archivo del certificado (PDF o foto).';
    } else if (a.length > maximoBytesArchivo) {
      e['archivo'] = 'El archivo pasa de 5 MB.';
    } else if (tipoDeArchivo(a) == null) {
      e['archivo'] = 'Solo PDF, JPG o PNG.';
    }
    return e;
  }

  /// Cuerpo de `POST /products/{id}/certificates`.
  Map<String, dynamic> aCuerpoApi() => {
        'certificate': tipo?['id'],
        if (numero.trim().isNotEmpty) 'number': numero.trim(),
        if (emision != null) 'issue_date': fechaApi(emision!),
        if (vence != null) 'expiry_date': fechaApi(vence!),
        'filename': nombreArchivo ?? 'certificado',
        'data': base64.encode(archivo ?? Uint8List(0)),
      };
}

/// Campo del formulario de certificado de un campo de la API (422).
const campoCertificadoDeApi = {
  'certificate': 'tipo',
  'number': 'numero',
  'issue_date': 'emision',
  'expiry_date': 'vence',
  'data': 'archivo',
  'filename': 'archivo',
};
