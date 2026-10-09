import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import 'pantalla_fotos.dart';
import 'pantalla_informe.dart';
import 'pantalla_veredicto.dart';

/// Detalle de una verificación y las acciones del técnico, en el orden del
/// trabajo real: iniciar campo → llegada del camión → informe → veredicto.
class PantallaVerificacion extends StatelessWidget {
  final String id;
  const PantallaVerificacion({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final v = sinc.bandeja.where((e) => e.id == id).firstOrNull;
    if (v == null) {
      return Scaffold(appBar: AppBar(), body: const Center(child: Text('Esta verificación ya no está en tu bandeja.')));
    }
    final fmt = DateFormat('dd/MM/yyyy HH:mm');
    final pesos = (v.crudo['weights'] ?? {}) as Map;
    final clasif = (v.crudo['classification'] ?? {}) as Map;

    return Scaffold(
      appBar: AppBar(title: Text(v.referencia)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _Seccion(titulo: 'Lote', filas: [
            ('Producto', v.producto.nombre),
            ('Camaronera', v.vendedor.nombre),
            ('Compra de', v.comprador.nombre),
            if (v.instalacion != null) ('Finca', v.instalacion!),
            if (v.piscina != null) ('Piscina', v.piscina!),
            if (v.loteCodigo != null) ('Código de lote', v.loteCodigo!),
            ('Estado', v.estadoEtiqueta),
          ]),
          if (v.despachoId != null)
            _Seccion(titulo: 'Camión', filas: [
              ('Llegada prevista', v.despachoEta == null ? 'sin programar' : fmt.format(v.despachoEta!)),
            ]),
          if (v.tieneDetalle)
            _Seccion(titulo: 'Informe (lo que tiene el servidor)', filas: [
              ('Libras enviadas', _n(pesos['sent_lb'])),
              ('Libras en planta', _n(pesos['plant_lb'])),
              ('Basura', _n(pesos['trash_lb'])),
              ('Rendimiento', clasif['yield_pct'] == null ? '—' : '${_n(clasif['yield_pct'])} %'),
              ('Clase A', clasif['yield_class_a_pct'] == null ? '—' : '${_n(clasif['yield_class_a_pct'])} %'),
              ('Líneas de clasificación', '${(clasif['lines'] as List?)?.length ?? 0}'),
              ('Conteos', '${(v.crudo['counts'] as List?)?.length ?? 0}'),
              ('Fotos en el servidor', '${v.crudo['photo_count'] ?? 0}'),
            ]),
          const SizedBox(height: 16),
          if (v.puedeIniciarCampo)
            FilledButton.icon(
              onPressed: () async {
                await sinc.iniciarCampo(v);
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Trabajo de campo iniciado')));
                }
              },
              icon: const Icon(Icons.play_arrow),
              label: const Text('Iniciar trabajo de campo'),
            ),
          if (v.despachoId != null && !v.cerrada) ...[
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => _registrarLlegada(context, sinc, v),
              icon: const Icon(Icons.local_shipping_outlined),
              label: const Text('Registrar llegada del camión'),
            ),
          ],
          if (!v.cerrada) ...[
            const SizedBox(height: 10),
            FilledButton.tonalIcon(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInforme(id: v.id))),
              icon: const Icon(Icons.edit_note),
              label: const Text('Informe de campo'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaFotos(id: v.id))),
              icon: const Icon(Icons.photo_camera_outlined),
              label: const Text('Fotos'),
            ),
          ],
          if (v.enCampo || v.estado == 'done') ...[
            const SizedBox(height: 10),
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: Colors.green.shade700),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVeredicto(id: v.id))),
              icon: const Icon(Icons.gavel),
              label: const Text('Dar veredicto'),
            ),
          ],
        ],
      ),
    );
  }

  static String _n(dynamic v) => v == null ? '—' : NumberFormat('#,##0.##').format(v);

  Future<void> _registrarLlegada(BuildContext context, Sincronizador sinc, Verificacion v) async {
    final ahora = DateTime.now();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Llegada del camión'),
        content: Text('Se registrará la llegada ahora: ${DateFormat('dd/MM/yyyy HH:mm').format(ahora)}.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Registrar')),
        ],
      ),
    );
    if (ok == true) {
      await sinc.registrarLlegadaCamion(v, ahora);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Llegada registrada')));
      }
    }
  }
}

class _Seccion extends StatelessWidget {
  final String titulo;
  final List<(String, String)> filas;
  const _Seccion({required this.titulo, required this.filas});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(titulo, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 6),
            for (final f in filas)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(width: 150, child: Text(f.$1, style: Theme.of(context).textTheme.bodySmall)),
                    Expanded(child: Text(f.$2)),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
