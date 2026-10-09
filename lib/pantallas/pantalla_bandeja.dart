import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
import 'pantalla_pendientes.dart';
import 'pantalla_verificacion.dart';

/// Bandeja del técnico: sus verificaciones, con filtro por estado.
class PantallaBandeja extends StatefulWidget {
  const PantallaBandeja({super.key});
  @override
  State<PantallaBandeja> createState() => _PantallaBandejaState();
}

class _PantallaBandejaState extends State<PantallaBandeja> {
  String _filtro = 'abiertas';

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final lista = sinc.bandeja.where((v) {
      return switch (_filtro) {
        'abiertas' => !v.cerrada,
        'campo' => v.enCampo,
        'cerradas' => v.cerrada,
        _ => true,
      };
    }).toList();
    final abiertas = sinc.bandeja.where((v) => !v.cerrada).length;
    final enCampo = sinc.bandeja.where((v) => v.enCampo).length;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: Image.asset('assets/logo.png', height: 30),
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
          PopupMenuButton<String>(
            onSelected: (v) async {
              if (v == 'salir') {
                final ok = await showDialog<bool>(
                  context: context,
                  builder: (c) => AlertDialog(
                    title: const Text('Cerrar sesión'),
                    content: Text(sinc.pendientes > 0
                        ? 'Tienes ${sinc.pendientes} cambios sin enviar. Si cierras la sesión se pierden.'
                        : 'Se borrará la clave y los datos guardados en el teléfono.'),
                    actions: [
                      TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
                      FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cerrar sesión')),
                    ],
                  ),
                );
                if (ok == true) sinc.cerrarSesion();
              }
            },
            itemBuilder: (_) => [
              PopupMenuItem(
                enabled: false,
                child: Text(sinc.quienSoy?['name'] as String? ?? 'Cuenta conectada',
                    style: const TextStyle(fontWeight: FontWeight.w700, color: Marca.navy)),
              ),
              const PopupMenuItem(value: 'salir', child: Text('Cerrar sesión')),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          _BarraEstado(sinc: sinc),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
            child: Row(
              children: [
                Expanded(child: Text('Mis verificaciones', style: Theme.of(context).textTheme.titleLarge)),
                Text('$abiertas abiertas · $enCampo en campo', style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: SizedBox(
              width: double.infinity,
              child: SegmentedButton<String>(
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'abiertas', label: Text('Abiertas')),
                  ButtonSegment(value: 'campo', label: Text('En campo')),
                  ButtonSegment(value: 'cerradas', label: Text('Cerradas')),
                ],
                selected: {_filtro},
                onSelectionChanged: (s) => setState(() => _filtro = s.first),
              ),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              color: Marca.brand,
              onRefresh: sinc.sincronizar,
              child: lista.isEmpty
                  ? ListView(children: [
                      const SizedBox(height: 90),
                      Icon(Icons.inbox_outlined, size: 56, color: Marca.gris.withValues(alpha: 0.6)),
                      const SizedBox(height: 12),
                      const Center(
                        child: Text('No hay verificaciones en esta lista.\nDesliza hacia abajo para sincronizar.',
                            textAlign: TextAlign.center, style: TextStyle(color: Marca.tintaSuave)),
                      ),
                    ])
                  : ListView.separated(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                      itemCount: lista.length,
                      separatorBuilder: (_, _) => const SizedBox(height: 10),
                      itemBuilder: (_, i) => _TarjetaVerificacion(v: lista[i]),
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _BarraEstado extends StatelessWidget {
  final Sincronizador sinc;
  const _BarraEstado({required this.sinc});

  @override
  Widget build(BuildContext context) {
    final sinRed = !sinc.hayRed;
    final texto = sinRed
        ? 'Sin internet. Lo que captures queda guardado en el teléfono.'
        : sinc.ultimoMensaje ??
            (sinc.ultimaSincronizacion == null
                ? 'Todavía no se ha sincronizado'
                : 'Sincronizado el ${DateFormat('dd/MM').format(sinc.ultimaSincronizacion!)} a las ${DateFormat('HH:mm').format(sinc.ultimaSincronizacion!)}');
    return Container(
      width: double.infinity,
      color: sinRed ? const Color(0xFFFDF1E0) : Marca.tealClaro,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
      child: Row(children: [
        Icon(sinRed ? Icons.cloud_off_outlined : Icons.cloud_done_outlined, size: 18, color: sinRed ? Marca.ambar : Marca.brand),
        const SizedBox(width: 8),
        Expanded(
          child: Text(texto, style: TextStyle(fontSize: 13, color: sinRed ? Marca.ambar : Marca.brand, fontWeight: FontWeight.w600)),
        ),
      ]),
    );
  }
}

class _TarjetaVerificacion extends StatelessWidget {
  final Verificacion v;
  const _TarjetaVerificacion({required this.v});

  @override
  Widget build(BuildContext context) {
    final (_, colorEstado) = Marca.insignia(v.estado);
    return Tarjeta(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVerificacion(id: v.id))),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(v.referencia,
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: Marca.navy)),
              ),
              Insignia(estado: v.estado, texto: v.estadoEtiqueta),
            ],
          ),
          const SizedBox(height: 6),
          Text(v.producto.nombre, maxLines: 2, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Marca.tinta)),
          const SizedBox(height: 10),
          _Dato(icono: Icons.water_outlined, texto: v.vendedor.nombre),
          _Dato(icono: Icons.factory_outlined, texto: v.comprador.nombre),
          if (v.despachoEta != null)
            _Dato(
              icono: Icons.local_shipping_outlined,
              texto: 'Camión: ${DateFormat('dd/MM').format(v.despachoEta!)} a las ${DateFormat('HH:mm').format(v.despachoEta!)}',
              color: colorEstado,
            ),
        ],
      ),
    );
  }
}

class _Dato extends StatelessWidget {
  final IconData icono;
  final String texto;
  final Color? color;
  const _Dato({required this.icono, required this.texto, this.color});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(children: [
          Icon(icono, size: 16, color: Marca.gris),
          const SizedBox(width: 8),
          Expanded(
            child: Text(texto, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 13.5, color: color ?? Marca.tintaSuave)),
          ),
        ]),
      );
}
