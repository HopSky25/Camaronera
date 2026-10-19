import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/perfil.dart';
import '../../tema.dart';
import '../pantalla_pendientes.dart';
import '../seguridad/pantalla_seguridad.dart';
import '../supervisor/franja_supervisor.dart';
import 'pantalla_avisos.dart';

/// Barra superior común a todos los perfiles: logo, avisos, pendientes de
/// enviar, sincronizar y el menú de la cuenta (cambiar perfil, datos y privacidad,
/// cerrar sesión).
class BarraCuenta extends StatelessWidget implements PreferredSizeWidget {
  final String? titulo;
  const BarraCuenta({super.key, this.titulo});

  @override
  Size get preferredSize => const Size.fromHeight(kToolbarHeight);

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    return AppBar(
      titleSpacing: 16,
      title: titulo == null ? const LogoMarca() : Text(titulo!),
      actions: [
        const CampanaAvisos(),
        // Modo supervisor: no hay nada que enviar.
        if (!soloLectura)
          IconButton(
            tooltip: 'Pendientes de enviar',
            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaPendientes())),
            icon: Badge(
              isLabelVisible: sinc.pendientes > 0,
              backgroundColor: context.colores.advertencia,
              label: Text('${sinc.pendientes}'),
              child: const Icon(Icons.cloud_upload_outlined),
            ),
          ),
        IconButton(
          tooltip: 'Sincronizar',
          onPressed: sinc.sincronizando ? null : () => sinc.sincronizar(),
          icon: sinc.sincronizando
              ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.sync),
        ),
        const MenuCuenta(),
      ],
    );
  }
}

class MenuCuenta extends StatelessWidget {
  const MenuCuenta({super.key});

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final puedeCambiar = !sesion.esTecnico && (sesion.yo?.aprobados.length ?? 0) > 1;
    final supervisor = sesion.soloLectura;
    return PopupMenuButton<String>(
      icon: const Icon(Icons.account_circle_outlined),
      onSelected: (v) => _accion(context, v),
      itemBuilder: (_) => [
        PopupMenuItem(
          enabled: false,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(sesion.nombre, style: TextStyle(fontWeight: FontWeight.w700, color: context.colores.titulo)),
            Text(
                supervisor
                    ? '${nombreDePerfil(sesion.perfil ?? '')} · solo lectura'
                    : nombreDePerfil(sesion.perfil ?? ''),
                style: TextStyle(fontSize: 12.5, color: context.colores.tintaSuave)),
            if (supervisor && sesion.yoPropio != null)
              Text('Supervisor: ${sesion.yoPropio!.nombre}', style: TextStyle(fontSize: 12, color: context.colores.tintaSuave)),
          ]),
        ),
        const PopupMenuDivider(),
        // Cambiar de perfil solo cambia X-Shrimp-Role (lectura), también en modo supervisor.
        if (puedeCambiar)
          const PopupMenuItem(value: 'perfil', child: ListTile(leading: Icon(Icons.swap_horiz), title: Text('Cambiar de perfil'))),
        if (supervisor)
          const PopupMenuItem(value: 'socio', child: ListTile(leading: Icon(Icons.groups_outlined), title: Text('Cambiar de socio'))),
        const PopupMenuItem(value: 'seguridad', child: ListTile(leading: Icon(Icons.shield_outlined), title: Text('Seguridad'))),
        if (!supervisor)
          const PopupMenuItem(value: 'datos', child: ListTile(leading: Icon(Icons.privacy_tip_outlined), title: Text('Mis datos y privacidad'))),
        if (supervisor)
          const PopupMenuItem(value: 'supervisor', child: ListTile(leading: Icon(Icons.logout), title: Text('Salir del modo supervisor')))
        else
          const PopupMenuItem(value: 'salir', child: ListTile(leading: Icon(Icons.logout), title: Text('Cerrar sesión'))),
      ],
    );
  }

  Future<void> _accion(BuildContext context, String v) async {
    final sesion = context.read<Sesion>();
    final sinc = context.read<Sincronizador>();
    switch (v) {
      case 'perfil':
        Navigator.popUntil(context, (r) => r.isFirst);
        await sesion.volverAElegirPerfil();
      case 'socio':
        Navigator.popUntil(context, (r) => r.isFirst);
        await sesion.cambiarDeSocio();
      case 'supervisor':
        await confirmarSalirSupervisor(context);
      case 'seguridad':
        await Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaSeguridad()));
      case 'datos':
        await abrirMisDatos(context, sesion.url);
      case 'salir':
        final ok = await showDialog<bool>(
          context: context,
          builder: (c) => AlertDialog(
            title: const Text('Cerrar sesión'),
            content: Text(sinc.pendientes > 0
                ? 'Tienes ${sinc.pendientes} cambios sin enviar. Si cierras la sesión se pierden.'
                : 'Se borrará la sesión y los datos guardados en el teléfono.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
              FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cerrar sesión')),
            ],
          ),
        );
        if (ok == true && context.mounted) {
          Navigator.popUntil(context, (r) => r.isFirst);
          await sesion.cerrarSesion();
        }
    }
  }
}

/// "Mis datos y privacidad" vive en el portal: `https://<servidor>/my/datos`
Future<void> abrirMisDatos(BuildContext context, String servidor) => abrirEnlace(context, '$servidor/my/datos');

/// Abre una dirección en el navegador del teléfono; si no hay navegador o la
/// dirección no vale, lo dice sin tumbar la app.
Future<void> abrirEnlace(BuildContext context, String url) async {
  final uri = Uri.tryParse(url);
  var ok = false;
  try {
    ok = uri != null && await launchUrl(uri, mode: LaunchMode.externalApplication);
  } catch (_) {
    ok = false;
  }
  if (!ok && context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('No se pudo abrir el enlace en el navegador.')));
  }
}

/// Cabecera del panel: nombre, perfil activo y botón para cambiar de perfil.
class FranjaPerfil extends StatelessWidget {
  const FranjaPerfil({super.key});

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final rol = sesion.perfil ?? '';
    final puedeCambiar = !sesion.esTecnico && (sesion.yo?.aprobados.length ?? 0) > 1;
    final avisoCaduca = sesion.avisoCaducidadProxima;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
      decoration: const BoxDecoration(gradient: ColoresMarca.degradado),
      child: Column(children: [
        Row(children: [
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(12)),
          child: Icon(iconoDePerfil(rol), color: Colors.white),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(sesion.nombre, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w800)),
            Text(
              sesion.esTecnico && sesion.yo?.empresa != null
                  ? '${nombreDePerfil(rol)} · ${sesion.yo!.empresa!.nombre}${sesion.soloLectura ? ' · solo lectura' : ''}'
                  : '${nombreDePerfil(rol)}${sesion.soloLectura ? ' · solo lectura' : ''}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: ColoresMarca.sobreBandaSuave, fontSize: 13),
            ),
          ]),
        ),
        if (puedeCambiar)
          TextButton.icon(
            style: TextButton.styleFrom(foregroundColor: Colors.white),
            onPressed: () async {
              Navigator.popUntil(context, (r) => r.isFirst);
              await sesion.volverAElegirPerfil();
            },
            icon: const Icon(Icons.swap_horiz, size: 18),
            label: const Text('Cambiar'),
          ),
        ]),
        if (avisoCaduca != null)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Row(children: [
              const Icon(Icons.timer_outlined, size: 16, color: ColoresMarca.sobreBandaAviso),
              const SizedBox(width: 6),
              Expanded(child: Text(avisoCaduca, style: const TextStyle(color: ColoresMarca.sobreBandaAviso, fontSize: 12.5, fontWeight: FontWeight.w600))),
            ]),
          ),
      ]),
    );
  }
}
