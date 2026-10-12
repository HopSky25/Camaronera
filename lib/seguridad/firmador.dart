import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';

/// Fallo de la parte segura del teléfono (huella, PIN, Keystore). [codigo] es
/// el que devuelve el canal nativo (SeguridadDispositivo.kt).
class ErrorSeguridad implements Exception {
  /// cancelado, sin_bloqueo, sin_biometria, bloqueado_intentos,
  /// clave_invalidada, sin_claves, no_autenticado, datos_danados, ocupado,
  /// no_disponible, error.
  final String codigo;
  final String detalle;
  const ErrorSeguridad(this.codigo, [this.detalle = '']);

  bool get cancelado => codigo == 'cancelado';

  /// La clave del Keystore ya no sirve (se añadió una huella o se quitó el
  /// bloqueo de pantalla): hay que proteger la cuenta de nuevo.
  bool get claveInvalidada => codigo == 'clave_invalidada' || codigo == 'sin_claves' || codigo == 'datos_danados';

  String get mensaje => switch (codigo) {
        'cancelado' => 'No se confirmó con tu huella o PIN, así que no se hizo nada.',
        'sin_bloqueo' => 'El teléfono no tiene bloqueo de pantalla. Actívalo en Ajustes > Seguridad.',
        'sin_biometria' => 'La huella no está disponible. Usa el PIN del teléfono.',
        'bloqueado_intentos' => 'Demasiados intentos con la huella. Usa el PIN del teléfono o espera un momento.',
        'clave_invalidada' || 'sin_claves' || 'datos_danados' =>
          'Cambiaron las huellas o el bloqueo del teléfono. Por seguridad, vuelve a entrar con tu correo y contraseña.',
        'no_autenticado' => 'No se confirmó a tiempo. Vuelve a intentarlo.',
        'ocupado' => 'Ya hay una confirmación abierta.',
        'no_disponible' => 'Este teléfono no permite proteger la cuenta (falta el almacén seguro de claves).',
        _ => detalle.isEmpty ? 'No se pudo usar la seguridad del teléfono.' : 'No se pudo usar la seguridad del teléfono: $detalle',
      };

  @override
  String toString() => 'ErrorSeguridad($codigo): $detalle';
}

/// Qué protección tiene el teléfono.
class EstadoProteccion {
  /// Hay PIN, patrón o contraseña de pantalla (sin esto no se puede seguir).
  final bool bloqueoPantalla;

  /// disponible, sin_inscribir, sin_hardware, no_disponible.
  final String biometria;
  final int sdk;
  final String fabricante;
  final String modelo;
  final bool tieneClaveFirma;
  final bool tieneClaveCifrado;

  const EstadoProteccion({
    required this.bloqueoPantalla,
    required this.biometria,
    this.sdk = 0,
    this.fabricante = '',
    this.modelo = '',
    this.tieneClaveFirma = false,
    this.tieneClaveCifrado = false,
  });

  bool get hayHuella => biometria == 'disponible';

  factory EstadoProteccion.desde(Map<Object?, Object?> m) => EstadoProteccion(
        bloqueoPantalla: m['bloqueo'] == true,
        biometria: m['biometria'] is String ? m['biometria'] as String : 'no_disponible',
        sdk: m['sdk'] is int ? m['sdk'] as int : 0,
        fabricante: m['fabricante'] is String ? m['fabricante'] as String : '',
        modelo: m['modelo'] is String ? m['modelo'] as String : '',
        tieneClaveFirma: m['tieneClaveFirma'] == true,
        tieneClaveCifrado: m['tieneClaveCifrado'] == true,
      );
}

/// La parte segura del teléfono, detrás de una interfaz para poder probar el
/// resto de la app con un firmador falso.
///
/// La implementación real ([FirmadorAndroid]) usa dos claves del Android
/// Keystore, no exportables, que solo se usan tras la huella fuerte o el PIN
/// del teléfono: un par ECDSA P-256 (ES256) para firmar y una AES-256-GCM para
/// cifrar la clave de API.
abstract class Firmador {
  Future<EstadoProteccion> estado();

  /// Crea las claves que falten y devuelve la clave pública vigente:
  /// SubjectPublicKeyInfo DER en base64 estándar.
  Future<String> prepararClaves();

  /// Clave pública vigente (SPKI DER base64) o nula si no hay par de firma.
  Future<String?> clavePublica();

  /// "Registrar de nuevo" (contrato 1.1): crea un par NUEVO sin tocar el
  /// vigente (que todavía tiene que firmar la sustitución) y devuelve su
  /// clave pública.
  Future<String> prepararClaveNueva();

  /// Tras el 201: el par nuevo pasa a ser el vigente y el viejo se borra.
  Future<void> activarClaveNueva();

  /// La sustitución falló: se borra el par nuevo y sigue el vigente.
  Future<void> descartarClaveNueva();

  /// Pide la huella (o el PIN con [conPin]) y firma [datos] con ES256 con el
  /// par vigente.
  /// Devuelve la firma DER (ASN.1) en base64 estándar.
  Future<String> firmar(Uint8List datos, {required String titulo, String? subtitulo, bool conPin = false});

  /// Pide la huella / PIN y cifra [texto] (AES-GCM). Devuelve base64(iv+cifrado).
  Future<String> cifrar(String texto, {required String titulo, String? subtitulo, bool conPin = false});

  /// Pide la huella / PIN y descifra lo que devolvió [cifrar].
  Future<String> descifrar(String datos, {required String titulo, String? subtitulo, bool conPin = false});

  /// Borra solo el par de firma (dispositivo revocado).
  Future<void> borrarClaveFirma();

  /// Borra las dos claves (cerrar sesión).
  Future<void> borrarClaves();

  Future<void> abrirAjustesSeguridad();
}

/// Huella corta de una clave pública: identifica con qué par se firmó cada
/// operación de la cola (si el par cambia, hay que volver a firmar).
String huellaDeClave(String spkiBase64) => sha256.convert(base64.decode(spkiBase64)).toString().substring(0, 16);

/// Firmador real: canal nativo `com.camaronmarket/seguridad` (Android).
class FirmadorAndroid implements Firmador {
  static const _canal = MethodChannel('com.camaronmarket/seguridad');

  Future<T> _llamar<T>(String metodo, [Map<String, Object?>? args]) async {
    try {
      return await _canal.invokeMethod<T>(metodo, args) as T;
    } on PlatformException catch (e) {
      throw ErrorSeguridad(e.code, e.message ?? '');
    } on MissingPluginException {
      throw const ErrorSeguridad('no_disponible');
    }
  }

  @override
  Future<EstadoProteccion> estado() async =>
      EstadoProteccion.desde(await _llamar<Map<Object?, Object?>>('estado'));

  @override
  Future<String> prepararClaves() => _llamar<String>('prepararClaves');

  @override
  Future<String?> clavePublica() => _llamar<String?>('clavePublica');

  @override
  Future<String> prepararClaveNueva() => _llamar<String>('prepararClaveNueva');

  @override
  Future<void> activarClaveNueva() => _llamar<void>('activarClaveNueva');

  @override
  Future<void> descartarClaveNueva() => _llamar<void>('descartarClaveNueva');

  @override
  Future<String> firmar(Uint8List datos, {required String titulo, String? subtitulo, bool conPin = false}) =>
      _llamar<String>('firmar', {'datos': datos, 'titulo': titulo, 'subtitulo': subtitulo, 'conPin': conPin});

  @override
  Future<String> cifrar(String texto, {required String titulo, String? subtitulo, bool conPin = false}) =>
      _llamar<String>('cifrar', {'texto': texto, 'titulo': titulo, 'subtitulo': subtitulo, 'conPin': conPin});

  @override
  Future<String> descifrar(String datos, {required String titulo, String? subtitulo, bool conPin = false}) =>
      _llamar<String>('descifrar', {'datos': datos, 'titulo': titulo, 'subtitulo': subtitulo, 'conPin': conPin});

  @override
  Future<void> borrarClaveFirma() => _llamar<void>('borrarClaveFirma');

  @override
  Future<void> borrarClaves() => _llamar<void>('borrarClaves');

  @override
  Future<void> abrirAjustesSeguridad() => _llamar<void>('abrirAjustesSeguridad');
}
