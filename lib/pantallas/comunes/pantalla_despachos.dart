import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../tema.dart';
import 'formato.dart';
import 'lista_remota.dart';
import 'widgets.dart';

/// Despachos donde participo (`GET /dispatches`). El vendedor programa la
/// cita del camión; la empacadora solo consulta.
class PantallaDespachos extends StatefulWidget {
  const PantallaDespachos({super.key});
  @override
  State<PantallaDespachos> createState() => _PantallaDespachosState();
}

class _PantallaDespachosState extends State<PantallaDespachos> {
  String _filtro = 'pendientes';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Despachos')),
      body: ListaRemota(
        ruta: '/dispatches',
        vacio: 'No hay despachos en esta lista.',
        filtro: (d) => switch (_filtro) {
          'pendientes' => d['state'] != 'llegado',
          'llegados' => d['state'] == 'llegado',
          _ => true,
        },
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            width: double.infinity,
            child: SegmentedButton<String>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: 'pendientes', label: Text('Por llegar')),
                ButtonSegment(value: 'llegados', label: Text('Llegados')),
                ButtonSegment(value: 'todos', label: Text('Todos')),
              ],
              selected: {_filtro},
              onSelectionChanged: (s) => setState(() => _filtro = s.first),
            ),
          ),
        ),
        tarjeta: (c, d) => TarjetaDespacho(d: d),
      ),
    );
  }
}

class TarjetaDespacho extends StatelessWidget {
  final Map<String, dynamic> d;
  const TarjetaDespacho({super.key, required this.d});

  @override
  Widget build(BuildContext context) {
    final esVendedor = d['my_role'] == 'seller';
    return TarjetaRegistro(
      titulo: nombreDe(d['product']),
      estado: (d['state'] ?? '') as String,
      estadoTexto: estadoDespacho(d['state'] as String?),
      onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaDespacho(id: d['id'] as String))),
      datos: [
        Dato(icono: esVendedor ? Icons.factory_outlined : Icons.water_outlined, texto: esVendedor ? nombreDe(d['buyer']) : nombreDe(d['seller'])),
        Dato(icono: Icons.local_shipping_outlined, texto: d['eta'] == null ? 'Sin cita' : 'Llegada prevista: ${fechaHora(d['eta'])}', color: d['eta'] == null ? Marca.ambar : null),
        if (d['actual_arrival'] != null) Dato(icono: Icons.flag_outlined, texto: 'Llegó: ${fechaHora(d['actual_arrival'])}', color: Marca.verde),
      ],
    );
  }
}

/// Detalle del despacho. El vendedor puede programar o editar la cita:
/// `PATCH /dispatches/{id}` (harvest_date, farm_departure, eta, carrier_name,
/// vehicle_plate, carrier_phone).
class PantallaDespacho extends StatelessWidget {
  final String id;
  const PantallaDespacho({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Despacho')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/dispatches/$id'),
        hijo: (c, r, _, _, recargar) {
          final d = r.datos.first;
          final esVendedor = d['my_role'] == 'seller';
          final llegado = d['state'] == 'llegado';
          final soloLectura = context.read<Sesion>().soloLectura;
          return RefreshIndicator(
            color: Marca.brand,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Row(children: [
                  Expanded(child: Text(nombreDe(d['product']), style: Theme.of(context).textTheme.titleLarge)),
                  Insignia(estado: (d['state'] ?? '') as String, texto: estadoDespacho(d['state'] as String?)),
                ]),
                const Rotulo('Partes'),
                SeccionDatos(filas: [
                  ('Vendedor', nombreDe(d['seller'])),
                  ('Comprador', nombreDe(d['buyer'])),
                  if (d['verifier'] is Map) ('Verificadora', nombreDe(d['verifier'])),
                ]),
                const Rotulo('Cita'),
                SeccionDatos(filas: [
                  ('Fecha de cosecha', fecha(d['harvest_date'])),
                  ('Salida de la finca', fechaHora(d['farm_departure'])),
                  ('Llegada prevista (ETA)', fechaHora(d['eta'])),
                  if (d['eta_changes'] is num && (d['eta_changes'] as num) > 0) ('Cambios de cita', '${d['eta_changes']}'),
                  ('Transportista', '${d['carrier_name'] ?? '—'}'),
                  ('Placa', '${d['vehicle_plate'] ?? '—'}'),
                  ('Teléfono', '${d['carrier_phone'] ?? '—'}'),
                  if (d['notes'] != null) ('Notas', '${d['notes']}'),
                ]),
                if (d['actual_arrival'] != null) ...[
                  const Rotulo('Llegada real'),
                  SeccionDatos(filas: [
                    ('Llegó a planta', fechaHora(d['actual_arrival'])),
                    if (d['delay_minutes'] is num) ('Retraso', '${entero(d['delay_minutes'])} min'),
                    if (d['on_time'] != null) ('A tiempo', d['on_time'] == true ? 'Sí' : 'No'),
                  ]),
                ],
                const SizedBox(height: 16),
                if (esVendedor && !llegado && soloLectura)
                  const AvisoSoloLectura()
                else if (esVendedor && !llegado)
                  FilledButton.icon(
                    onPressed: () async {
                      final cambiado = await Navigator.push<bool>(context, MaterialPageRoute(builder: (_) => PantallaProgramarDespacho(d: d)));
                      if (cambiado == true) await recargar();
                    },
                    icon: const Icon(Icons.edit_calendar_outlined),
                    label: Text(d['eta'] == null ? 'Programar el despacho' : 'Cambiar la cita'),
                  )
                else if (!esVendedor)
                  const AvisoWeb(texto: 'La cita la fija el vendedor; la llegada real la estampa el técnico verificador.'),
              ],
            ),
          );
        },
      ),
    );
  }
}

class PantallaProgramarDespacho extends StatefulWidget {
  final Map<String, dynamic> d;
  const PantallaProgramarDespacho({super.key, required this.d});
  @override
  State<PantallaProgramarDespacho> createState() => _PantallaProgramarDespachoState();
}

class _PantallaProgramarDespachoState extends State<PantallaProgramarDespacho> {
  DateTime? _cosecha;
  DateTime? _salida;
  DateTime? _eta;
  late final _transportista = TextEditingController(text: widget.d['carrier_name'] as String? ?? '');
  late final _placa = TextEditingController(text: widget.d['vehicle_plate'] as String? ?? '');
  late final _telefono = TextEditingController(text: widget.d['carrier_phone'] as String? ?? '');
  late final _notas = TextEditingController(text: widget.d['notes'] as String? ?? '');
  bool _ocupado = false;

  @override
  void initState() {
    super.initState();
    _cosecha = aFecha(widget.d['harvest_date']);
    _salida = aFecha(widget.d['farm_departure']);
    _eta = aFecha(widget.d['eta']);
  }

  @override
  void dispose() {
    _transportista.dispose();
    _placa.dispose();
    _telefono.dispose();
    _notas.dispose();
    super.dispose();
  }

  Future<DateTime?> _elegirFechaHora(DateTime? actual, {bool conHora = true}) async {
    final desde = DateTime.now().subtract(const Duration(days: 30));
    final hasta = DateTime.now().add(const Duration(days: 365));
    final base = fechaEntre(actual ?? DateTime.now(), desde, hasta);
    final fecha = await showDatePicker(
      context: context,
      initialDate: base,
      firstDate: desde,
      lastDate: hasta,
    );
    if (fecha == null || !mounted) return actual;
    if (!conHora) return fecha;
    final hora = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(base));
    if (hora == null) return actual;
    return DateTime(fecha.year, fecha.month, fecha.day, hora.hour, hora.minute);
  }

  Future<void> _guardar() async {
    if (_ocupado) return;
    final cuerpo = cuerpoDespacho(
      cosecha: _cosecha,
      salida: _salida,
      eta: _eta,
      transportista: _transportista.text,
      placa: _placa.text,
      telefono: _telefono.text,
      notas: _notas.text,
    );
    if (cuerpo.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Indica al menos la llegada prevista.')));
      return;
    }
    setState(() => _ocupado = true);
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.programarDespacho, widget.d['id'] as String, cuerpo);
    if (!mounted) return;
    setState(() => _ocupado = false);
    avisarResultado(context, r, hecho: 'Despacho programado; se avisa a la empacadora y a la verificadora');
    if (r.error == null) Navigator.pop(context, r.enviado);
  }

  @override
  Widget build(BuildContext context) {
    final fmt = DateFormat('dd/MM/yyyy HH:mm');
    return Scaffold(
      appBar: AppBar(title: const Text('Programar despacho')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
        children: [
          const Rotulo('Fechas'),
          _FilaFecha(
            etiqueta: 'Fecha de cosecha',
            valor: _cosecha == null ? 'Sin fijar' : DateFormat('dd/MM/yyyy').format(_cosecha!),
            onTap: () async {
              final f = await _elegirFechaHora(_cosecha, conHora: false);
              if (mounted) setState(() => _cosecha = f);
            },
          ),
          _FilaFecha(
            etiqueta: 'Salida de la finca',
            valor: _salida == null ? 'Sin fijar' : fmt.format(_salida!),
            onTap: () async {
              final f = await _elegirFechaHora(_salida);
              if (mounted) setState(() => _salida = f);
            },
          ),
          _FilaFecha(
            etiqueta: 'Llegada prevista a planta (ETA)',
            valor: _eta == null ? 'Sin fijar' : fmt.format(_eta!),
            destacado: true,
            onTap: () async {
              final f = await _elegirFechaHora(_eta);
              if (mounted) setState(() => _eta = f);
            },
          ),
          const Rotulo('Transporte'),
          TextField(controller: _transportista, decoration: const InputDecoration(labelText: 'Transportista', prefixIcon: Icon(Icons.local_shipping_outlined))),
          const SizedBox(height: 12),
          TextField(controller: _placa, maxLength: 16, textCapitalization: TextCapitalization.characters, decoration: const InputDecoration(labelText: 'Placa', prefixIcon: Icon(Icons.pin_outlined))),
          const SizedBox(height: 12),
          TextField(controller: _telefono, maxLength: 32, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Teléfono del chofer', prefixIcon: Icon(Icons.phone_outlined))),
          const SizedBox(height: 12),
          TextField(controller: _notas, maxLines: 2, decoration: const InputDecoration(labelText: 'Notas')),
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: _ocupado ? null : _guardar,
            icon: const Icon(Icons.save_outlined),
            label: Text(_ocupado ? 'Enviando…' : 'Guardar la cita'),
          ),
          const SizedBox(height: 8),
          const Text('Cambiar la hora de llegada avisa a la empacadora y a la verificadora.',
              textAlign: TextAlign.center, style: TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
        ],
      ),
    );
  }
}

class _FilaFecha extends StatelessWidget {
  final String etiqueta;
  final String valor;
  final bool destacado;
  final Future<void> Function() onTap;
  const _FilaFecha({required this.etiqueta, required this.valor, required this.onTap, this.destacado = false});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Tarjeta(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          onTap: onTap,
          child: Row(children: [
            Icon(Icons.event_outlined, color: destacado ? Marca.brand : Marca.gris),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(etiqueta, style: const TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
                Text(valor, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: destacado ? Marca.brand : Marca.tinta)),
              ]),
            ),
            const Icon(Icons.edit_outlined, size: 18, color: Marca.gris),
          ]),
        ),
      );
}
