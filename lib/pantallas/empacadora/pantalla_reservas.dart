import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Reservas de cosecha de la empacadora (`GET /harvest/commitments`) y compra
/// del lote nacido de un compromiso cumplido (`POST /harvest/commitments/{id}:buy`).
class PantallaReservas extends StatelessWidget {
  const PantallaReservas({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Reservas de cosecha')),
      body: ListaRemota(
        ruta: '/harvest/commitments',
        vacio: 'No tienes reservas de cosecha.',
        cabecera: const Padding(
          padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: AvisoWeb(texto: 'Las reservas nuevas se comprometen desde el portal web; aquí se consultan y se compra el lote cosechado.'),
        ),
        tarjeta: (c, r) => TarjetaRegistro(
          titulo: nombreDe(r['farmer']),
          subtitulo: r['product'] is Map ? nombreDe(r['product']) : 'Cosecha prevista',
          estado: (r['state'] ?? '') as String,
          estadoTexto: estadoReserva(r['state'] as String?),
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaReserva(id: r['id'] as String))),
          datos: [
            Dato(icono: Icons.scale_outlined, texto: '${libras(r['committed_lb'])} comprometidas'),
            Dato(icono: Icons.attach_money, texto: r['price_mode'] == 'fijo' ? 'Precio fijo ${dinero(r['price_per_lb'])}/lb' : 'Precio de lista${r['price_floor_per_lb'] != null ? ' (piso ${dinero(r['price_floor_per_lb'])}/lb)' : ''}'),
            if (r['valid_until'] != null) Dato(icono: Icons.event_outlined, texto: 'Válida hasta ${fecha(r['valid_until'])}'),
          ],
        ),
      ),
    );
  }
}

String estadoReserva(String? s) => switch (s) {
      'sent' => 'Enviada',
      'accepted' => 'Aceptada',
      'rejected' => 'Rechazada',
      'withdrawn' => 'Retirada',
      'to_confirm' => 'Por confirmar',
      'honored' => 'Cumplida',
      'released' => 'Liberada',
      'broken' => 'Rota',
      _ => s ?? '—',
    };

class PantallaReserva extends StatelessWidget {
  final String id;
  const PantallaReserva({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Reserva')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/harvest/commitments/$id'),
        hijo: (c, r, _, _, recargar) {
          final m = r.datos.first;
          final liq = m['settlement'] is Map ? m['settlement'] as Map : null;
          final puedeComprar = m['my_role'] == 'packer' && m['state'] == 'honored' && m['product'] is Map && m['transaction'] == null;
          return RefreshIndicator(
            color: Marca.brand,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Row(children: [
                  Expanded(child: Text(nombreDe(m['farmer']), style: Theme.of(context).textTheme.titleLarge)),
                  Insignia(estado: (m['state'] ?? '') as String, texto: estadoReserva(m['state'] as String?)),
                ]),
                const Rotulo('Compromiso'),
                SeccionDatos(filas: [
                  ('Libras comprometidas', libras(m['committed_lb'])),
                  ('Precio', m['price_mode'] == 'fijo' ? '${dinero(m['price_per_lb'])}/lb fijo' : 'Según lista de precios'),
                  if (m['price_floor_per_lb'] != null) ('Precio piso', '${dinero(m['price_floor_per_lb'])}/lb'),
                  if (m['step_delta_per_lb'] != null) ('Ajuste por talla', '${dinero(m['step_delta_per_lb'])}/lb por escalón'),
                  if (m['valid_until'] != null) ('Válida hasta', fecha(m['valid_until'])),
                  if (m['accepted_at'] != null) ('Aceptada', fechaHora(m['accepted_at'])),
                  if (m['notes'] != null) ('Notas', '${m['notes']}'),
                ]),
                if (liq != null) ...[
                  const Rotulo('Liquidación'),
                  SeccionDatos(filas: [
                    ('Libras', libras(liq['lb'])),
                    ('Precio', '${dinero(liq['price_per_lb'])}/lb'),
                    ('Total', dinero(liq['total'])),
                    if (liq['deviation_notes'] != null) ('Desviaciones', '${liq['deviation_notes']}'),
                  ]),
                ],
                if (m['product'] is Map) ...[
                  const Rotulo('Lote cosechado'),
                  SeccionDatos(filas: [
                    ('Lote', nombreDe(m['product'])),
                    if (m['transaction'] is Map) ('Compra', nombreDe(m['transaction'])),
                  ]),
                ],
                const SizedBox(height: 16),
                if (puedeComprar && context.read<Sesion>().soloLectura)
                  const AvisoSoloLectura()
                else if (puedeComprar)
                  FilledButton.icon(
                    onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => _PantallaComprarReserva(m: m))),
                    icon: const Icon(Icons.verified_outlined),
                    label: const Text('Comprar el lote con verificación'),
                  )
                else
                  const AvisoWeb(texto: 'Retirar o desistir de una reserva, y las confirmaciones de cosecha, se hacen desde el portal web.'),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _PantallaComprarReserva extends StatefulWidget {
  final Map<String, dynamic> m;
  const _PantallaComprarReserva({required this.m});
  @override
  State<_PantallaComprarReserva> createState() => _PantallaComprarReservaState();
}

class _PantallaComprarReservaState extends State<_PantallaComprarReserva> {
  String _modo = 'platform';
  String? _verificadora;
  bool _ocupado = false;

  Future<void> _comprar() async {
    if (_ocupado) return;
    if (_modo == 'platform' && _verificadora == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Elige la verificadora.')));
      return;
    }
    setState(() => _ocupado = true);
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.comprarReserva, '${widget.m['id']}',
        cuerpoCompraReserva(modo: _modo, verificadoraId: _verificadora));
    if (!mounted) return;
    setState(() => _ocupado = false);
    avisarResultado(context, r, hecho: 'Compra registrada: queda pendiente de verificación');
    if (r.error == null) Navigator.popUntil(context, (ruta) => ruta.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Comprar el lote reservado')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
        children: [
          const Rotulo('Lote'),
          SeccionDatos(filas: [
            ('Lote', nombreDe(widget.m['product'])),
            ('Camaronera', nombreDe(widget.m['farmer'])),
            ('Libras', libras(widget.m['committed_lb'])),
          ]),
          const Rotulo('Verificación'),
          Tarjeta(
            padding: EdgeInsets.zero,
            child: RadioGroup<String>(
              groupValue: _modo,
              onChanged: (v) => setState(() => _modo = v ?? 'platform'),
              child: const Column(children: [
                RadioListTile(value: 'platform', title: Text('Verificadora de la plataforma')),
                RadioListTile(value: 'declared', title: Text('Declarada por las partes')),
              ]),
            ),
          ),
          if (_modo == 'platform') ...[
            const Rotulo('Verificadora'),
            CargaRemota<ResultadoLista>(
              compacto: true,
              cargar: (repo) => repo.todo('/public/directory/verifiers'),
              hijo: (c, r, _, _, _) => r.datos.isEmpty
                  ? const AvisoWeb(texto: 'No hay verificadoras acreditadas en el directorio. Elige la verificadora desde el portal web.')
                  : Tarjeta(
                      padding: EdgeInsets.zero,
                      child: RadioGroup<String>(
                        groupValue: _verificadora,
                        onChanged: (v) => setState(() => _verificadora = v),
                        child: Column(children: [
                          for (final v in r.datos.where((v) => v['id'] is String))
                            RadioListTile<String>(value: v['id'] as String, title: Text((v['name'] ?? '') as String), subtitle: v['province'] == null ? null : Text('${v['province']}')),
                        ]),
                      ),
                    ),
            ),
          ],
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: _ocupado ? null : _comprar,
            icon: const Icon(Icons.shopping_cart_checkout),
            label: Text(_ocupado ? 'Enviando…' : 'Comprar'),
          ),
        ],
      ),
    );
  }
}
