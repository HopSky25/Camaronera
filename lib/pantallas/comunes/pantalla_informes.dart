import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../modelos/verificacion.dart';
import '../../tema.dart';
import 'formato.dart';
import 'lista_remota.dart';
import 'pantalla_segunda_opinion.dart';
import 'widgets.dart';

/// Informes de verificación de mis ventas (`role=seller`) o compras
/// (`role=buyer`), con los que esperan mi decisión primero (aceptar el
/// informe o, el vendedor, pedir una segunda opinión tras un rechazo).
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
          'aceptar' => v['acceptance_state'] == 'waiting' || (widget.rol == 'seller' && v['contest_state'] == 'open'),
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
                ButtonSegment(value: 'aceptar', label: Text('Por decidir')),
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
                  texto: v.plazoAceptacion == null ? 'Esperando tu decisión' : 'Decidir antes del ${fechaHoraDe(v.plazoAceptacion!)}',
                  color: context.colores.advertenciaTexto,
                ),
              if (v.esSegundaOpinion) const Dato(icono: Icons.balance_rounded, texto: 'Segunda opinión'),
              if (v.rechazoNoPresentado) const Dato(icono: Icons.visibility_off_outlined, texto: 'Rechazada: producto no presentado'),
              if (v.estado == 'rejected' && v.estadoImpugnacion == 'open')
                Dato(
                  icono: Icons.hourglass_bottom_rounded,
                  texto: widget.rol == 'seller'
                      ? 'Puedes pedir segunda opinión${v.plazoImpugnacion == null ? '' : ' hasta el ${fechaHoraDe(v.plazoImpugnacion!)}'}'
                      : 'El vendedor puede pedir segunda opinión',
                  color: context.colores.advertenciaTexto,
                ),
              if (v.declinada && !v.cerrada)
                Dato(icono: Icons.block_rounded, texto: 'La verificadora declinó la orden', color: context.colores.peligroTexto),
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
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);

    return RefreshIndicator(
      color: context.colores.marcaTexto,
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
          if (v.esSegundaOpinion) ...[
            const SizedBox(height: 10),
            const CajaAviso(
              tipo: TipoAviso.info,
              icono: Icons.balance_rounded,
              texto: 'Segunda opinión: el vendedor impugnó el rechazo de otra verificadora y esta vuelve a verificar. '
                  'Su honorario lo paga el vendedor. Si aprueba, la compra sigue; si rechaza, el rechazo se mantiene.',
            ),
          ],
          ..._orden(context, v),
          const Rotulo('Partes'),
          SeccionDatos(filas: [
            ('Vendedor', v.vendedor.nombre),
            ('Comprador', v.comprador.nombre),
            ('Verificó', (j['verification_label'] as String?) ?? v.verificadora.nombre),
            if (v.loteCodigo != null) ('Código de lote', v.loteCodigo!),
            if (j['verified_at'] != null) ('Veredicto', fechaHora(j['verified_at'])),
            if (j['verdict_notes'] != null) ('Notas del veredicto', '${j['verdict_notes']}'),
          ]),
          if (v.estado == 'rejected') ..._rechazo(context, v, soloLectura),
          const Rotulo('Resultado'),
          if (!hayInforme)
            Tarjeta(child: Text('Todavía no hay datos de campo.', style: TextStyle(color: context.colores.tintaSuave)))
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
              ('Metabisulfito', meta['ppm'] == null ? '—' : '${numero(meta['ppm'])} ppm · ${resultadoMetabisulfito(meta['result'] as String?)}'),
              ('Sabor', resultadoSabor(sabor['result'] as String?)),
              ('Gramaje finca / planta', '${numero(gramos['farm'])} / ${numero(gramos['plant_1'])} / ${numero(gramos['plant_2'])}'),
            ]),
            if (lineas.isNotEmpty) ...[
              const SizedBox(height: 10),
              Tarjeta(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('Clasificación por talla', style: TextStyle(fontWeight: FontWeight.w700, color: context.colores.titulo)),
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
            if (v.plazoAceptacion != null) ('Plazo', fechaHoraDe(v.plazoAceptacion!)),
            for (final a in posturas.whereType<Map>())
              (
                a['role'] == 'buyer' ? 'Comprador' : 'Vendedor',
                '${decisionEtiqueta(a['decision'] as String?)}${a['counter_price'] != null ? ' · ${dinero(a['counter_price'])}' : ''}${a['reason'] != null ? ' · ${a['reason']}' : ''}',
              ),
          ]),
          if (v.puedoDecidir && soloLectura) ...[
            const SizedBox(height: 16),
            const AvisoSoloLectura(),
          ] else if (v.puedoDecidir) ...[
            const SizedBox(height: 16),
            BotonAccion(
              estilo: FilledButton.styleFrom(backgroundColor: context.colores.exito),
              onPressed: () => _decidir(context, 'accept'),
              icono: Icons.check_circle_outline,
              texto: 'Aceptar el informe',
            ),
            const SizedBox(height: 10),
            BotonAccion(
              relleno: false,
              estilo: EstilosBoton.peligro(context),
              onPressed: () => _decidir(context, 'reject'),
              icono: Icons.cancel_outlined,
              texto: 'Rechazar el informe',
            ),
            const SizedBox(height: 8),
            const AvisoWeb(texto: 'La contraoferta de precio se hace desde el portal web.'),
          ] else if (v.esperaAceptacion) ...[
            const SizedBox(height: 10),
            Tarjeta(child: Text('Ya diste tu decisión. Falta la otra parte.', style: TextStyle(color: context.colores.tintaSuave))),
          ],
        ],
      ),
    );
  }

  /// La verificadora declinó la orden o no cumplió su plazo: quien eligió la
  /// verificadora (el comprador; en una segunda opinión, el vendedor) elige
  /// otra o cancela. Eso se hace en el portal.
  List<Widget> _orden(BuildContext context, Verificacion v) {
    if (v.cerrada || v.modo != 'platform') return const [];
    final vencido = v.plazoVencidoEtiqueta;
    final filas = <(String, String)>[
      if (v.plazoVeredicto != null) ('Veredicto hasta', fechaHoraDe(v.plazoVeredicto!)),
    ];
    if (vencido == null && filas.isEmpty) return const [];
    final eligeYo = v.miRol == (v.esSegundaOpinion ? 'seller' : 'buyer');
    final motivo = v.declinada && v.motivoDeclinada != null ? ' (motivo: ${v.motivoDeclinada})' : '';
    return [
      const Rotulo('Verificadora'),
      if (vencido != null) ...[
        CajaAviso(
          tipo: TipoAviso.advertencia,
          icono: Icons.schedule_rounded,
          texto: eligeYo
              ? '$vencido$motivo. Puedes elegir otra verificadora o cancelar la compra sin costo desde el portal web.'
              : '$vencido$motivo. ${v.esSegundaOpinion ? 'El vendedor' : 'El comprador'} elegirá otra verificadora o cancelará sin costo.',
        ),
      ] else
        SeccionDatos(filas: filas),
    ];
  }

  /// Rechazo: por «producto no presentado» (firme, sin segunda opinión) o
  /// por el informe (el vendedor puede pedir una segunda opinión en plazo).
  List<Widget> _rechazo(BuildContext context, Verificacion v, bool soloLectura) {
    final vendedor = v.miRol == 'seller';
    if (v.rechazoNoPresentado) {
      final llegada = v.llegadaNoPresentado;
      final cuando = llegada == null ? '' : ' (el técnico llegó a la planta el ${fechaHoraDe(llegada)})';
      return [
        const Rotulo('Rechazo'),
        CajaAviso(
          tipo: TipoAviso.peligro,
          icono: Icons.visibility_off_outlined,
          texto: vendedor
              ? 'Rechazada porque el producto no se presentó en la planta$cuando. El honorario lo pagas tú y suma un strike; '
                  'la compra se canceló. Este rechazo no admite segunda opinión.'
              : 'Rechazada porque el producto no se presentó en la planta$cuando. La compra se canceló sin costo para ti.',
        ),
      ];
    }
    final plazo = v.plazoImpugnacion;
    final hasta = plazo == null ? '' : ' hasta el ${fechaHoraDe(plazo)}';
    final widgets = <Widget>[const Rotulo('Segunda opinión')];
    switch (v.estadoImpugnacion) {
      case 'open':
        widgets.add(CajaAviso(
          tipo: TipoAviso.advertencia,
          icono: Icons.balance_rounded,
          texto: vendedor
              ? 'La verificadora rechazó el producto. Si no estás de acuerdo, pide una segunda opinión$hasta: otra verificadora acreditada '
                  'vuelve a verificar y su honorario lo pagas tú. Si aprueba, la compra sigue; si rechaza, el rechazo se mantiene. '
                  'Si no la pides, la compra se cancela y el honorario de la verificación lo pagas tú.'
              : 'La verificadora rechazó el producto. El vendedor puede pedir una segunda opinión$hasta; mientras tanto la compra queda '
                  'en espera. Si no la pide, la compra se cancela sin costo para ti.',
        ));
        if (vendedor && v.puedoPedirSegundaOpinion) {
          widgets.add(const SizedBox(height: 12));
          widgets.add(soloLectura
              ? const AvisoSoloLectura()
              : BotonAccion(
                  onPressed: () async {
                    final pedida = await Navigator.push<bool>(
                        context, MaterialPageRoute(builder: (_) => PantallaSegundaOpinion(id: v.id)));
                    if (pedida == true) await recargar();
                  },
                  icono: Icons.balance_rounded,
                  texto: 'Pedir segunda opinión',
                ));
        }
      case 'contested':
        widgets.add(CajaAviso(
          tipo: TipoAviso.info,
          icono: Icons.search_rounded,
          texto: '${vendedor ? 'Pediste' : 'El vendedor pidió'} una segunda opinión: otra verificadora vuelve a verificar. '
              'Si aprueba, la compra sigue y las partes aceptan el nuevo informe; si rechaza, el rechazo se mantiene.',
        ));
        final segunda = v.segundaOpinion;
        if (segunda != null) {
          widgets.add(const SizedBox(height: 10));
          widgets.add(OutlinedButton.icon(
            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInformePartes(id: segunda))),
            icon: const Icon(Icons.open_in_new_rounded),
            label: const Text('Ver la segunda opinión'),
          ));
        }
      case 'firm':
        widgets.add(CajaAviso(
          tipo: TipoAviso.peligro,
          icono: Icons.cancel_outlined,
          texto: 'Nadie pidió la segunda opinión a tiempo: el rechazo quedó firme, la compra se canceló y el honorario lo paga el vendedor.',
        ));
      case 'upheld' || 'overturned':
        widgets.add(CajaAviso(
          tipo: v.estadoImpugnacion == 'upheld' ? TipoAviso.peligro : TipoAviso.ok,
          icono: Icons.balance_rounded,
          texto: v.estadoImpugnacion == 'upheld'
              ? 'La segunda opinión confirmó el rechazo: la compra se canceló y los honorarios los paga el vendedor.'
              : 'La segunda opinión aprobó el producto: la compra siguió con el nuevo informe.',
        ));
      default:
        if (v.esSegundaOpinion) {
          widgets.add(const CajaAviso(
            tipo: TipoAviso.peligro,
            icono: Icons.cancel_outlined,
            texto: 'La segunda opinión también rechazó el producto: el rechazo se mantiene y la compra se cancela.',
          ));
        } else {
          return const [];
        }
    }
    return widgets;
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
            style: decision == 'reject' ? FilledButton.styleFrom(backgroundColor: context.colores.peligro) : null,
            onPressed: () {
              if (decision == 'reject' && motivo.text.trim().isEmpty) return;
              Navigator.pop(c, true);
            },
            child: Text(decision == 'accept' ? 'Aceptar' : 'Rechazar'),
          ),
        ],
      ),
    );
    final cuerpo = cuerpoAceptacion(decision, motivo.text);
    motivo.dispose();
    if (ok != true || !context.mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.aceptacionInforme, v.id, cuerpo);
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: decision == 'accept' ? 'Informe aceptado' : 'Informe rechazado');
    if (r.enviado) await recargar();
  }
}
