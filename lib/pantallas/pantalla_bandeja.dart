import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
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

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mis verificaciones'),
        actions: [
          IconButton(
            tooltip: 'Pendientes de enviar',
            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaPendientes())),
            icon: Badge(
              isLabelVisible: sinc.pendientes > 0,
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
            itemBuilder: (_) => const [PopupMenuItem(value: 'salir', child: Text('Cerrar sesión'))],
          ),
        ],
      ),
      body: Column(
        children: [
          _BarraEstado(sinc: sinc),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'abiertas', label: Text('Abiertas')),
                ButtonSegment(value: 'campo', label: Text('En campo')),
                ButtonSegment(value: 'cerradas', label: Text('Cerradas')),
              ],
              selected: {_filtro},
              onSelectionChanged: (s) => setState(() => _filtro = s.first),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: sinc.sincronizar,
              child: lista.isEmpty
                  ? ListView(children: const [
                      SizedBox(height: 80),
                      Center(child: Text('No hay verificaciones en esta lista.\nDesliza hacia abajo para sincronizar.',
                          textAlign: TextAlign.center)),
                    ])
                  : ListView.builder(
                      itemCount: lista.length,
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
    final esquema = Theme.of(context).colorScheme;
    final texto = !sinc.hayRed
        ? 'Sin internet. Lo que captures queda guardado en el teléfono.'
        : sinc.ultimoMensaje ??
            (sinc.ultimaSincronizacion == null
                ? 'Todavía no se ha sincronizado'
                : 'Última sincronización: ${DateFormat('dd/MM HH:mm').format(sinc.ultimaSincronizacion!)}');
    return Container(
      width: double.infinity,
      color: !sinc.hayRed ? esquema.errorContainer : esquema.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(children: [
        Icon(!sinc.hayRed ? Icons.cloud_off : Icons.cloud_done_outlined, size: 18),
        const SizedBox(width: 8),
        Expanded(child: Text(texto, style: Theme.of(context).textTheme.bodySmall)),
      ]),
    );
  }
}

class _TarjetaVerificacion extends StatelessWidget {
  final Verificacion v;
  const _TarjetaVerificacion({required this.v});

  Color _colorEstado(ColorScheme c) => switch (v.estado) {
        'in_field' => Colors.orange,
        'approved' || 'approved_obs' => Colors.green,
        'rejected' || 'cancelled' => c.error,
        _ => c.primary,
      };

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: ListTile(
        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVerificacion(id: v.id))),
        leading: CircleAvatar(
          backgroundColor: _colorEstado(esquema).withValues(alpha: 0.15),
          child: Icon(Icons.assignment_outlined, color: _colorEstado(esquema)),
        ),
        title: Text('${v.referencia}  ·  ${v.vendedor.nombre}', maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(v.producto.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
            Text(
              'Compra: ${v.comprador.nombre}'
              '${v.despachoEta != null ? '  ·  Camión: ${DateFormat('dd/MM HH:mm').format(v.despachoEta!)}' : ''}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
        trailing: Chip(
          label: Text(v.estadoEtiqueta, style: const TextStyle(fontSize: 11)),
          visualDensity: VisualDensity.compact,
          backgroundColor: _colorEstado(esquema).withValues(alpha: 0.12),
          side: BorderSide.none,
        ),
        isThreeLine: true,
      ),
    );
  }
}
