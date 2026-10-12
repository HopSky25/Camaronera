import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Órdenes de empaque (`GET /copack/orders`).
class PantallaOrdenes extends StatefulWidget {
  final String filtroInicial;
  const PantallaOrdenes({super.key, this.filtroInicial = 'curso'});
  @override
  State<PantallaOrdenes> createState() => _PantallaOrdenesState();
}

class _PantallaOrdenesState extends State<PantallaOrdenes> {
  late String _filtro = widget.filtroInicial;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Órdenes de empaque')),
      body: ListaRemota(
        ruta: '/copack/orders',
        vacio: 'No hay órdenes en esta lista.',
        filtro: (o) => switch (_filtro) {
          'curso' => o['state'] != 'closed' && o['state'] != 'cancelled',
          'actas' => o['acceptance_state'] == 'open' || o['acceptance_state'] == 'disputed',
          'cerradas' => o['state'] == 'closed' || o['state'] == 'cancelled',
          _ => true,
        },
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            width: double.infinity,
            child: SegmentedButton<String>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: 'curso', label: Text('En curso')),
                ButtonSegment(value: 'actas', label: Text('Actas')),
                ButtonSegment(value: 'cerradas', label: Text('Cerradas')),
              ],
              selected: {_filtro},
              onSelectionChanged: (s) => setState(() => _filtro = s.first),
            ),
          ),
        ),
        tarjeta: (c, o) => TarjetaRegistro(
          titulo: (o['reference'] ?? '') as String,
          subtitulo: '${libras(o['agreed_qty_lb'])} · ${dinero(o['rate_per_lb'])}/lb${o['self_packing'] == true ? ' · empaque propio' : ''}',
          estado: (o['state'] ?? '') as String,
          estadoTexto: estadoOrdenMaquila(o['state'] as String?),
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaOrden(id: o['id'] as String))),
          datos: [
            Dato(icono: Icons.business_outlined, texto: o['my_role'] == 'copacker' ? nombreDe(o['client']) : nombreDe(o['copacker'])),
            if (o['product'] is Map) Dato(icono: Icons.inventory_outlined, texto: nombreDe(o['product'])),
            if (o['acceptance_state'] != 'na') Dato(icono: Icons.draw_outlined, texto: 'Acta: ${estadoActa(o['acceptance_state'] as String?)}', color: o['acceptance_state'] == 'open' ? Marca.ambar : null),
          ],
        ),
      ),
    );
  }
}

/// Detalle de la orden con las acciones del maquilador: recepción
/// (`:reception`), empaque (`:packing`) y firma del acta (`:sign`).
class PantallaOrden extends StatelessWidget {
  final String id;
  const PantallaOrden({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Orden de empaque')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/copack/orders/$id'),
        hijo: (c, r, _, _, recargar) => _Detalle(o: r.datos.first, recargar: recargar),
      ),
    );
  }
}

class _Detalle extends StatelessWidget {
  final Map<String, dynamic> o;
  final Future<void> Function() recargar;
  const _Detalle({required this.o, required this.recargar});

  @override
  Widget build(BuildContext context) {
    final soyMaquilador = o['my_role'] == 'copacker';
    final estado = o['state'] as String?;
    final firmas = ((o['signatures'] as List?) ?? []).whereType<Map>().toList();
    final miFirma = firmas.where((f) => f['role'] == (soyMaquilador ? 'copacker' : 'client')).firstOrNull;
    final puedoFirmar = (o['acceptance_state'] == 'open' || o['acceptance_state'] == 'disputed') && miFirma != null && miFirma['decision'] == 'pending';

    return RefreshIndicator(
      color: Marca.brand,
      onRefresh: recargar,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(children: [
            Expanded(child: Text((o['reference'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge)),
            Insignia(estado: estado ?? '', texto: estadoOrdenMaquila(estado)),
          ]),
          const Rotulo('Orden'),
          SeccionDatos(filas: [
            ('Cliente', nombreDe(o['client'])),
            ('Maquilador', nombreDe(o['copacker'])),
            if (o['product'] is Map) ('Lote', nombreDe(o['product'])),
            ('Cantidad acordada', libras(o['agreed_qty_lb'])),
            ('Tarifa', '${dinero(o['rate_per_lb'])}/lb'),
            ('Tolerancia', porcentaje(o['tolerance_pct'])),
            if (o['supplies_notes'] != null) ('Insumos', '${o['supplies_notes']}'),
          ]),
          const Rotulo('Recepción y empaque'),
          SeccionDatos(filas: [
            ('Recibido', o['received_lb'] is num && (o['received_lb'] as num) > 0 ? '${libras(o['received_lb'])} · ${fechaHora(o['received_at'])}' : 'Pendiente'),
            if (o['supplies_received'] == true) ('Insumos recibidos', 'Sí'),
            if (o['supplies_issue'] != null) ('Problema con insumos', '${o['supplies_issue']}'),
            ('Empacado', o['packed_lb'] is num && (o['packed_lb'] as num) > 0 ? '${libras(o['packed_lb'])} · ${entero(o['boxes'])} cajas · ${fechaHora(o['packed_at'])}' : 'Pendiente'),
            if (o['packed_presentation'] != null) ('Presentación', presentacion(o['packed_presentation'] as String?)),
            if (o['difference_lb'] is num) ('Diferencia', '${libras(o['difference_lb'])} (${porcentaje(o['difference_pct'])})'),
            ('Cuadra', o['balanced'] == true ? 'Sí' : 'No'),
            ('Importe del servicio', dinero(o['service_amount'])),
          ]),
          const Rotulo('Acta de cuadre'),
          SeccionDatos(filas: [
            ('Estado', estadoActa(o['acceptance_state'] as String?)),
            for (final f in firmas)
              (f['role'] == 'copacker' ? 'Maquilador' : 'Cliente', '${_decision(f['decision'] as String?)}${f['reason'] != null ? ' · ${f['reason']}' : ''}'),
          ]),
          const SizedBox(height: 16),
          if (soyMaquilador && estado == 'confirmed') ...[
            BotonAccion(onPressed: () => _recepcion(context), icono: Icons.move_to_inbox_outlined, texto: 'Registrar recepción en planta'),
            const SizedBox(height: 10),
          ],
          if (soyMaquilador && estado == 'received') ...[
            BotonAccion(onPressed: () => _empaque(context), icono: Icons.inventory_2_outlined, texto: 'Registrar empaque terminado'),
            const SizedBox(height: 10),
          ],
          if (puedoFirmar) ...[
            BotonAccion(
              estilo: FilledButton.styleFrom(backgroundColor: Marca.verde),
              onPressed: () => _firmar(context, 'accepted'),
              icono: Icons.draw_outlined,
              texto: 'Firmar el acta (conforme)',
            ),
            const SizedBox(height: 10),
            BotonAccion(
              relleno: false,
              estilo: OutlinedButton.styleFrom(foregroundColor: Marca.rojo, side: const BorderSide(color: Marca.rojo, width: 1.5)),
              onPressed: () => _firmar(context, 'rejected'),
              icono: Icons.cancel_outlined,
              texto: 'No conforme',
            ),
            const SizedBox(height: 10),
          ],
          if (estado == 'closed' || estado == 'cancelled')
            const Tarjeta(child: Text('Esta orden está cerrada. Puedes consultarla, pero ya no se edita.', style: TextStyle(color: Marca.tintaSuave)))
          else
            const AvisoWeb(texto: 'Deshacer una firma, reabrir el acta en disputa o cancelar la orden se hace desde el portal web.'),
        ],
      ),
    );
  }

  static String _decision(String? d) => switch (d) {
        'pending' => 'Pendiente',
        'accepted' => 'Conforme',
        'rejected' => 'No conforme',
        _ => d ?? '—',
      };

  Future<void> _recepcion(BuildContext context) async {
    final lb = TextEditingController(text: '${o['agreed_qty_lb'] ?? ''}');
    final problema = TextEditingController();
    var insumos = true;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setLocal) => AlertDialog(
          title: const Text('Recepción en planta'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(controller: lb, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Libras recibidas')),
            SwitchListTile(contentPadding: EdgeInsets.zero, value: insumos, onChanged: (v) => setLocal(() => insumos = v), title: const Text('Insumos recibidos')),
            if (!insumos) TextField(controller: problema, maxLines: 2, decoration: const InputDecoration(labelText: 'Qué faltó')),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
            FilledButton(
              onPressed: () {
                final v = numeroEscrito(lb.text);
                if (v == null || v <= 0) return;
                Navigator.pop(c, true);
              },
              child: const Text('Registrar'),
            ),
          ],
        ),
      ),
    );
    final libras = numeroEscrito(lb.text) ?? 0;
    final cuerpo = cuerpoRecepcionMaquila(librasRecibidas: libras, insumos: insumos, problema: problema.text);
    lb.dispose();
    problema.dispose();
    if (ok != true || libras <= 0 || !context.mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.recepcionMaquila, '${o['id']}', cuerpo);
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: 'Recepción registrada');
    if (r.enviado) await recargar();
  }

  Future<void> _empaque(BuildContext context) async {
    final lb = TextEditingController(text: '${o['received_lb'] ?? ''}');
    final cajas = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Empaque terminado'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: lb, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Libras empacadas')),
          const SizedBox(height: 10),
          TextField(controller: cajas, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'Cajas')),
          const SizedBox(height: 8),
          const Text('Al registrar el empaque se abre el acta de cuadre a la firma de las dos partes.', style: TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(
            onPressed: () {
              final v = numeroEscrito(lb.text);
              if (v == null || v <= 0) return;
              Navigator.pop(c, true);
            },
            child: const Text('Registrar'),
          ),
        ],
      ),
    );
    final libras = numeroEscrito(lb.text) ?? 0;
    final cuerpo = cuerpoEmpaqueMaquila(librasEmpacadas: libras, cajas: int.tryParse(cajas.text.trim()));
    lb.dispose();
    cajas.dispose();
    if (ok != true || libras <= 0 || !context.mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.empaqueMaquila, '${o['id']}', cuerpo);
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: 'Empaque registrado: el acta queda a la firma');
    if (r.enviado) await recargar();
  }

  Future<void> _firmar(BuildContext context, String decision) async {
    final motivo = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(decision == 'accepted' ? 'Firmar el acta' : 'No conforme'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(decision == 'accepted'
              ? 'Firmas conforme con el cuadre: ${libras(o['received_lb'])} recibidas, ${libras(o['packed_lb'])} empacadas.'
              : 'Explica por qué no estás conforme con el cuadre.'),
          const SizedBox(height: 12),
          TextField(controller: motivo, maxLines: 3, decoration: InputDecoration(labelText: decision == 'accepted' ? 'Comentario (opcional)' : 'Motivo (obligatorio)')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(
            onPressed: () {
              if (decision == 'rejected' && motivo.text.trim().isEmpty) return;
              Navigator.pop(c, true);
            },
            child: Text(decision == 'accepted' ? 'Firmar' : 'Enviar'),
          ),
        ],
      ),
    );
    final cuerpo = cuerpoFirmaActa(decision, motivo.text);
    motivo.dispose();
    if (ok != true || !context.mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.firmaActa, '${o['id']}', cuerpo);
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: decision == 'accepted' ? 'Acta firmada' : 'Disconformidad registrada');
    if (r.enviado) await recargar();
  }
}
