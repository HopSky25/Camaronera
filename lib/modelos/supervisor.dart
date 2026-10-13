/// Modo supervisor (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md, Anexo B): un
/// usuario interno con el grupo "Supervisor de la app" ve la app de
/// cualquier socio en solo lectura.
library;

import 'perfil.dart';
import 'verificacion.dart' show jsonTexto;

/// Texto que reemplaza a cada acción de escritura en modo supervisor.
const textoSoloLectura = 'Solo lectura en modo supervisor';

/// Tipos del filtro del directorio (`GET /supervisor/partners?type=`), en el
/// orden de los chips. El valor es el código del perfil en la plataforma.
const tiposDeSocio = ['camaronera', 'empacadora', 'verificador', 'maquilador', 'laboratorio', 'semillero'];

/// ¿La respuesta del login es de un supervisor? (`"supervisor": true` en la
/// raíz o en `me`).
bool esLoginSupervisor(Map<String, dynamic> resp) {
  final me = resp['me'];
  return resp['supervisor'] == true || (me is Map && me['supervisor'] == true);
}

/// Un socio del directorio (`GET /supervisor/partners`):
/// `{id, name, type, roles, city, province}`.
class SocioDirectorio {
  final String id;
  final String nombre;

  /// Perfil activo del socio (camaronera, empacadora...). Puede faltar.
  final String? tipo;

  /// Perfiles del socio (código). La API puede mandarlos como texto o como
  /// `{role, label, state}`.
  final List<String> perfiles;
  final String? ciudad;
  final String? provincia;

  const SocioDirectorio({
    required this.id,
    required this.nombre,
    this.tipo,
    this.perfiles = const [],
    this.ciudad,
    this.provincia,
  });

  /// Nulo si no trae `id` (no se podría actuar como él).
  static SocioDirectorio? desde(Map json) {
    final id = jsonTexto(json['id']);
    if (id == null || id.isEmpty) return null;
    final roles = json['roles'] is List ? json['roles'] as List : const [];
    return SocioDirectorio(
      id: id,
      nombre: jsonTexto(json['name']) ?? 'Socio sin nombre',
      tipo: jsonTexto(json['type']),
      perfiles: [
        for (final r in roles)
          if (r is String && r.isNotEmpty)
            r
          else if (r is Map && (jsonTexto(r['role']) ?? '').isNotEmpty)
            jsonTexto(r['role'])!,
      ],
      ciudad: jsonTexto(json['city']),
      provincia: jsonTexto(json['province']),
    );
  }

  /// "Machala, El Oro" (o lo que haya).
  String get lugar => [?ciudad, ?provincia].where((s) => s.trim().isNotEmpty).join(', ');

  String get tipoEtiqueta => tipo == null ? 'Sin perfil activo' : nombreDePerfil(tipo!);
}

/// Una página del directorio.
class PaginaSocios {
  final List<SocioDirectorio> socios;
  final String? siguienteCursor;
  const PaginaSocios(this.socios, this.siguienteCursor);
}

/// Respuesta de `POST /supervisor/act-as`:
/// `{api_key, expires_at, partner, me, read_only}`.
class AccesoComoSocio {
  final String clave;
  final DateTime? caducaEn;
  final Map<String, dynamic> me;
  final Map<String, dynamic>? socio;
  final bool soloLectura;

  /// `me.viewed_by.name`: el supervisor que está mirando (para la franja).
  final String? vistoPor;
  const AccesoComoSocio(
      {required this.clave, required this.me, this.caducaEn, this.socio, this.soloLectura = true, this.vistoPor});

  /// Un reintento idempotente de act-as devuelve `"api_key": null` (el
  /// servidor no guarda el secreto): hay que hacer un act-as nuevo.
  static bool sinClave(Map<String, dynamic> r) => (jsonTexto(r['api_key']) ?? '').isEmpty;

  /// Lanza [FormatException] si falta la clave o `me`.
  factory AccesoComoSocio.desde(Map<String, dynamic> r) {
    final clave = jsonTexto(r['api_key']);
    final me = r['me'];
    if (clave == null || clave.isEmpty || me is! Map<String, dynamic>) {
      throw const FormatException('Respuesta de act-as sin api_key o sin me');
    }
    final visto = me['viewed_by'];
    return AccesoComoSocio(
      clave: clave,
      me: me,
      vistoPor: visto is Map ? jsonTexto(visto['name']) : null,
      caducaEn: DateTime.tryParse(jsonTexto(r['expires_at']) ?? '')?.toLocal(),
      socio: r['partner'] is Map<String, dynamic> ? r['partner'] as Map<String, dynamic> : null,
      // El servidor siempre la emite de solo lectura; si no lo dice, igual.
      soloLectura: r['read_only'] != false,
    );
  }
}
