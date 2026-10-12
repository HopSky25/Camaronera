import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../modelos/verificacion.dart';
import '../../tema.dart';
import 'formato.dart';
import 'lista_remota.dart';
import 'widgets.dart';

/// Informes de verificación de mis ventas (`role=seller`) o compras
/// (`role=buyer`), con los que esperan mi decisión primero.
class PantallaInformes extends StatefulWidget {
  /// seller | buyer
  final String rol;
  const PantallaInformes({super.key, required this.rol});
  @override
  State<PantallaInformes> createState() => _PantallaInformesState();
}

class _PantallaInformesState extends State<PantallaInformes> {
  String _filtro = 'aceptar';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Informes de verificación')),
      body: ListaRemota(
        ruta: '/verifications',
        params: {'role': widget.rol},
        vacio: 'No hay informes en esta lista.',
        filtro: (v) => switch (_filtro) {
          'aceptar' => v['acceptance_state'] == 'waiting',
          'curso' => !const {'approved', 'approved_obs', 'rejected', 'cancelled'}.contains(v['state']),
          _ => true,
        },
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            width: double.infinity,
            child: SegmentedButton<String>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: 'aceptar', label: Text('Por aceptar')),
                ButtonSegment(value: 'curso', label: Text('En curso')),
                ButtonSegment(value: 'todas', label: Text('Todas')),
              ],
              selected: {_filtro},
              onSelectionChanged: (s) => setState(() => _filtro = s.first),
            ),
          ),
        ),
        tarjeta: (c, json) {
          final v = Verificacion.desde(json);
          final contraparte = widget.rol == 'seller' ? v.comprador.nombre : v.vendedor.nombre;
          return TarjetaRegistro(
            titulo: v.referencia,
            subtitulo: v.producto.nombre,
            estado: v.estado,
            estadoTexto: v.estadoEtiqueta,
            onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInformePartes(id: v.id))),
            datos: [
              Dato(icono: widget.rol == 'seller' ? Icons.factory_outlined : Icons.water_outlined, texto: contraparte),
              Dato(icono: Icons.verified_outlined, texto: v.verificadora.nombre.isEmpty ? 'Verificación declarada' : v.verificadora.nombre),
              if (v.esperaAceptacion)
                Dato(
                  icono: Icons.how_to_vote_outlined,
                  texto: v.plazoAceptacion == null ? 'Esperando tu decisión' : 'Decidir antes del ${fechaHora(v.plazoAceptacion!.toIso8601String())}',
                  color: Marca.ambar,
                ),
            ],
          );
        },
      ),
    );
  }
}

/// Detalle del informe para comprador o vendedor, con ACEPTAR / RECHAZAR
/// (`POST /verifications/{id}/acceptance {"decision","reason"}`).
class PantallaInformePartes extends StatelessWidget {
  final String id;
  const PantallaInformePartes({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Informe de verificación')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/verifications/$id'),
        hijo: (c, r, _, _, recargar) => _Detalle(v: Verificacion.desde(r.datos.first), recargar: recargar),
      ),
    );
  }
}

class _Detalle extends StatelessWidget {
  final Verificacion v;
  final Future<void> Function() recargar;
  const _Detalle({required this.v, required this.recargar});

  @override
  Widget build(BuildContext context) {
    final j = v.crudo;
    final pesos = (j['weights'] ?? {}) as Map;
    final clasif = (j['classification'] ?? {}) as Map;
    final meta = (j['metabisulfite'] ?? {}) as Map;
    final sabor = (j['taste'] ?? {}) as Map;
    final gramos = (j['grams'] ?? {}) as Map;
    final larva = j['larvae'] is Map ? j['larvae'] as Map : null;
    final lineas = (clasif['lines'] as List?) ?? [];
    final posturas = (j['acceptances'] as List?) ?? [];
    final hayInforme = pesos['plant_lb'] is num && (pesos['plant_lb'] as num) > 0 || lineas.isNotEmpty || larva != null;

    return RefreshIndicator(
      color: Marca.brand,
      onRefresh: recargar,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(children: [
            Expanded(child: Text(v.referencia, style: Theme.of(context).textTheme.titleLarge)),
            Insignia(estado: v.estado, texto: v.estadoEtiqueta),
          ]),
          const SizedBox(height: 4),
          Text(v.producto.nombre, style: Theme.of(context).textTheme.titleMedium),
          const Rotulo('Partes'),
          SeccionDatos(filas: [
            ('Vendedor', v.vendedor.nombre),
            ('Comprador', v.comprador.nombre),
            ('Verificó', (j['verification_label'] as String?) ?? v.verificadora.nombre),
            if (v.loteCodigo != null) ('Código de lote', v.loteCodigo!),
            if (j['verified_at'] != null) ('Veredicto', fechaHora(j['verified_at'])),
            if (j['verdict_notes'] != null) ('Notas del veredicto', '${j['verdict_notes']}'),
          ]),
          const Rotulo('Resultado'),
          if (!hayInforme)
            const Tarjeta(child: Text('Todavía no hay datos de campo.', style: TextStyle(color: Marca.tintaSuave)))
          else if (larva != null)
            SeccionDatos(filas: [
              ('Cantidad verificada', numero(larva['qty_verified'])),
              ('Supervivencia', porcentaje(larva['survival_rate'])),
              ('Tamaño medio', '${numero(larva['avg_size_mg'])} mg'),
              ('Sanidad', '${larva['health_status'] ?? '—'}'),
              if (larva['health_notes'] != null) ('Notas', '${larva['health_notes']}'),
            ])
          else ...[
            SeccionDatos(filas: [
              ('Libras enviadas', libras(pesos['sent_lb'])),
              ('Libras en planta', libras(pesos['plant_lb'])),
              ('Basura', libras(pesos['trash_lb'])),
              ('Peso neto', libras(pesos['net_lb'])),
              ('Rendimiento', porcentaje(clasif['yield_pct'])),
              ('Clase A', '${libras(clasif['class_a_lb'])} (${porcentaje(clasif['yield_class_a_pct'])})'),
              ('Clase B', '${libras(clasif['class_b_lb'])} (${porcentaje(clasif['yield_class_b_pct'])})'),
              ('Clase C', '${libras(clasif['class_c_lb'])} (${porcentaje(clasif['yield_class_c_pct'])})'),
              ('Presentación', presentacion(j['presentation'] as String?)),
              ('Metabisulfito', meta['ppm'] == null ? '—' : '${numero(meta['ppm'])} ppm · ${meta['result'] ?? ''}'),
              ('Sabor', '${sabor['result'] ?? '—'}'),
              ('Gramaje finca / planta', '${numero(gramos['farm'])} / ${numero(gramos['plant_1'])} / ${numero(gramos['plant_2'])}'),
            ]),
            if (lineas.isNotEmpty) ...[
              const SizedBox(height: 10),
              Tarjeta(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('Clasificación por talla', style: TextStyle(fontWeight: FontWeight.w700, color: Marca.navy)),
                  const SizedBox(height: 6),
                  for (final l in lineas.whereType<Map>())
                    Text('Clase ${(l['quality_class'] as String? ?? '').toUpperCase()} · talla ${l['size_code']} · ${libras(l['weight_lb'])} (${porcentaje(l['percent_of_total'])})',
                        style: const TextStyle(fontSize: 14)),
                ]),
              ),
            ],
          ],
          if (j['incident_notes'] != null) ...[
            const Rotulo('Incidencias'),
            Tarjeta(child: Text('${j['incident_notes']}')),
          ],
          const Rotulo('Aceptación de las partes'),
          SeccionDatos(filas: [
            ('Estado', v.aceptacionEtiqueta),
            if (v.plazoAceptacion != null) ('Plazo', fechaHora(v.plazoAceptacion!.toIso8601String())),
            for (final a in posturas.whereType<Map>())
              (
                a['role'] == 'buyer' ? 'Comprador' : 'Vendedor',
                '${decisionEtiqueta(a['decision'] as String?)}${a['counter_price'] != null ? ' · ${dinero(a['counter_price'])}' : ''}${a['reason'] != null ? ' · ${a['reason']}' : ''}',
              ),
          ]),
          if (v.puedoDecidir) ...[
            const SizedBox(height: 16),
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: Marca.verde),
              onPressed: () => _decidir(context, 'accept'),
              icon: const Icon(Icons.check_circle_outline),
              label: const Text('Aceptar el informe'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              style: OutlinedButton.styleFrom(foregroundColor: Marca.rojo, side: const BorderSide(color: Marca.rojo, width: 1.5)),
              onPressed: () => _decidir(context, 'reject'),
              icon: const Icon(Icons.cancel_outlined),
              label: const Text('Rechazar el informe'),
            ),
            const SizedBox(height: 8),
            const AvisoWeb(texto: 'La contraoferta de precio se hace desde el portal web.'),
          ] else if (v.esperaAceptacion) ...[
            const SizedBox(height: 10),
            const Tarjeta(child: Text('Ya diste tu decisión. Falta la otra parte.', style: TextStyle(color: Marca.tintaSuave))),
          ],
        ],
      ),
    );
  }

  Future<void> _decidir(BuildContext context, String decision) async {
    final motivo = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(decision == 'accept' ? 'Aceptar el informe' : 'Rechazar el informe'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(decision == 'accept'
              ? 'Aceptas el resultado de la verificación. Cuando la otra parte también acepte, la compra sigue.'
              : 'Rechazar el informe puede tumbar la venta. Explica el motivo.'),
          const SizedBox(height: 12),
          TextField(
            controller: motivo,
            maxLines: 3,
            decoration: InputDecoration(labelText: decision == 'accept' ? 'Comentario (opcional)' : 'Motivo (obligatorio)'),
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(
            style: decision == 'reject' ? FilledButton.styleFrom(backgroundColor: Marca.rojo) : null,
            onPressed: () {
              if (decision == 'reject' && motivo.text.trim().isEmpty) return;
              Navigator.pop(c, true);
            },
            child: Text(decision == 'accept' ? 'Aceptar' : 'Rechazar'),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.aceptacionInforme, v.id, {
      'decision': decision,
      if (motivo.text.trim().isNotEmpty) 'reason': motivo.text.trim(),
    });
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: decision == 'accept' ? 'Informe aceptado' : 'Informe rechazado');
    if (r.enviado) await recargar();
  }
}
