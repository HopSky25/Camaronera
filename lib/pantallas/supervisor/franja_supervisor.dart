import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/sesion.dart';
import '../../tema.dart';

/// Navegador de la app: la franja vive por encima de él (en
/// `MaterialApp.builder`) y lo usa para sus diálogos.
final claveNavegador = GlobalKey<NavigatorState>();

/// Pone la franja del modo supervisor por encima de TODAS las pantallas
/// (también las abiertas encima del panel y los diálogos) mientras se ve la
/// app de un socio. El árbol de debajo no cambia de sitio al aparecer o
/// desaparecer la franja, así no se pierde lo que estaba abierto.
class CapaSupervisor extends StatelessWidget {
  final Widget child;
  const CapaSupervisor({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    final (socio, supervisor) = context.select<Sesion, (String?, String?)>((s) => (s.comoSocio?.nombre, s.comoSocio?.vistoPor));
    return Column(children: [
      if (socio != null) FranjaSupervisor(socio: socio, supervisor: supervisor),
      Expanded(
        key: const ValueKey('app'),
        // La franja ya ocupa la barra de estado.
        child: MediaQuery.removePadding(context: context, removeTop: socio != null, child: child),
      ),
    ]);
  }
}

/// "Viendo como `socio` · solo lectura", con "Cambiar de socio" y "Salir del
/// modo supervisor".
class FranjaSupervisor extends StatelessWidget {
  final String socio;

  /// Quién mira (`me.viewed_by.name` del act-as).
  final String? supervisor;
  const FranjaSupervisor({super.key, required this.socio, this.supervisor});

  static const _amarillo = ColoresMarca.sobreBandaAviso;

  @override
  Widget build(BuildContext context) {
    final estiloBoton = TextButton.styleFrom(
      foregroundColor: ColoresMarca.sobreBanda,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      minimumSize: const Size(0, 36),
      textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
    );
    return Material(
      color: ColoresMarca.banda,
      child: SafeArea(
        bottom: false,
        child: Container(
          width: double.infinity,
          decoration: const BoxDecoration(border: Border(left: BorderSide(color: _amarillo, width: 4))),
          padding: const EdgeInsets.fromLTRB(12, 8, 6, 4),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Row(children: [
              const Icon(Icons.visibility_outlined, size: 18, color: _amarillo),
              const SizedBox(width: 8),
              Expanded(
                child: Text.rich(
                  TextSpan(children: [
                    const TextSpan(text: 'Viendo como '),
                    TextSpan(text: socio, style: const TextStyle(fontWeight: FontWeight.w800)),
                    const TextSpan(text: ' · solo lectura', style: TextStyle(color: _amarillo, fontWeight: FontWeight.w700)),
                    if (supervisor != null && supervisor!.isNotEmpty)
                      TextSpan(text: '\nSupervisor: $supervisor', style: const TextStyle(fontSize: 12.5, color: ColoresMarca.sobreBandaSuave)),
                  ]),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: ColoresMarca.sobreBanda, fontSize: 14),
                ),
              ),
            ]),
            Align(
              alignment: Alignment.centerRight,
              child: Wrap(spacing: 4, children: [
                TextButton.icon(
                  style: estiloBoton,
                  onPressed: () => context.read<Sesion>().cambiarDeSocio(),
                  icon: const Icon(Icons.groups_outlined, size: 18),
                  label: const Text('Cambiar de socio'),
                ),
                TextButton.icon(
                  style: estiloBoton,
                  onPressed: () => confirmarSalirSupervisor(claveNavegador.currentContext ?? context),
                  icon: const Icon(Icons.logout, size: 18),
                  label: const Text('Salir del modo supervisor'),
                ),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Confirma y sale del modo supervisor: revoca la clave del socio y la de
/// supervisor y borra lo guardado en el teléfono.
Future<void> confirmarSalirSupervisor(BuildContext context) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (c) => AlertDialog(
      title: const Text('Salir del modo supervisor'),
      content: const Text('Se cierra tu sesión de supervisor en este teléfono y se revoca el acceso de solo lectura '
          'al socio. Para volver tendrás que entrar de nuevo.'),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
        FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Salir')),
      ],
    ),
  );
  if (ok == true && context.mounted) await context.read<Sesion>().salirModoSupervisor();
}
