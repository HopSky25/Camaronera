import 'package:flutter/material.dart';

import 'base_local.dart';

/// Modo de color que elige la persona: el del teléfono, claro u oscuro.
enum ModoTema {
  sistema('Como el teléfono', 'Claro u oscuro según el ajuste del sistema', Icons.brightness_auto_outlined),
  claro('Claro', 'Fondo claro, para trabajar al sol', Icons.light_mode_outlined),
  oscuro('Oscuro', 'Fondo oscuro, para la noche', Icons.dark_mode_outlined);

  final String nombre;
  final String descripcion;
  final IconData icono;
  const ModoTema(this.nombre, this.descripcion, this.icono);

  ThemeMode get themeMode => switch (this) {
        ModoTema.sistema => ThemeMode.system,
        ModoTema.claro => ThemeMode.light,
        ModoTema.oscuro => ThemeMode.dark,
      };

  /// Valor guardado → modo; lo desconocido o vacío es «sistema».
  static ModoTema deTexto(String? texto) =>
      ModoTema.values.firstWhere((m) => m.name == texto, orElse: () => ModoTema.sistema);
}

/// Preferencia de tema (día / noche), guardada en el teléfono (tabla config
/// de [BaseLocal], clave [clave]). Por defecto sigue al sistema. No se borra
/// al cerrar sesión: es del teléfono, no de la cuenta.
class PreferenciaTema extends ChangeNotifier {
  static const clave = 'tema';

  final BaseLocal _bd;
  ModoTema _modo = ModoTema.sistema;

  PreferenciaTema({BaseLocal? bd}) : _bd = bd ?? BaseLocal();

  ModoTema get modo => _modo;
  ThemeMode get themeMode => _modo.themeMode;

  /// Lee la preferencia guardada (si la base no abre, se queda en «sistema»).
  Future<void> cargar() async {
    try {
      final guardado = ModoTema.deTexto(await _bd.leerConfig(clave));
      if (guardado != _modo) {
        _modo = guardado;
        notifyListeners();
      }
    } catch (e) {
      debugPrint('No se pudo leer la preferencia de tema: $e');
    }
  }

  /// Cambia el modo al momento y lo guarda.
  Future<void> elegir(ModoTema modo) async {
    if (modo != _modo) {
      _modo = modo;
      notifyListeners();
    }
    try {
      await _bd.escribirConfig(clave, modo == ModoTema.sistema ? null : modo.name);
    } catch (e) {
      debugPrint('No se pudo guardar la preferencia de tema: $e');
    }
  }
}
