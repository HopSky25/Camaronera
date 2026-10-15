import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../datos/control_version.dart';
import '../../tema.dart';

/// Abre el enlace de descarga en el navegador / la tienda. Devuelve si se pudo.
Future<bool> abrirDescarga(String? url) async {
  final uri = url == null ? null : Uri.tryParse(url);
  if (uri == null) return false;
  try {
    return await launchUrl(uri, mode: LaunchMode.externalApplication);
  } catch (_) {
    return false;
  }
}

/// Pantalla bloqueante "Actualiza la app" (C1): la versión instalada es
/// menor que la mínima del servidor (o la API respondió 426). Tapa todo,
/// también la pantalla de desbloqueo y la de entrada.
class PantallaActualizar extends StatefulWidget {
  const PantallaActualizar({super.key});
  @override
  State<PantallaActualizar> createState() => _PantallaActualizarState();
}

class _PantallaActualizarState extends State<PantallaActualizar> {
  bool _comprobando = false;

  @override
  Widget build(BuildContext context) {
    final cv = context.watch<ControlVersion>();
    return PopScope(
      canPop: false,
      child: Scaffold(
        backgroundColor: Marca.fondo,
        body: SafeArea(
          child: ListView(padding: const EdgeInsets.fromLTRB(24, 48, 24, 24), children: [
            Center(child: Image.asset('assets/logo.png', height: 44)),
            const SizedBox(height: 36),
            const Icon(Icons.system_update_outlined, size: 64, color: Marca.brand),
            const SizedBox(height: 16),
            Text('Actualiza la app', textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 12),
            Text(
              cv.mensaje ??
                  'Esta versión de CamaronMarket ya no se puede usar. Instala la versión nueva para seguir trabajando; '
                      'lo que tengas guardado en el teléfono se conserva.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Marca.tintaSuave, fontSize: 15),
            ),
            const SizedBox(height: 8),
            Text(
              'Tu versión: ${cv.version}${cv.minVersion != null ? ' · mínima: ${cv.minVersion}' : ''}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Marca.gris, fontSize: 13),
            ),
            const SizedBox(height: 28),
            if (cv.urlDescarga != null)
              FilledButton.icon(
                onPressed: () async {
                  final ok = await abrirDescarga(cv.urlDescarga);
                  if (!ok && context.mounted) {
                    ScaffoldMessenger.maybeOf(context)?.showSnackBar(
                        const SnackBar(content: Text('No se pudo abrir el enlace de descarga.')));
                  }
                },
                icon: const Icon(Icons.download_outlined),
                label: const Text('Descargar la actualización'),
              )
            else
              const Text('Pide la versión nueva a soporte de CamaronMarket.',
                  textAlign: TextAlign.center, style: TextStyle(color: Marca.tintaSuave)),
            const SizedBox(height: 10),
            TextButton(
              onPressed: _comprobando
                  ? null
                  : () async {
                      setState(() => _comprobando = true);
                      await cv.comprobar(forzar: true);
                      if (mounted) setState(() => _comprobando = false);
                    },
              child: Text(_comprobando ? 'Comprobando…' : 'Ya la actualicé: comprobar de nuevo'),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Aviso discreto (descartable) de que hay una versión más nueva que no es
/// obligatoria.
class AvisoVersionNueva extends StatelessWidget {
  const AvisoVersionNueva({super.key});

  @override
  Widget build(BuildContext context) {
    final cv = context.watch<ControlVersion>();
    if (!cv.hayNueva) return const SizedBox.shrink();
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
        child: Material(
          elevation: 3,
          color: Marca.navy,
          borderRadius: BorderRadius.circular(14),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 6, 4, 6),
            child: Row(children: [
              const Icon(Icons.system_update_outlined, color: Colors.white, size: 20),
              const SizedBox(width: 10),
              Expanded(
                child: Text('Hay una versión nueva (${cv.versionNueva}).',
                    style: const TextStyle(color: Colors.white, fontSize: 13.5, fontWeight: FontWeight.w600)),
              ),
              if (cv.urlDescarga != null)
                TextButton(
                  style: TextButton.styleFrom(foregroundColor: const Color(0xFF8FE3EC)),
                  onPressed: () => abrirDescarga(cv.urlDescarga),
                  child: const Text('Actualizar'),
                ),
              IconButton(
                tooltip: 'Ahora no',
                onPressed: cv.descartar,
                icon: const Icon(Icons.close, color: Colors.white70, size: 20),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
