// Firmador falso para las pruebas: hace lo mismo que el Keystore (ECDSA P-256
// con SHA-256, firma DER, clave pública SPKI DER) pero en memoria, con
// pointycastle. Así las pruebas verifican firmas ES256 reales.
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';
import 'package:verificador_campo/seguridad/firmador.dart';

/// Cabecera DER fija de un SubjectPublicKeyInfo EC P-256 sin comprimir.
final _cabeceraSpki = _hex('3059301306072a8648ce3d020106082a8648ce3d030107034200');

Uint8List _hex(String h) => Uint8List.fromList([for (var i = 0; i < h.length; i += 2) int.parse(h.substring(i, i + 2), radix: 16)]);

Uint8List _bytesSinSigno(BigInt v, [int? largo]) {
  final hex = v.toRadixString(16);
  var b = _hex(hex.length.isOdd ? '0$hex' : hex);
  if (largo != null && b.length < largo) b = Uint8List.fromList([...List.filled(largo - b.length, 0), ...b]);
  return b;
}

BigInt _entero(List<int> b) => b.fold(BigInt.zero, (a, x) => (a << 8) | BigInt.from(x));

List<int> _derEntero(BigInt v) {
  var b = _bytesSinSigno(v);
  if (b[0] >= 0x80) b = Uint8List.fromList([0, ...b]);
  return [0x02, b.length, ...b];
}

/// Firma (r, s) → DER: SEQUENCE { INTEGER r, INTEGER s }.
Uint8List firmaDer(ECSignature f) {
  final cuerpo = [..._derEntero(f.r), ..._derEntero(f.s)];
  return Uint8List.fromList([0x30, cuerpo.length, ...cuerpo]);
}

/// DER → (r, s). Lanza si no es una SEQUENCE de dos INTEGER.
ECSignature leerFirmaDer(Uint8List der) {
  if (der[0] != 0x30 || der[1] != der.length - 2) throw const FormatException('No es una SEQUENCE DER');
  var i = 2;
  BigInt leer() {
    if (der[i] != 0x02) throw const FormatException('Se esperaba INTEGER');
    final n = der[i + 1];
    final v = _entero(der.sublist(i + 2, i + 2 + n));
    i += 2 + n;
    return v;
  }

  final r = leer();
  final s = leer();
  if (i != der.length) throw const FormatException('Sobran bytes');
  return ECSignature(r, s);
}

/// Verifica una firma ES256 (DER base64) con una clave pública SPKI DER
/// base64, como lo hará el servidor.
bool verificarEs256(String spkiBase64, List<int> datos, String firmaBase64) {
  final spki = base64.decode(spkiBase64);
  if (spki.length != 91) return false;
  for (var i = 0; i < _cabeceraSpki.length; i++) {
    if (spki[i] != _cabeceraSpki[i]) return false;
  }
  final dominio = ECCurve_secp256r1();
  final q = dominio.curve.decodePoint(spki.sublist(_cabeceraSpki.length))!;
  final verificador = ECDSASigner(SHA256Digest())..init(false, PublicKeyParameter(ECPublicKey(q, dominio)));
  try {
    return verificador.verifySignature(Uint8List.fromList(datos), leerFirmaDer(base64.decode(firmaBase64)));
  } on FormatException {
    return false;
  }
}

class FirmadorFalso implements Firmador {
  bool bloqueo = true;
  String biometria = 'disponible';

  /// Si no es nulo, la próxima confirmación falla con este código.
  String? fallarCon;

  /// Si es true, todas las confirmaciones fallan con este código (no solo la próxima).
  bool fallarSiempre = false;
  int confirmaciones = 0;
  bool? ultimoConPin;
  final firmados = <Uint8List>[];

  ECPrivateKey? _privada;
  ECPublicKey? _publica;

  /// Simula que cambiaron las huellas: la clave de firma deja de servir.
  bool firmaInvalidada = false;
  bool _tieneAes = false;
  int _generacion = 0;

  final _azar = FortunaRandom()..seed(KeyParameter(Uint8List.fromList(List.generate(32, (i) => Random(7).nextInt(256) ^ i))));

  void _confirmar(bool conPin) {
    confirmaciones++;
    ultimoConPin = conPin;
    final f = fallarCon;
    if (f != null) {
      if (!fallarSiempre) fallarCon = null;
      throw ErrorSeguridad(f);
    }
  }

  @override
  Future<EstadoProteccion> estado() async => EstadoProteccion(
        bloqueoPantalla: bloqueo,
        biometria: biometria,
        sdk: 34,
        fabricante: 'samsung',
        modelo: 'SM-A546E',
        tieneClaveFirma: _privada != null,
        tieneClaveCifrado: _tieneAes,
      );

  AsymmetricKeyPair<ECPublicKey, ECPrivateKey> _nuevoPar() {
    final gen = ECKeyGenerator()..init(ParametersWithRandom(ECKeyGeneratorParameters(ECCurve_secp256r1()), _azar));
    _generacion++;
    return gen.generateKeyPair();
  }

  @override
  Future<String> prepararClaves() async {
    if (!bloqueo) throw const ErrorSeguridad('sin_bloqueo');
    if (_privada == null) {
      final par = _nuevoPar();
      _privada = par.privateKey;
      _publica = par.publicKey;
    }
    _tieneAes = true;
    return (await clavePublica())!;
  }

  int get generacion => _generacion;

  static String _spki(ECPublicKey p) {
    final q = p.Q!;
    final punto = [0x04, ..._bytesSinSigno(q.x!.toBigInteger()!, 32), ..._bytesSinSigno(q.y!.toBigInteger()!, 32)];
    return base64.encode([..._cabeceraSpki, ...punto]);
  }

  @override
  Future<String?> clavePublica() async => _publica == null ? null : _spki(_publica!);

  AsymmetricKeyPair<ECPublicKey, ECPrivateKey>? _nueva;

  @override
  Future<String> prepararClaveNueva() async {
    _nueva = _nuevoPar();
    return _spki(_nueva!.publicKey);
  }

  @override
  Future<void> activarClaveNueva() async {
    final n = _nueva ?? (throw const ErrorSeguridad('sin_claves'));
    _privada = n.privateKey;
    _publica = n.publicKey;
    _nueva = null;
  }

  @override
  Future<void> descartarClaveNueva() async => _nueva = null;

  bool get hayClaveNueva => _nueva != null;

  @override
  Future<String> firmar(Uint8List datos, {required String titulo, String? subtitulo, bool conPin = false}) async {
    if (_privada == null) throw const ErrorSeguridad('sin_claves');
    if (firmaInvalidada) throw const ErrorSeguridad('clave_invalidada');
    _confirmar(conPin);
    firmados.add(datos);
    final firmador = ECDSASigner(SHA256Digest(), HMac(SHA256Digest(), 64))..init(true, PrivateKeyParameter(_privada!));
    return base64.encode(firmaDer(firmador.generateSignature(datos) as ECSignature));
  }

  static const _prefijo = 'cifrado-falso:';

  @override
  Future<String> cifrar(String texto, {required String titulo, String? subtitulo, bool conPin = false}) async {
    if (!_tieneAes) throw const ErrorSeguridad('sin_claves');
    _confirmar(conPin);
    return base64.encode(utf8.encode('$_prefijo$texto'));
  }

  @override
  Future<String> descifrar(String datos, {required String titulo, String? subtitulo, bool conPin = false}) async {
    if (!_tieneAes) throw const ErrorSeguridad('sin_claves');
    _confirmar(conPin);
    final texto = utf8.decode(base64.decode(datos));
    if (!texto.startsWith(_prefijo)) throw const ErrorSeguridad('datos_danados');
    return texto.substring(_prefijo.length);
  }

  @override
  Future<void> borrarClaveFirma() async {
    _privada = null;
    _publica = null;
  }

  @override
  Future<void> borrarClaves() async {
    await borrarClaveFirma();
    _tieneAes = false;
  }

  @override
  Future<void> abrirAjustesSeguridad() async {}
}
