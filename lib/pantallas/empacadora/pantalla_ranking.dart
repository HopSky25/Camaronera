import 'package:flutter/material.dart';

import '../../datos/repositorio.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Ranking completo de proveedores (`GET /suppliers/ranking?order=`).
class PantallaRanking extends StatefulWidget {
  const PantallaRanking({super.key});
  @override
  State<PantallaRanking> createState() => _PantallaRankingState();
}

class _PantallaRankingState extends State<PantallaRanking> {
  String _orden = 'puntaje';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Ranking de proveedores'),
        actions: [
          PopupMenuButton<String>(
            icon: const Icon(Icons.sort),
            tooltip: 'Ordenar por',
            onSelected: (v) => setState(() => _orden = v),
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'puntaje', child: Text('Puntaje')),
              PopupMenuItem(value: 'rendimiento', child: Text('Rendimiento')),
              PopupMenuItem(value: 'clase_a', child: Text('Clase A')),
              PopupMenuItem(value: 'cumplimiento', child: Text('Cumplimiento')),
              PopupMenuItem(value: 'lotes', child: Text('Lotes')),
              PopupMenuItem(value: 'metabisulfito', child: Text('Metabisulfito')),
            ],
          ),
        ],
      ),
      body: CargaRemota<ResultadoLista>(
        key: ValueKey(_orden),
        cargar: (repo) => repo.detalle('/suppliers/ranking', params: {'order': _orden}),
        hijo: (c, r, _, _, recargar) {
          final datos = r.datos.first;
          final filas = ((datos['rows'] as List?) ?? []).whereType<Map>().toList();
          return RefreshIndicator(
            color: context.colores.marcaTexto,
            onRefresh: recargar,
            child: filas.isEmpty
                ? const VacioLista(texto: 'Todavía no hay lotes verificados de tus proveedores.', icono: Icons.emoji_events_outlined)
                : ListView.separated(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                    itemCount: filas.length + 1,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (_, i) {
                      if (i == 0) {
                        return Text(
                          '${datos['suppliers'] ?? filas.length} proveedores · ${datos['lots'] ?? 0} lotes · muestra mínima ${datos['threshold'] ?? '—'} lotes',
                          style: TextStyle(fontSize: 13, color: context.colores.tintaSuave),
                        );
                      }
                      final f = filas[i - 1];
                      final suficiente = f['suficiente'] == true;
                      return Tarjeta(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Row(children: [
                            CircleAvatar(radius: 15, backgroundColor: context.colores.marcaSuave, child: Text('$i', style: TextStyle(fontWeight: FontWeight.w800, color: context.colores.marcaTexto, fontSize: 13))),
                            const SizedBox(width: 12),
                            Expanded(child: Text(nombreDe(f['supplier']), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: context.colores.titulo))),
                            Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                              Text(numero(f['puntaje']), style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: context.colores.marcaTexto)),
                              Text('puntaje', style: TextStyle(fontSize: 11, color: context.colores.tintaSuave)),
                            ]),
                          ]),
                          const SizedBox(height: 10),
                          Wrap(spacing: 14, runSpacing: 4, children: [
                            _Cifra('Rendimiento', porcentaje(f['rendimiento'])),
                            _Cifra('Clase A', porcentaje(f['clase_a'])),
                            _Cifra('Cumplimiento', porcentaje(f['cumplimiento'])),
                            _Cifra('Merma', porcentaje(f['merma'])),
                            _Cifra('Metabisulfito', f['metabisulfito_fail'] == null ? '—' : '${entero(f['metabisulfito_fail'])} fallos'),
                            _Cifra('Lotes', '${entero(f['lotes'])} (${libras(f['lb_verificadas'])})'),
                            _Cifra('Último', f['last_verified'] == null ? '—' : fecha(f['last_verified'])),
                          ]),
                          if (!suficiente) ...[
                            const SizedBox(height: 6),
                            Text('Muestra insuficiente todavía', style: TextStyle(fontSize: 12, color: context.colores.advertenciaTexto, fontWeight: FontWeight.w600)),
                          ],
                        ]),
                      );
                    },
                  ),
          );
        },
      ),
    );
  }
}

class _Cifra extends StatelessWidget {
  final String etiqueta;
  final String valor;
  const _Cifra(this.etiqueta, this.valor);
  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
        Text(etiqueta, style: TextStyle(fontSize: 11, color: context.colores.tintaSuave)),
        Text(valor, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: context.colores.tinta)),
      ]);
}
