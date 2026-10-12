import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../seguridad/dispositivo.dart';
import '../../seguridad/firmador.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/menu_cuenta.dart' show abrirEnlace;
import '../comunes/widgets.dart';
import 'comunes_seguridad.dart';

/// "Seguridad" (menú de la cuenta): el teléfono registrado, su fecha de
/// registro y la opción de registrarlo de nuevo.
class PantallaSeguridad extends StatefulWidget {
  const PantallaSeguridad({super.key});
  @override
  State<PantallaSeguridad> createState() => _PantallaSeguridadState();
}

class _PantallaSeguridadState extends State<PantallaSeguridad> {
  bool _ocupado = false;
  String? _error;

  /// Lo que dice el servidor de este dispositivo (último uso).
  Map<String, dynamic>? _servidor;

  @override
  void initState() {
    super.initState();
    _cargarServidor();
  }

  Future<void> _cargarServidor() async {
    final d = await context.read<Sesion>().dispositivoEnServidor();
    if (mounted) setState(() => _servidor = d);
  }

  static String _estado(Dispositivo? d) => switch (d?.estado) {
        EstadoRegistro.registrado => 'Registrado',
        EstadoRegistro.pendiente => 'Se registrará cuando haya internet',
        null => 'Sin registrar',
      };

  Future<void> _registrarDeNuevo() async {
    final sinc = context.read<Sincronizador>();
    final firmadas = sinc.cola.where((o) => o.firma != null).length;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Registrar de nuevo este teléfono'),
        content: Text('Se crea una clave nueva en el teléfono y el registro anterior deja de valer. '
            'Lo confirmas con tu huella (la clave actual firma el cambio). Necesita internet.'
            '${firmadas > 0 ? '\n\nTienes $firmadas envíos pendientes ya confirmados: tendrás que confirmarlos otra vez en Pendientes.' : ''}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Registrar de nuevo')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await context.read<Sesion>().registrarDeNuevo();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Teléfono registrado de nuevo')));
      }
      await sinc.recargarLocal();
      await _cargarServidor();
    } on SinConexion {
      if (mounted) setState(() => _error = 'Sin internet. Registrar de nuevo el teléfono necesita señal; no se cambió nada.');
    } on ErrorSeguridad catch (e) {
      if (mounted && !e.cancelado) {
        setState(() => _error = e.codigo == 'sin_claves'
            ? 'La clave de este teléfono ya no existe, así que no puede firmar el cambio. Cierra sesión y vuelve a entrar con tu correo y contraseña.'
            : e.mensaje);
      }
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final d = sesion.dispositivo;
    return Scaffold(
      appBar: AppBar(title: const Text('Seguridad')),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
        const Rotulo('Este teléfono'),
        SeccionDatos(filas: [
          ('Nombre', d?.nombre ?? '—'),
          ('Estado', _estado(d)),
          ('Registrado el', d?.creadoEn == null ? '—' : fechaHoraDe(d!.creadoEn!)),
          ('Último uso', _servidor == null ? '—' : fechaHora(_servidor!['last_used_at'])),
          ('Desbloqueo', 'Huella o PIN del teléfono'),
        ]),
        const SizedBox(height: 16),
        BotonAccion(
          onPressed: _ocupado ? null : _registrarDeNuevo,
          icono: Icons.phonelink_lock_outlined,
          texto: 'Registrar de nuevo este teléfono',
          textoOcupado: 'Registrando…',
          relleno: false,
        ),
        if (_error != null) ...[const SizedBox(height: 12), CajaError(texto: _error!)],
        const Rotulo('Cómo te protege'),
        const Tarjeta(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            PuntoSeguridad(icono: Icons.verified_user_outlined, texto: textoHuellaNoSale),
            PuntoSeguridad(
                icono: Icons.draw_outlined,
                texto: 'Cada operación importante lleva la firma de este teléfono: el servidor sabe que la hiciste tú, '
                    'desde aquí, y que nadie la cambió por el camino.'),
            PuntoSeguridad(
                icono: Icons.key_outlined,
                texto: 'La sesión se guarda cifrada: sin tu huella o tu PIN nadie puede usarla, aunque tenga el teléfono.'),
            PuntoSeguridad(
                icono: Icons.phonelink_erase_outlined,
                texto: 'Si pierdes el teléfono, entra al portal (Mi cuenta → Dispositivos) y desconéctalo.'),
          ]),
        ),
        const SizedBox(height: 12),
        TextButton.icon(
          onPressed: () => abrirEnlace(context, '${sesion.url}/my'),
          icon: const Icon(Icons.open_in_new, size: 18),
          label: const Text('Abrir Mi cuenta en el portal'),
        ),
      ]),
    );
  }
}
