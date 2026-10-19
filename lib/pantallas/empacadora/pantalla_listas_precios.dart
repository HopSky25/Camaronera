import 'package:flutter/material.dart';

import '../../datos/repositorio.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Listas de precios que emito (`GET /price-lists?role=issuer`), solo lectura.
class PantallaListasPrecios extends StatelessWidget {
  const PantallaListasPrecios({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Listas de precios')),
      body: ListaRemota(
        ruta: '/price-lists',
        params: const {'role': 'issuer'},
        vacio: 'No has publicado listas de precios.',
        cabecera: const Padding(
          padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: AvisoWeb(texto: 'Las listas se crean y publican desde el portal web; aquí solo se consultan.'),
        ),
        tarjeta: (c, l) => TarjetaRegistro(
          titulo: (l['name'] ?? '') as String,
          subtitulo: l['aguaje'] is Map ? 'Aguaje ${nombreDe(l['aguaje'])}' : null,
          estado: (l['state'] ?? '') as String,
          estadoTexto: '${estadoListaPrecios(l['state'] as String?)}${l['is_current'] == true ? ' · vigente' : ''}',
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaListaPrecios(id: l['id'] as String))),
          datos: [
            Dato(icono: Icons.date_range_outlined, texto: 'Despacho desde ${fecha(l['dispatch_from'])}${l['open_ended'] == true ? ' (sin fecha fin)' : ' hasta ${fecha(l['dispatch_to'])}'}'),
            Dato(icono: Icons.format_list_numbered, texto: '${(l['lines'] as List?)?.length ?? 0} renglones · ${(l['recipients'] as List?)?.length ?? 0} destinatarios'),
          ],
        ),
      ),
    );
  }
}

class PantallaListaPrecios extends StatelessWidget {
  final String id;
  const PantallaListaPrecios({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Lista de precios')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/price-lists/$id'),
        hijo: (c, r, _, _, recargar) {
          final l = r.datos.first;
          final lineas = ((l['lines'] as List?) ?? []).whereType<Map>().toList();
          final bonos = ((l['bonuses'] as List?) ?? []).whereType<Map>().toList();
          final pago = l['payment'] is Map ? l['payment'] as Map : {};
          return RefreshIndicator(
            color: context.colores.marcaTexto,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Row(children: [
                  Expanded(child: Text((l['name'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge)),
                  Insignia(estado: (l['state'] ?? '') as String, texto: estadoListaPrecios(l['state'] as String?)),
                ]),
                const Rotulo('Vigencia'),
                SeccionDatos(filas: [
                  ('Emitida', fecha(l['issue_date'])),
                  ('Despacho', '${fecha(l['dispatch_from'])} → ${l['open_ended'] == true ? 'sin fecha fin' : fecha(l['dispatch_to'])}'),
                  if (l['aguaje'] is Map) ('Aguaje', nombreDe(l['aguaje'])),
                  ('Vigente hoy', l['is_current'] == true ? 'Sí' : 'No'),
                  ('Anticipo', '${numero(pago['advance_pct'])} % a ${entero(pago['advance_days'])} días · saldo a ${entero(pago['balance_days'])} días'),
                  if (pago['notes'] != null) ('Pago', '${pago['notes']}'),
                  if (l['quality_conditions'] != null) ('Condiciones', '${l['quality_conditions']}'),
                ]),
                const Rotulo('Precios'),
                SeccionDatos(filas: [
                  for (final x in lineas)
                    (
                      '${nombreDe(x['size_grade'])} ${presentacion(x['presentation'] as String?)}${x['channel'] != null ? ' · ${x['channel']}' : ''}',
                      '${dinero(x['price'])} / ${x['uom']} · ${(x['quality'] as String? ?? '').toUpperCase()}',
                    ),
                ]),
                if (bonos.isNotEmpty) ...[
                  const Rotulo('Bonificaciones'),
                  SeccionDatos(filas: [for (final b in bonos) ('${b['name']}', '${dinero(b['amount'])}${b['note'] != null ? ' · ${b['note']}' : ''}')]),
                ],
                if (l['recipients'] is List) ...[
                  const Rotulo('Destinatarios'),
                  Tarjeta(child: Text((l['recipients'] as List).map(nombreDe).join(', '))),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}
