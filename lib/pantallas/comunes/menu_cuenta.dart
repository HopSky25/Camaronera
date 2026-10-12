import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/perfil.dart';
import '../../tema.dart';
import '../pantalla_pendientes.dart';

/// Barra superior común a todos los perfiles: logo, pendientes de enviar,
/// sincronizar y el menú de la cuenta (cambiar perfil, datos y privacidad,
/// cerrar sesión).
class BarraCuenta extends StatelessWidget implements PreferredSizeWidget {
  final String? titulo;
  const BarraCuenta({super.key, this.titulo});

  @override
  Size get preferredSize => const Size.fromHeight(kToolbarHeight);

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    return AppBar(
      titleSpacing: 16,
      title: titulo == null ? Image.asset('assets/logo.png', height: 30) : Text(titulo!),
      actions: [
        IconButton(
          tooltip: 'Pendientes de enviar',
          onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaPendientes())),
          icon: Badge(
            isLabelVisible: sinc.pendientes > 0,
            backgroundColor: Marca.ambar,
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
    return PopupMenuButton<String>(
      icon: const Icon(Icons.account_circle_outlined),
      onSelected: (v) => _accion(context, v),
      itemBuilder: (_) => [
        PopupMenuItem(
          enabled: false,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(sesion.nombre, style: const TextStyle(fontWeight: FontWeight.w700, color: Marca.navy)),
            Text(nombreDePerfil(sesion.perfil ?? ''), style: const TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
          ]),
        ),
        const PopupMenuDivider(),
        if (puedeCambiar)
          const PopupMenuItem(value: 'perfil', child: ListTile(leading: Icon(Icons.swap_horiz), title: Text('Cambiar de perfil'))),
        const PopupMenuItem(value: 'datos', child: ListTile(leading: Icon(Icons.privacy_tip_outlined), title: Text('Mis datos y privacidad'))),
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
Future<void> abrirMisDatos(BuildContext context, String servidor) async {
  final uri = Uri.parse('$servidor/my/datos');
  final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
  if (!ok && context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('No se pudo abrir $uri')));
  }
}

Future<void> abrirEnlace(BuildContext context, String url) async {
  final uri = Uri.tryParse(url);
  final ok = uri != null && await launchUrl(uri, mode: LaunchMode.externalApplication);
  if (!ok && context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('No se pudo abrir $url')));
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
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
      decoration: const BoxDecoration(gradient: Marca.degradado),
      child: Row(children: [
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
                  ? '${nombreDePerfil(rol)} · ${sesion.yo!.empresa!.nombre}'
                  : nombreDePerfil(rol),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 13),
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
    );
  }
}
