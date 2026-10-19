/// Lo común de los formularios del vendedor: llevar la vista al primer
/// campo con error, el aviso en SnackBar y los `errors[].field` de un 422.
library;

import 'package:flutter/material.dart';

import '../../datos/api_cliente.dart';
import '../../tema.dart';

/// Lleva la vista al campo con error que está más arriba.
void irAlPrimerError(Set<FormFieldState<Object?>> campos) {
  if (campos.isEmpty) return;
  double alto(FormFieldState<Object?> c) {
    final r = c.context.findRenderObject();
    return r is RenderBox && r.attached ? r.localToGlobal(Offset.zero).dy : double.infinity;
  }

  final primero = campos.reduce((a, b) => alto(a) <= alto(b) ? a : b);
  if (!primero.mounted) return;
  Scrollable.ensureVisible(primero.context, duration: const Duration(milliseconds: 350), alignment: 0.2);
}

void avisar(BuildContext context, String texto, {bool error = false}) {
  if (!context.mounted) return;
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(texto),
      backgroundColor: error ? context.colores.peligro : null,
      duration: Duration(seconds: error ? 6 : 3),
    ));
}

/// Errores por campo de un 422, con las claves del formulario según [mapa].
Map<String, String> erroresPorCampo(ErrorApi e, Map<String, String> mapa) {
  final out = <String, String>{};
  final lista = e.datos['errors'];
  if (lista is! List) return out;
  for (final item in lista.whereType<Map>()) {
    final campo = mapa[item['field']];
    final msg = item['message'];
    if (campo != null && msg is String && msg.isNotEmpty) out.putIfAbsent(campo, () => msg);
  }
  return out;
}
