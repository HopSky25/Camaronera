import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../seguridad/firmador.dart';
import '../../tema.dart';
import '../comunes/widgets.dart';
import 'comunes_seguridad.dart';

/// Pantalla de desbloqueo: tapa toda la app (al abrirla y al volver tras más
/// de 2 minutos en segundo plano). Sin desbloquear no se ve ningún dato.
/// Desbloquear = descifrar la clave de API con la huella o el PIN.
class PantallaDesbloqueo extends StatefulWidget {
  const PantallaDesbloqueo({super.key});
  @override
  State<PantallaDesbloqueo> createState() => _PantallaDesbloqueoState();
}

class _PantallaDesbloqueoState extends State<PantallaDesbloqueo> {
  bool _ocupado = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    // Se pide la huella nada más aparecer.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _desbloquear();
    });
  }

  Future<void> _desbloquear({bool conPin = false}) async {
    if (_ocupado) return;
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await context.read<Sesion>().desbloquear(conPin: conPin);
    } on ErrorSeguridad catch (e) {
      // Cancelar no es un error: se queda la pantalla con los botones.
      if (mounted && !e.cancelado) setState(() => _error = e.mensaje);
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    return Scaffold(
      body: Column(children: [
        CabeceraSeguridad(
          icono: Icons.lock_outline,
          titulo: 'CamaronMarket está bloqueada',
          texto: sesion.yo == null ? 'Confirma que eres tú para continuar.' : '${sesion.nombre}: confirma que eres tú para continuar.',
        ),
        Expanded(
          child: ListView(padding: const EdgeInsets.all(24), children: [
            FilledButton.icon(
              onPressed: _ocupado ? null : () => _desbloquear(),
              icon: const Icon(Icons.fingerprint),
              label: const Text('Desbloquear con huella'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: _ocupado ? null : () => _desbloquear(conPin: true),
              icon: const Icon(Icons.pin_outlined),
              label: const Text('Usar PIN del teléfono'),
            ),
            if (_error != null) ...[const SizedBox(height: 14), CajaError(texto: _error!)],
            const SizedBox(height: 24),
            Text(textoHuellaNoSale, textAlign: TextAlign.center, style: TextStyle(color: context.colores.tintaSuave, fontSize: 13)),
            const SizedBox(height: 16),
            TextButton(onPressed: _ocupado ? null : () => _salir(context), child: const Text('Cerrar sesión')),
          ]),
        ),
      ]),
    );
  }

  Future<void> _salir(BuildContext context) async {
    final pendientes = context.read<Sincronizador>().pendientes;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cerrar sesión'),
        content: Text(pendientes > 0
            ? 'Hay $pendientes cambios sin enviar en este teléfono. Si cierras la sesión se pierden.'
            : 'Se borrará la sesión y los datos guardados en el teléfono.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cerrar sesión')),
        ],
      ),
    );
    if (ok == true && context.mounted) await context.read<Sesion>().cerrarSesion();
  }
}
