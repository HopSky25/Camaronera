import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/control_legal.dart';
import '../../datos/sesion.dart';
import '../../tema.dart';
import 'pantalla_actualizar.dart' show abrirDescarga;

/// Pantalla «Acepta los documentos actualizados»: la plataforma publicó
/// versiones nuevas de la política de privacidad o de los términos y, hasta
/// aceptarlas en el portal, la API no deja operar (403
/// legal-acceptance-required). Tapa la app (no la de desbloqueo).
class PantallaDocumentosLegales extends StatefulWidget {
  const PantallaDocumentosLegales({super.key});
  @override
  State<PantallaDocumentosLegales> createState() => _PantallaDocumentosLegalesState();
}

class _PantallaDocumentosLegalesState extends State<PantallaDocumentosLegales> {
  bool _comprobando = false;

  Future<void> _salir(BuildContext context, Sesion sesion) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cerrar sesión'),
        content: const Text('Se cierra la sesión y se borra lo guardado en este teléfono, también lo que no se haya enviado.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cerrar sesión')),
        ],
      ),
    );
    if (ok == true) await sesion.cerrarSesion();
  }

  Future<void> _comprobar(ControlLegal cl) async {
    setState(() => _comprobando = true);
    await cl.comprobar();
    if (!mounted) return;
    setState(() => _comprobando = false);
    if (cl.pendiente) {
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(const SnackBar(
          content: Text('Todavía faltan documentos por aceptar. Acéptalos en el portal y vuelve a comprobar.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final cl = context.watch<ControlLegal>();
    Sesion? s;
    try {
      s = context.watch<Sesion>();
    } on ProviderNotFoundException {
      s = null;
    }
    final sesion = s;
    return PopScope(
      canPop: false,
      child: Scaffold(
        backgroundColor: Marca.fondo,
        body: SafeArea(
          child: ListView(padding: const EdgeInsets.fromLTRB(24, 48, 24, 24), children: [
            Center(child: Image.asset('assets/logo.png', height: 44)),
            const SizedBox(height: 36),
            const Icon(Icons.gavel_outlined, size: 64, color: Marca.brand),
            const SizedBox(height: 16),
            Text('Acepta los documentos actualizados',
                textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 12),
            const Text(
              'CamaronMarket publicó versiones nuevas de sus documentos legales. Para seguir usando la app '
              'tienes que leerlos y aceptarlos en el portal. Lo que tengas guardado en el teléfono se conserva.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Marca.tintaSuave, fontSize: 15),
            ),
            if (cl.documentos.isNotEmpty) ...[
              const SizedBox(height: 16),
              for (final d in cl.documentos)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 3),
                  child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                    const Icon(Icons.description_outlined, size: 18, color: Marca.navy),
                    const SizedBox(width: 6),
                    Flexible(child: Text(d, style: const TextStyle(fontWeight: FontWeight.w600, color: Marca.navy))),
                  ]),
                ),
            ],
            const SizedBox(height: 28),
            if (cl.urlAceptar != null)
              FilledButton.icon(
                onPressed: () async {
                  final ok = await abrirDescarga(cl.urlAceptar);
                  if (!ok && context.mounted) {
                    ScaffoldMessenger.maybeOf(context)
                        ?.showSnackBar(const SnackBar(content: Text('No se pudo abrir el portal.')));
                  }
                },
                icon: const Icon(Icons.open_in_new),
                label: const Text('Leer y aceptar en el portal'),
              )
            else
              const Text('Entra al portal de CamaronMarket para aceptarlos.',
                  textAlign: TextAlign.center, style: TextStyle(color: Marca.tintaSuave)),
            const SizedBox(height: 10),
            TextButton(
              onPressed: _comprobando ? null : () => _comprobar(cl),
              child: Text(_comprobando ? 'Comprobando…' : 'Ya los acepté: comprobar'),
            ),
            if (sesion != null && sesion.conectado) ...[
              const SizedBox(height: 20),
              TextButton.icon(
                style: TextButton.styleFrom(foregroundColor: Marca.tintaSuave),
                onPressed: () => _salir(context, sesion),
                icon: const Icon(Icons.logout),
                label: const Text('Cerrar sesión'),
              ),
            ],
          ]),
        ),
      ),
    );
  }
}
