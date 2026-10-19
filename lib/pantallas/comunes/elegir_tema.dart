import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/preferencia_tema.dart';
import '../../tema.dart';

/// Diálogo «Tema»: como el teléfono, claro u oscuro. Se aplica al momento y
/// queda guardado en el teléfono.
Future<void> elegirTema(BuildContext context) async {
  final pref = context.read<PreferenciaTema>();
  await showDialog<void>(
    context: context,
    builder: (c) => AlertDialog(
      icon: const Icon(Icons.contrast),
      title: const Text('Tema de la app'),
      contentPadding: const EdgeInsets.fromLTRB(12, 16, 12, 8),
      content: ListenableBuilder(
        listenable: pref,
        builder: (c, _) => Column(mainAxisSize: MainAxisSize.min, children: [
          for (final m in ModoTema.values) _OpcionTema(modo: m, elegido: pref.modo == m, onTap: () => pref.elegir(m)),
        ]),
      ),
      actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('Listo'))],
    ),
  );
}

class _OpcionTema extends StatelessWidget {
  final ModoTema modo;
  final bool elegido;
  final VoidCallback onTap;
  const _OpcionTema({required this.modo, required this.elegido, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        color: elegido ? c.marcaSuave : Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Radios.campo),
          side: BorderSide(color: elegido ? c.marcaTexto : c.borde),
        ),
        clipBehavior: Clip.antiAlias,
        child: ListTile(
          selected: elegido,
          onTap: onTap,
          leading: Icon(modo.icono),
          title: Text(modo.nombre),
          subtitle: Text(modo.descripcion),
          trailing: elegido ? Icon(Icons.check_circle, color: c.marcaTexto) : null,
        ),
      ),
    );
  }
}

/// Botón con el icono del modo actual que abre [elegirTema] (pantalla de
/// entrar, donde no hay menú de la cuenta).
class BotonTema extends StatelessWidget {
  final Color? color;
  const BotonTema({super.key, this.color});

  @override
  Widget build(BuildContext context) {
    final modo = context.watch<PreferenciaTema>().modo;
    return IconButton(
      tooltip: 'Tema: ${modo.nombre.toLowerCase()}',
      color: color,
      onPressed: () => elegirTema(context),
      icon: Icon(modo.icono),
    );
  }
}
