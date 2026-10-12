import 'dart:convert';

/// En qué punto está el registro del teléfono en el servidor.
enum EstadoRegistro {
  /// `POST /me/devices` respondió 201: hay `device_id`.
  registrado,

  /// Las claves están creadas en el teléfono pero no había señal para el
  /// primer registro: se firma igual (la firma no incluye el device_id) y se
  /// registra en la próxima sincronización, antes de enviar nada firmado.
  pendiente,
}

/// El teléfono registrado para firmar operaciones (contrato, sección 2).
class Dispositivo {
  final String? id;
  final String nombre;
  final DateTime? creadoEn;

  /// [huellaDeClave] de la clave pública registrada.
  final String huellaClave;
  final EstadoRegistro estado;

  const Dispositivo({this.id, required this.nombre, this.creadoEn, required this.huellaClave, required this.estado});

  /// Hay `device_id` para mandar en X-Device-Id.
  bool get listo => estado == EstadoRegistro.registrado && (id ?? '').isNotEmpty;

  String aJson() => jsonEncode({
        'id': id,
        'name': nombre,
        'created_at': creadoEn?.toUtc().toIso8601String(),
        'huella': huellaClave,
        'estado': estado.name,
      });

  static Dispositivo? desdeJson(String? texto) {
    if (texto == null || texto.isEmpty) return null;
    try {
      final m = jsonDecode(texto) as Map<String, dynamic>;
      return Dispositivo(
        id: m['id'] as String?,
        nombre: m['name'] as String? ?? 'Este teléfono',
        creadoEn: DateTime.tryParse(m['created_at'] as String? ?? '')?.toLocal(),
        huellaClave: m['huella'] as String,
        estado: EstadoRegistro.values.byName(m['estado'] as String),
      );
    } catch (_) {
      return null; // dañado: se vuelve a registrar
    }
  }
}
