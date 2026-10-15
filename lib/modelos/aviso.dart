/// Un aviso de la bandeja (`GET /me/notifications`, contrato C3).
class Aviso {
  final String id;

  /// `kind` del servidor (verification_assigned, report_ready, …).
  final String tipo;
  final String titulo;
  final String cuerpo;

  /// `target: {type, id}`: a qué apunta (verificación, compra, despacho…).
  final String? destinoTipo;
  final String? destinoId;

  /// `link`: ruta web del destino (la misma de los enlaces directos, C6).
  final String? enlace;
  final DateTime? creadoEn;
  final bool leido;

  const Aviso({
    required this.id,
    this.tipo = '',
    required this.titulo,
    this.cuerpo = '',
    this.destinoTipo,
    this.destinoId,
    this.enlace,
    this.creadoEn,
    this.leido = false,
  });

  static String? _texto(dynamic v) => v is String && v.trim().isNotEmpty ? v.trim() : null;

  /// Nulo si no trae `id` (no se podría marcar como leído).
  static Aviso? desde(Map<String, dynamic> m) {
    final id = _texto(m['id']) ?? (m['id'] is int ? '${m['id']}' : null);
    if (id == null) return null;
    final destino = m['target'] is Map ? m['target'] as Map : const {};
    return Aviso(
      id: id,
      tipo: _texto(m['kind']) ?? '',
      titulo: _texto(m['title']) ?? 'Aviso de CamaronMarket',
      cuerpo: _texto(m['body']) ?? '',
      destinoTipo: _texto(destino['type']),
      destinoId: _texto(destino['id']) ?? (destino['id'] is int ? '${destino['id']}' : null),
      enlace: _texto(m['link']),
      creadoEn: DateTime.tryParse(_texto(m['created_at']) ?? '')?.toLocal(),
      leido: m['read'] == true,
    );
  }

  Aviso comoLeido() => Aviso(
        id: id,
        tipo: tipo,
        titulo: titulo,
        cuerpo: cuerpo,
        destinoTipo: destinoTipo,
        destinoId: destinoId,
        enlace: enlace,
        creadoEn: creadoEn,
        leido: true,
      );
}

/// Una página de avisos.
class PaginaAvisos {
  final List<Aviso> avisos;
  final String? siguienteCursor;

  /// Total que informa el servidor en `meta` (si lo informa).
  final int? total;
  const PaginaAvisos(this.avisos, this.siguienteCursor, {this.total});
}
