import 'dart:async';

import 'package:flutter/material.dart';

import '../datos/sesion.dart';
import '../modelos/perfil.dart';
import '../pantallas/comunes/pantalla_avisos.dart';
import '../pantallas/comunes/pantalla_despachos.dart';
import '../pantallas/comunes/pantalla_informes.dart';
import '../pantallas/comunes/pantalla_transacciones.dart';
import '../pantallas/empacadora/pantalla_catalogo.dart';
import '../pantallas/maquilador/pantalla_ordenes.dart';
import '../pantallas/maquilador/pantalla_solicitudes.dart';
import '../pantallas/pantalla_verificacion.dart';
import 'destinos.dart';

/// La pantalla que abre cada destino. La verificación se abre con el flujo
/// del técnico / la verificadora (bandeja local); para el resto de perfiles,
/// como informe de las partes.
Widget pantallaDe(Destino d, {String? perfil}) {
  final id = d.id ?? '';
  return switch (d.tipo) {
    TipoDestino.informePorAceptar => PantallaInformePartes(id: id),
    TipoDestino.verificacion =>
      perfil == perfilTecnico || perfil == 'verificador' ? PantallaVerificacion(id: id) : PantallaInformePartes(id: id),
    TipoDestino.compra || TipoDestino.venta || TipoDestino.despachoDeCompra => PantallaTransaccion(id: id),
    TipoDestino.despacho => PantallaDespacho(id: id),
    TipoDestino.ordenEmpaque => PantallaOrden(id: id),
    TipoDestino.lote => PantallaLotePublico(id: id),
    TipoDestino.solicitudesEmpaque => const PantallaSolicitudes(),
    TipoDestino.avisos => const PantallaAvisos(),
  };
}

/// Abre los enlaces directos (C6) y los avisos tocados (C3). Si la app está
/// bloqueada, sin sesión, protegiendo la cuenta o eligiendo perfil, el
/// destino queda pendiente y se abre en cuanto la persona entra / desbloquea.
/// Un enlace que no se reconoce (destino nulo) lleva al inicio.
class Enrutador {
  final Sesion sesion;
  final GlobalKey<NavigatorState> navegador;

  /// Se llama antes de abrir una verificación (refrescar la bandeja local).
  final void Function()? alAbrirVerificacion;

  Enrutador({required this.sesion, required this.navegador, this.alAbrirVerificacion}) {
    sesion.addListener(_intentar);
  }

  /// Destino esperando a que se pueda navegar.
  Destino? pendiente;
  bool _hayPendiente = false;

  /// Se puede navegar: sesión abierta, desbloqueada, protegida, con perfil
  /// (y, en modo supervisor, con un socio elegido).
  bool get listo =>
      sesion.apiOperativa != null &&
      !sesion.bloqueada &&
      !sesion.necesitaProteger &&
      !sesion.necesitaElegirPerfil &&
      sesion.perfil != null &&
      !(sesion.esSupervisor && sesion.comoSocio == null);

  /// Abre [d] (o el inicio si es nulo) ahora o en cuanto se pueda.
  void ir(Destino? d) {
    pendiente = d;
    _hayPendiente = true;
    _intentar();
  }

  void _intentar() {
    if (!_hayPendiente || !listo) return;
    final d = pendiente;
    _hayPendiente = false;
    pendiente = null;
    // Tras entrar / cambiar de perfil la raíz vuelve al inicio en el
    // siguiente cuadro: se abre después, para no quedar debajo.
    unawaited(Future<void>.delayed(const Duration(milliseconds: 350), () => _abrir(d)));
  }

  void _abrir(Destino? d) {
    final nav = navegador.currentState;
    if (nav == null) return;
    nav.popUntil((r) => r.isFirst);
    if (d == null) return; // ruta desconocida → inicio
    if (d.tipo == TipoDestino.verificacion) alAbrirVerificacion?.call();
    nav.push(MaterialPageRoute(
      settings: RouteSettings(name: 'enlace/${d.tipo.name}'),
      builder: (_) => pantallaDe(d, perfil: sesion.perfil),
    ));
  }

  void dispose() => sesion.removeListener(_intentar);
}
