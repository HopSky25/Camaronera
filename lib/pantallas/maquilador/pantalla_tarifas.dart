import 'package:flutter/material.dart';

import '../../datos/repositorio.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Tarifas de empaque (`GET /copack/tariffs`), solo lectura.
class PantallaTarifas extends StatelessWidget {
  const PantallaTarifas({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Tarifas')),
      body: ListaRemota(
        ruta: '/copack/tariffs',
        vacio: 'No hay tarifas.',
        cabecera: const Padding(
          padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: AvisoWeb(texto: 'Las tarifas se crean, publican y archivan desde el portal web; aquí solo se consultan.'),
        ),
        tarjeta: (c, t) => TarjetaRegistro(
          titulo: (t['name'] ?? '') as String,
          subtitulo: t['my_role'] == 'copacker' ? null : 'De ${nombreDe(t['copacker'])}',
          estado: (t['state'] ?? '') as String,
          estadoTexto: '${estadoTarifa(t['state'] as String?)}${t['is_current'] == true ? ' · vigente' : ''}',
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaTarifa(id: t['id'] as String))),
          datos: [
            Dato(icono: Icons.date_range_outlined, texto: 'Desde ${fecha(t['valid_from'])}${t['open_ended'] == true ? ' (sin fecha fin)' : ' hasta ${fecha(t['valid_to'])}'}'),
            Dato(icono: Icons.format_list_numbered, texto: '${(t['lines'] as List?)?.length ?? 0} renglones${t['min_lot_lb'] != null ? ' · lote mínimo ${libras(t['min_lot_lb'])}' : ''}'),
          ],
        ),
      ),
    );
  }
}

class PantallaTarifa extends StatelessWidget {
  final String id;
  const PantallaTarifa({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Tarifa')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/copack/tariffs/$id'),
        hijo: (c, r, _, _, recargar) {
          final t = r.datos.first;
          final lineas = ((t['lines'] as List?) ?? []).whereType<Map>().toList();
          return RefreshIndicator(
            color: Marca.brand,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Row(children: [
                  Expanded(child: Text((t['name'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge)),
                  Insignia(estado: (t['state'] ?? '') as String, texto: estadoTarifa(t['state'] as String?)),
                ]),
                const Rotulo('Condiciones'),
                SeccionDatos(filas: [
                  ('Maquilador', nombreDe(t['copacker'])),
                  ('Vigencia', '${fecha(t['valid_from'])} → ${t['open_ended'] == true ? 'sin fecha fin' : fecha(t['valid_to'])}'),
                  ('Vigente hoy', t['is_current'] == true ? 'Sí' : 'No'),
                  if (t['min_lot_lb'] != null) ('Lote mínimo', libras(t['min_lot_lb'])),
                  if (t['payment_notes'] != null) ('Pago', '${t['payment_notes']}'),
                  if (t['conditions'] != null) ('Condiciones', '${t['conditions']}'),
                ]),
                const Rotulo('Renglones'),
                SeccionDatos(filas: [
                  for (final x in lineas)
                    ('${presentacion(x['presentation'] as String?)} · ${x['pack_format']}${x['from_lb'] is num && (x['from_lb'] as num) > 0 ? ' · desde ${libras(x['from_lb'])}' : ''}', '${dinero(x['rate_per_lb'])}/lb'),
                ]),
                if (t['recipients'] is List) ...[
                  const Rotulo('Dirigida a'),
                  Tarjeta(child: Text((t['recipients'] as List).isEmpty ? 'Sin destinatarios' : (t['recipients'] as List).map(nombreDe).join(', '))),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}
