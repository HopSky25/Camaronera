import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../../datos/sincronizador.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Solicitudes de empaque abiertas (`GET /copack/requests?state=published`)
/// con "Ofertar" (`POST /copack/requests/{id}/offers`; repetir la edita).
class PantallaSolicitudes extends StatefulWidget {
  const PantallaSolicitudes({super.key});
  @override
  State<PantallaSolicitudes> createState() => _PantallaSolicitudesState();
}

class _PantallaSolicitudesState extends State<PantallaSolicitudes> {
  final _lista = GlobalKey<ListaRemotaState>();
  String _filtro = 'published';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Solicitudes de empaque')),
      body: ListaRemota(
        key: _lista,
        ruta: '/copack/requests',
        params: {if (_filtro != 'todas') 'state': _filtro},
        vacio: 'No hay solicitudes en esta lista.',
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            width: double.infinity,
            child: SegmentedButton<String>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: 'published', label: Text('Abiertas')),
                ButtonSegment(value: 'assigned', label: Text('Adjudicadas')),
                ButtonSegment(value: 'todas', label: Text('Todas')),
              ],
              selected: {_filtro},
              onSelectionChanged: (s) => setState(() => _filtro = s.first),
            ),
          ),
        ),
        tarjeta: (c, s) {
          final miOferta = ((s['offers'] as List?) ?? []).whereType<Map>().firstOrNull;
          final abierta = s['state'] == 'published';
          return Tarjeta(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(child: Text((s['reference'] ?? '') as String, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: Marca.navy))),
                Insignia(estado: (s['state'] ?? '') as String, texto: estadoSolicitudMaquila(s['state'] as String?)),
              ]),
              const SizedBox(height: 6),
              Text('${libras(s['quantity_lb'])} · ${presentacion(s['presentation'] as String?)}${s['size_grade'] is Map ? ' · talla ${nombreDe(s['size_grade'])}' : ''}',
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Dato(icono: Icons.business_outlined, texto: nombreDe(s['client'])),
              Dato(icono: Icons.date_range_outlined, texto: 'Entre ${fecha(s['needed_from'])} y ${fecha(s['needed_to'])}'),
              if (s['copacker'] is Map) const Dato(icono: Icons.lock_outline, texto: 'Dirigida a tu planta', color: Marca.brand),
              if (s['supplies_notes'] != null) Dato(icono: Icons.inventory_outlined, texto: 'Insumos: ${s['supplies_notes']}'),
              if (s['notes'] != null) Dato(icono: Icons.notes_outlined, texto: '${s['notes']}'),
              if (miOferta != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Tu oferta: ${dinero(miOferta['rate_per_lb'])}/lb · ${libras(miOferta['capacity_lb'])} · ${estadoOferta(miOferta['state'] as String?)}',
                    style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: Marca.brand),
                  ),
                ),
              if (abierta) ...[
                const SizedBox(height: 10),
                BotonAccion(
                  estilo: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                  onPressed: () => _ofertar(context, s, miOferta),
                  icono: Icons.local_offer_outlined,
                  texto: miOferta == null || miOferta['state'] == 'withdrawn' ? 'Ofertar' : 'Cambiar mi oferta',
                ),
              ],
            ]),
          );
        },
      ),
    );
  }

  Future<void> _ofertar(BuildContext context, Map<String, dynamic> s, Map? previa) async {
    final tarifa = TextEditingController(text: previa?['rate_per_lb'] is Map ? '${previa!['rate_per_lb']['amount']}' : '');
    final capacidad = TextEditingController(text: previa?['capacity_lb'] != null ? '${previa!['capacity_lb']}' : '${s['quantity_lb'] ?? ''}');
    final notas = TextEditingController(text: previa?['notes'] as String? ?? '');
    // El selector de fecha exige que la inicial esté dentro del rango: las
    // fechas de la solicitud pueden haber quedado atrás.
    final primero = DateTime.now().subtract(const Duration(days: 1));
    final ultimo = DateTime.now().add(const Duration(days: 365));
    var desde = fechaEntre(aFecha(previa?['available_from']) ?? aFecha(s['needed_from']) ?? DateTime.now(), primero, ultimo);
    var hasta = fechaEntre(
        aFecha(previa?['available_to']) ?? aFecha(s['needed_to']) ?? DateTime.now().add(const Duration(days: 7)), desde, ultimo);
    final fmt = DateFormat('dd/MM/yyyy');

    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setLocal) => AlertDialog(
          title: const Text('Mi oferta'),
          content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(controller: tarifa, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Tarifa por libra (USD)')),
              const SizedBox(height: 10),
              TextField(controller: capacidad, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Capacidad (lb)')),
              const SizedBox(height: 10),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text('Disponible desde: ${fmt.format(desde)}'),
                trailing: const Icon(Icons.edit_outlined, size: 18),
                onTap: () async {
                  final f = await showDatePicker(context: c, initialDate: desde, firstDate: primero, lastDate: ultimo);
                  if (f != null) {
                    setLocal(() {
                      desde = f;
                      if (hasta.isBefore(desde)) hasta = desde;
                    });
                  }
                },
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text('Disponible hasta: ${fmt.format(hasta)}'),
                trailing: const Icon(Icons.edit_outlined, size: 18),
                onTap: () async {
                  final f = await showDatePicker(context: c, initialDate: fechaEntre(hasta, desde, ultimo), firstDate: desde, lastDate: ultimo);
                  if (f != null) setLocal(() => hasta = f);
                },
              ),
              TextField(controller: notas, maxLines: 2, decoration: const InputDecoration(labelText: 'Notas (opcional)')),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
            FilledButton(
              onPressed: () {
                final t = numeroEscrito(tarifa.text);
                final cap = numeroEscrito(capacidad.text);
                if (t == null || t < 0 || cap == null || cap <= 0) return;
                Navigator.pop(c, true);
              },
              child: const Text('Enviar oferta'),
            ),
          ],
        ),
      ),
    );
    final t = numeroEscrito(tarifa.text);
    final cap = numeroEscrito(capacidad.text);
    final cuerpo = cuerpoOfertaMaquila(tarifaPorLb: t ?? 0, capacidadLb: cap ?? 0, desde: desde, hasta: hasta, notas: notas.text);
    tarifa.dispose();
    capacidad.dispose();
    notas.dispose();
    if (ok != true || t == null || cap == null || !context.mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.ofertaMaquila, '${s['id']}', cuerpo);
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: 'Oferta enviada');
    if (r.enviado) await _lista.currentState?.recargar();
  }
}
