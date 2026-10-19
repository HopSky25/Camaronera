import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/sesion.dart';
import '../../modelos/perfil.dart';
import '../../tema.dart';
import '../comunes/widgets.dart';

/// "¿Con qué perfil quieres trabajar?": tarjetas grandes, una por perfil de
/// la cuenta. Los pendientes de aprobación se ven pero no se pueden elegir.
class PantallaElegirPerfil extends StatefulWidget {
  const PantallaElegirPerfil({super.key});
  @override
  State<PantallaElegirPerfil> createState() => _PantallaElegirPerfilState();
}

class _PantallaElegirPerfilState extends State<PantallaElegirPerfil> {
  String? _eligiendo;
  String? _error;

  Future<void> _elegir(String rol) async {
    setState(() {
      _eligiendo = rol;
      _error = null;
    });
    try {
      await context.read<Sesion>().elegirPerfil(rol);
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _eligiendo = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final perfiles = sesion.yo?.perfiles ?? [];
    final orden = ['camaronera', 'empacadora', 'semillero', 'laboratorio', 'maquilador', 'verificador'];
    final ordenados = [...perfiles]..sort((a, b) => orden.indexOf(a.rol).compareTo(orden.indexOf(b.rol)));
    // Modo supervisor: perfiles del socio que se está viendo.
    final supervisor = sesion.soloLectura;
    return Scaffold(
      appBar: AppBar(
        title: const LogoMarca(),
        actions: [
          if (supervisor)
            TextButton.icon(
              onPressed: () => sesion.cambiarDeSocio(),
              icon: const Icon(Icons.groups_outlined, size: 18),
              label: const Text('Otro socio'),
            )
          else
            TextButton.icon(
              onPressed: () => sesion.cerrarSesion(),
              icon: const Icon(Icons.logout, size: 18),
              label: const Text('Salir'),
            ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
        children: [
          Text(supervisor ? sesion.nombre : 'Hola, ${sesion.nombre}', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 6),
          Text(supervisor ? '¿Con qué perfil quieres ver su app?' : '¿Con qué perfil quieres trabajar?',
              style: TextStyle(fontSize: 16, color: context.colores.tintaSuave)),
          const SizedBox(height: 18),
          if (_error != null) ...[CajaError(texto: _error!), const SizedBox(height: 12)],
          if (perfiles.isEmpty)
            Tarjeta(
              child: Text(
                  supervisor
                      ? 'Este socio todavía no tiene perfiles.'
                      : 'Tu cuenta todavía no tiene perfiles. Pídelos desde el portal web.',
                  style: TextStyle(color: context.colores.tintaSuave)),
            ),
          for (final p in ordenados) ...[
            _TarjetaPerfil(
              perfil: p,
              ocupado: _eligiendo == p.rol,
              onTap: p.aprobado && _eligiendo == null ? () => _elegir(p.rol) : null,
            ),
            const SizedBox(height: 12),
          ],
          const SizedBox(height: 8),
          Text(
              supervisor
                  ? 'Elegir un perfil aquí no cambia nada en la cuenta del socio: solo decide qué parte de su app ves.'
                  : 'Puedes cambiar de perfil cuando quieras desde el menú de la cuenta.',
              textAlign: TextAlign.center,
              style: TextStyle(color: context.colores.tintaSuave, fontSize: 13)),
        ],
      ),
    );
  }
}

class _TarjetaPerfil extends StatelessWidget {
  final Perfil perfil;
  final bool ocupado;
  final VoidCallback? onTap;
  const _TarjetaPerfil({required this.perfil, required this.ocupado, this.onTap});

  @override
  Widget build(BuildContext context) {
    final activo = perfil.aprobado;
    return Opacity(
      opacity: activo ? 1 : 0.55,
      child: Tarjeta(
        padding: const EdgeInsets.all(18),
        onTap: onTap,
        child: Row(children: [
          Container(
            width: 60,
            height: 60,
            decoration: BoxDecoration(
              color: activo ? context.colores.marcaSuave : context.colores.fondoAlt,
              borderRadius: BorderRadius.circular(16),
            ),
            child: Icon(iconoDePerfil(perfil.rol), size: 32, color: activo ? context.colores.marcaTexto : context.colores.tintaTenue),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(perfil.etiqueta, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: context.colores.titulo)),
              const SizedBox(height: 4),
              Text(activo ? descripcionDePerfil(perfil.rol) : 'Pendiente de aprobación',
                  style: TextStyle(fontSize: 13, color: activo ? context.colores.tintaSuave : context.colores.advertenciaTexto, fontWeight: activo ? FontWeight.w400 : FontWeight.w700)),
              const SizedBox(height: 8),
              Insignia(estado: activo ? 'approved' : 'in_field', texto: activo ? (perfil.activo ? 'Aprobado · activo en el portal' : 'Aprobado') : 'Pendiente'),
            ]),
          ),
          if (ocupado)
            const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))
          else if (activo)
            Icon(Icons.chevron_right, color: context.colores.tintaTenue),
        ]),
      ),
    );
  }
}
