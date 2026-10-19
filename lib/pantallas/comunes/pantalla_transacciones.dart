import 'package:flutter/material.dart';

import '../../datos/repositorio.dart';
import '../../tema.dart';
import 'formato.dart';
import 'lista_remota.dart';
import 'menu_cuenta.dart';
import 'widgets.dart';

/// Mis ventas (`GET /transactions?role=seller`) o Mis compras (`role=buyer`).
class PantallaTransacciones extends StatefulWidget {
  /// seller | buyer
  final String rol;
  const PantallaTransacciones({super.key, required this.rol});
  @override
  State<PantallaTransacciones> createState() => _PantallaTransaccionesState();
}

class _PantallaTransaccionesState extends State<PantallaTransacciones> {
  String _filtro = 'curso';

  @override
  Widget build(BuildContext context) {
    final esVenta = widget.rol == 'seller';
    return Scaffold(
      appBar: AppBar(title: Text(esVenta ? 'Mis ventas' : 'Mis compras')),
      body: ListaRemota(
        ruta: '/transactions',
        params: {'role': widget.rol},
        vacio: esVenta ? 'No hay ventas en esta lista.' : 'No hay compras en esta lista.',
        filtro: (t) => switch (_filtro) {
          'curso' => transaccionEnCurso(t['state'] as String?),
          'cerradas' => !transaccionEnCurso(t['state'] as String?),
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
                ButtonSegment(value: 'cerradas', label: Text('Cerradas')),
                ButtonSegment(value: 'todas', label: Text('Todas')),
              ],
              selected: {_filtro},
              onSelectionChanged: (s) => setState(() => _filtro = s.first),
            ),
          ),
        ),
        tarjeta: (c, t) => TarjetaTransaccion(t: t),
      ),
    );
  }
}

class TarjetaTransaccion extends StatelessWidget {
  final Map<String, dynamic> t;
  const TarjetaTransaccion({super.key, required this.t});

  @override
  Widget build(BuildContext context) {
    final esVenta = t['my_role'] == 'seller';
    return TarjetaRegistro(
      titulo: (t['reference'] ?? '') as String,
      subtitulo: nombreDe(t['product']),
      estado: (t['state'] ?? '') as String,
      estadoTexto: estadoTransaccion(t['state'] as String?),
      onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaTransaccion(id: t['id'] as String))),
      datos: [
        Dato(icono: esVenta ? Icons.factory_outlined : Icons.water_outlined, texto: esVenta ? nombreDe(t['buyer']) : nombreDe(t['seller'])),
        Dato(icono: Icons.scale_outlined, texto: '${cantidad(t['qty'], t['uom'])} · ${dinero(t['total'])}'),
        if (t['delivery_date'] != null) Dato(icono: Icons.event_outlined, texto: 'Entrega: ${fecha(t['delivery_date'])}'),
      ],
    );
  }
}

/// Detalle de una compra o venta: estado, importes, verificación y
/// trazabilidad (`GET /transactions/{id}/traceability`: texto + enlace público).
/// No hay botón de "concluir": la compra pasa sola a confirmada cuando las
/// dos partes aceptan el informe.
class PantallaTransaccion extends StatelessWidget {
  final String id;
  const PantallaTransaccion({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Detalle')),
      body: CargaRemota<List<ResultadoLista>>(
        cargar: (repo) async {
          final t = await repo.detalle('/transactions/$id');
          ResultadoLista? traza;
          try {
            traza = await repo.detalle('/transactions/$id/traceability');
          } catch (_) {
            // Sin trazabilidad todavía (o sin permiso): se muestra el resto.
          }
          return [t, ?traza];
        },
        hijo: (c, datos, desdeCache, _, recargar) {
          final t = datos.first.datos.first;
          final traza = datos.length > 1 ? datos[1].datos.first : null;
          return _DetalleTransaccion(t: t, traza: traza, recargar: recargar);
        },
      ),
    );
  }
}

class _DetalleTransaccion extends StatelessWidget {
  final Map<String, dynamic> t;
  final Map<String, dynamic>? traza;
  final Future<void> Function() recargar;
  const _DetalleTransaccion({required this.t, required this.traza, required this.recargar});

  @override
  Widget build(BuildContext context) {
    final esComprador = t['my_role'] == 'buyer';
    final estado = t['state'] as String?;
    final publico = traza?['public'] is Map ? traza!['public'] as Map : null;
    final cadena = (publico?['chain'] as List?) ?? [];
    final fechas = publico?['dates'] is Map ? publico!['dates'] as Map : {};
    final verif = publico?['verification'] is Map ? publico!['verification'] as Map : null;
    final urlPublica = traza?['public_url'] as String?;
    final factura = t['seller_invoice'] is Map ? t['seller_invoice'] as Map : null;

    return RefreshIndicator(
      color: context.colores.marcaTexto,
      onRefresh: recargar,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(children: [
            Expanded(child: Text((t['reference'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge)),
            Insignia(estado: estado ?? '', texto: estadoTransaccion(estado)),
          ]),
          const SizedBox(height: 4),
          Text(nombreDe(t['product']), style: Theme.of(context).textTheme.titleMedium),
          const Rotulo('Operación'),
          SeccionDatos(filas: [
            (esComprador ? 'Vendedor' : 'Comprador', esComprador ? nombreDe(t['seller']) : nombreDe(t['buyer'])),
            ('Cantidad', cantidad(t['qty'], t['uom'])),
            ('Precio unitario', dinero(t['unit_price'])),
            ('Total', dinero(t['total'])),
            ('Entrega', fecha(t['delivery_date'])),
            if (t['location'] != null) ('Lugar', '${t['location']}'),
            ('Verificación', t['needs_verification'] == true ? (t['verification_mode'] == 'declared' ? 'Declarada por las partes' : 'Verificadora de la plataforma') : 'No requiere'),
            if (factura != null) ('Factura del vendedor', '${factura['number'] ?? '—'} · ${fecha(factura['date'])}'),
          ]),
          if (estado == 'pending_acceptance') ...[
            const SizedBox(height: 12),
            Text(
              esComprador
                  ? 'La compra se confirma sola cuando tú y el vendedor aceptan el informe de verificación.'
                  : 'La venta se confirma sola cuando tú y el comprador aceptan el informe de verificación.',
              style: TextStyle(fontSize: 12.5, color: context.colores.tintaSuave),
            ),
          ],
          const Rotulo('Trazabilidad'),
          if (traza == null)
            Tarjeta(child: Text('La trazabilidad estará disponible cuando la compra avance.', style: TextStyle(color: context.colores.tintaSuave)))
          else ...[
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                if (publico?['product'] is Map) ...[
                  Text(_productoTexto(publico!['product'] as Map), style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 10),
                ],
                for (final e in cadena.whereType<Map>())
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Row(children: [
                      Icon(Icons.arrow_right_alt, size: 18, color: context.colores.tintaTenue),
                      const SizedBox(width: 6),
                      Expanded(child: Text('${e['role']}: ${e['company']} (${e['province'] ?? '—'})', style: const TextStyle(fontSize: 14))),
                    ]),
                  ),
                if (fechas.isNotEmpty) ...[
                  const Divider(),
                  Text(
                    'Compra: ${fecha(fechas['purchase'])} · Cosecha: ${fecha(fechas['harvest'])} · Proceso: ${fecha(fechas['process'])} · Entrega: ${fecha(fechas['delivery'])}',
                    style: TextStyle(fontSize: 13, color: context.colores.tintaSuave),
                  ),
                ],
                if (verif != null) ...[
                  const Divider(),
                  Text(
                    'Verificación: ${verif['result'] ?? '—'} · ${verif['verifier'] ?? ''} · rendimiento ${porcentaje(verif['yield_pct'])}',
                    style: TextStyle(fontSize: 13, color: context.colores.tintaSuave),
                  ),
                ],
              ]),
            ),
            if (urlPublica != null) ...[
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed: () => abrirEnlace(context, urlPublica),
                icon: const Icon(Icons.qr_code_2),
                label: const Text('Abrir la trazabilidad pública'),
              ),
            ],
          ],
          const SizedBox(height: 16),
          const AvisoWeb(texto: 'El certificado PDF, la factura y los pagos se gestionan desde el portal web.'),
        ],
      ),
    );
  }

  String _productoTexto(Map p) {
    final origen = p['origin'] is Map ? p['origin'] as Map : {};
    return [
      p['name'],
      p['species'],
      if (p['presentation'] != null) presentacion(p['presentation'] as String?),
      if (p['size_grade'] != null) 'talla ${p['size_grade']}',
      if (origen['facility'] != null) 'origen ${origen['facility']}',
    ].whereType<Object>().join(' · ');
  }
}
