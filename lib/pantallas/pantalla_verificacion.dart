import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
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
    final hayInforme = _hay(pesos['sent_lb']) || _hay(pesos['plant_lb']) || ((clasif['lines'] as List?)?.isNotEmpty ?? false);

    return Scaffold(
      appBar: AppBar(title: Text(v.referencia)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(children: [
            Expanded(child: Text(v.producto.nombre, style: Theme.of(context).textTheme.titleMedium)),
            const SizedBox(width: 10),
            Insignia(estado: v.estado, texto: v.estadoEtiqueta),
          ]),
          const Rotulo('Lote'),
          _Seccion(filas: [
            ('Camaronera', v.vendedor.nombre),
            ('Compra de', v.comprador.nombre),
            if (v.instalacion != null) ('Finca', v.instalacion!),
            if (v.piscina != null) ('Piscina', v.piscina!),
            if (v.loteCodigo != null) ('Código de lote', v.loteCodigo!),
          ]),
          if (v.despachoId != null) ...[
            const Rotulo('Camión'),
            _Seccion(filas: [
              ('Llegada prevista', v.despachoEta == null ? 'Sin programar' : fmt.format(v.despachoEta!)),
            ]),
          ],
          const Rotulo('Informe en el servidor'),
          if (!hayInforme)
            const Tarjeta(
              child: Text('Todavía no se ha cargado ningún dato de campo.', style: TextStyle(color: Marca.tintaSuave)),
            )
          else
            _Seccion(filas: [
              ('Libras enviadas', _lb(pesos['sent_lb'])),
              ('Libras en planta', _lb(pesos['plant_lb'])),
              ('Basura', _lb(pesos['trash_lb'])),
              ('Rendimiento', _pct(clasif['yield_pct'])),
              ('Clase A', _pct(clasif['yield_class_a_pct'])),
              ('Clasificación', '${(clasif['lines'] as List?)?.length ?? 0} líneas'),
              ('Conteos', '${(v.crudo['counts'] as List?)?.length ?? 0}'),
              ('Fotos', '${v.crudo['photo_count'] ?? 0}'),
            ]),
          const Rotulo('Acciones'),
          if (v.puedeIniciarCampo) ...[
            FilledButton.icon(
              onPressed: () async {
                await sinc.iniciarCampo(v);
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Trabajo de campo iniciado')));
                }
              },
              icon: const Icon(Icons.play_arrow_rounded),
              label: const Text('Iniciar trabajo de campo'),
            ),
            const SizedBox(height: 10),
          ],
          if (v.despachoId != null && !v.cerrada) ...[
            OutlinedButton.icon(
              onPressed: () => _registrarLlegada(context, sinc, v),
              icon: const Icon(Icons.local_shipping_outlined),
              label: const Text('Registrar llegada del camión'),
            ),
            const SizedBox(height: 10),
          ],
          if (!v.cerrada) ...[
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: v.enCampo ? Marca.brand : Marca.navy),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInforme(id: v.id))),
              icon: const Icon(Icons.edit_note_rounded),
              label: const Text('Informe de campo'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaFotos(id: v.id))),
              icon: const Icon(Icons.photo_camera_outlined),
              label: const Text('Fotos de campo'),
            ),
            const SizedBox(height: 10),
          ],
          if (v.enCampo || v.estado == 'done')
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: Marca.verde),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVeredicto(id: v.id))),
              icon: const Icon(Icons.gavel_rounded),
              label: const Text('Dar veredicto'),
            ),
          if (v.cerrada)
            const Tarjeta(
              child: Text('Esta verificación está cerrada. Puedes consultarla, pero ya no se edita.',
                  style: TextStyle(color: Marca.tintaSuave)),
            ),
        ],
      ),
    );
  }

  static bool _hay(dynamic v) => v != null && v is num && v > 0;
  static String _lb(dynamic v) => !_hay(v) ? '—' : '${NumberFormat('#,##0.##').format(v)} lb';
  static String _pct(dynamic v) => !_hay(v) ? '—' : '${NumberFormat('#,##0.#').format(v)} %';

  Future<void> _registrarLlegada(BuildContext context, Sincronizador sinc, Verificacion v) async {
    final ahora = DateTime.now();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Llegada del camión'),
        content: Text('Se registrará la llegada ahora, ${DateFormat('dd/MM/yyyy').format(ahora)} a las ${DateFormat('HH:mm').format(ahora)}.'),
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
  final List<(String, String)> filas;
  const _Seccion({required this.filas});

  @override
  Widget build(BuildContext context) {
    return Tarjeta(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Column(
        children: [
          for (var i = 0; i < filas.length; i++) ...[
            if (i > 0) const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 10),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(width: 130, child: Text(filas[i].$1, style: const TextStyle(fontSize: 13, color: Marca.tintaSuave))),
                  Expanded(
                    child: Text(filas[i].$2, textAlign: TextAlign.right,
                        style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Marca.tinta)),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}
