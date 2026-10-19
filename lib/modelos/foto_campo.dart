/// Fotos de campo (contrato C4): compresión y cuerpo de
/// `POST /verifications/{id}/photos`. Funciones puras para probarlas sin
/// teléfono.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:image/image.dart' as img;

import 'cuerpos.dart';

/// Lado mayor máximo y calidad JPEG del contrato.
const ladoMaximoFoto = 1600;
const calidadFoto = 75;

/// El servidor no acepta más de 5 MB decodificados (413).
const maximoBytesFoto = 5 * 1024 * 1024;

/// Una fila de SQLite de Android (CursorWindow) no pasa de ~2 MB: el cuerpo
/// en base64 se mantiene por debajo de esto (si no, se reduce más la foto).
const maximoCuerpoEnCola = 1800 * 1024;

/// Comprime una foto: corrige la orientación EXIF, reduce el lado mayor a
/// [ladoMaximo] (sin agrandar) y la guarda en JPEG con [calidad]. Lanza
/// [FormatException] si no es una imagen.
Uint8List comprimirFoto(Uint8List original, {int ladoMaximo = ladoMaximoFoto, int calidad = calidadFoto}) {
  final decodificada = img.decodeImage(original);
  if (decodificada == null) throw const FormatException('El archivo no es una imagen.');
  var foto = img.bakeOrientation(decodificada);
  final mayor = foto.width > foto.height ? foto.width : foto.height;
  if (mayor > ladoMaximo) {
    foto = foto.width >= foto.height
        ? img.copyResize(foto, width: ladoMaximo, interpolation: img.Interpolation.average)
        : img.copyResize(foto, height: ladoMaximo, interpolation: img.Interpolation.average);
  }
  return img.encodeJpg(foto, quality: calidad);
}

/// Cuerpo de `POST /verifications/{id}/photos`: exactamente los campos del
/// contrato (latitud, longitud y nota van aunque sean nulos).
Map<String, dynamic> cuerpoFoto({
  required String nombreArchivo,
  required Uint8List jpeg,
  required DateTime tomadaEn,
  double? latitud,
  double? longitud,
  String? nota,
}) {
  final n = nota?.trim() ?? '';
  return {
    'filename': nombreArchivo,
    'mime': 'image/jpeg',
    'data': base64.encode(jpeg),
    'taken_at': fechaHoraApi(tomadaEn),
    'latitude': latitud,
    'longitude': longitud,
    'note': n.isEmpty ? null : n,
  };
}

/// Comprime y arma el cuerpo; si el cuerpo no cabe en la cola, vuelve a
/// comprimir con un lado menor (1280, 1024…).
Map<String, dynamic> prepararFoto({
  required String nombreArchivo,
  required Uint8List original,
  required DateTime tomadaEn,
  double? latitud,
  double? longitud,
  String? nota,
}) {
  var lado = ladoMaximoFoto;
  while (true) {
    final jpeg = comprimirFoto(original, ladoMaximo: lado);
    final cuerpo = cuerpoFoto(
        nombreArchivo: nombreArchivo, jpeg: jpeg, tomadaEn: tomadaEn, latitud: latitud, longitud: longitud, nota: nota);
    if ((cuerpo['data'] as String).length <= maximoCuerpoEnCola || lado <= 800) {
      if (jpeg.length > maximoBytesFoto) throw const FormatException('La foto es demasiado grande.');
      return cuerpo;
    }
    lado = (lado * 0.8).round();
  }
}

/// Fotos de evidencia del rechazo por «Producto no presentado»: viajan
/// dentro del veredicto (`photos: [{"data", "name"}]`) porque, sin trabajo
/// de campo, la verificación todavía no admite fotos sueltas. Todas juntas
/// tienen que caber en una fila de la cola ([maximoCuerpoEnCola]): cada
/// foto se reduce hasta su parte del espacio.
const maximoFotosEvidencia = 3;

List<Map<String, dynamic>> prepararFotosEvidencia(List<(String nombre, Uint8List original)> fotos) {
  if (fotos.isEmpty) return const [];
  if (fotos.length > maximoFotosEvidencia) {
    throw const FormatException('Son demasiadas fotos: elige hasta $maximoFotosEvidencia.');
  }
  final cupo = (maximoCuerpoEnCola - 4 * 1024) ~/ fotos.length;
  return [
    for (final (nombre, original) in fotos) {'data': _jpegQueQuepa(original, cupo), 'name': nombre},
  ];
}

/// Base64 de la foto comprimida, reduciendo el lado mayor hasta que quepa
/// en [cupo] caracteres (y a la peor calidad razonable si hace falta).
String _jpegQueQuepa(Uint8List original, int cupo) {
  var lado = ladoMaximoFoto;
  var calidad = calidadFoto;
  while (true) {
    final datos = base64.encode(comprimirFoto(original, ladoMaximo: lado, calidad: calidad));
    if (datos.length <= cupo) return datos;
    if (lado > 640) {
      lado = (lado * 0.8).round();
    } else if (calidad > 40) {
      calidad -= 15;
    } else {
      throw const FormatException('La foto es demasiado grande.');
    }
  }
}

/// Estado de una foto en la pantalla de fotos.
enum EstadoFoto {
  /// Guardada en el teléfono; falta confirmarla con la huella para subirla.
  sinConfirmar,

  /// Firmada y en la cola: se sube cuando haya señal.
  pendiente,

  /// El servidor la recibió.
  subida,

  /// El servidor la rechazó (queda en Pendientes con el motivo).
  error,
}

/// Una foto guardada en el teléfono.
class FotoCampo {
  final int id;
  final String verificacionId;
  final String ruta;
  final DateTime tomadaEn;
  final bool subida;

  /// Operación de la cola que la sube (nula si todavía no se confirmó).
  final int? operacionId;
  final double? latitud;
  final double? longitud;

  /// Estado calculado con la cola ([conEstado]).
  final EstadoFoto estado;
  final String? error;

  const FotoCampo({
    required this.id,
    required this.verificacionId,
    required this.ruta,
    required this.tomadaEn,
    this.subida = false,
    this.operacionId,
    this.latitud,
    this.longitud,
    this.estado = EstadoFoto.sinConfirmar,
    this.error,
  });

  FotoCampo conEstado(EstadoFoto e, [String? error]) => FotoCampo(
        id: id,
        verificacionId: verificacionId,
        ruta: ruta,
        tomadaEn: tomadaEn,
        subida: subida,
        operacionId: operacionId,
        latitud: latitud,
        longitud: longitud,
        estado: e,
        error: error,
      );

  String get nombreArchivo => ruta.split(RegExp(r'[/\\]')).last;
}
