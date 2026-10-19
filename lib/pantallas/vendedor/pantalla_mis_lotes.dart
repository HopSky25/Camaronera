import 'package:flutter/material.dart';

import '../../datos/repositorio.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';

/// Mis lotes (`GET /products`, solo lectura): publicados con las unidades
/// disponibles, borradores y vendidos.
class PantallaMisLotes extends StatefulWidget {
  const PantallaMisLotes({super.key});
  @override
  State<PantallaMisLotes> createState() => _PantallaMisLotesState();
}

class _PantallaMisLotesState extends State<PantallaMisLotes> {
  String _estado = 'published';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Mis lotes')),
      body: ListaRemota(
        ruta: '/products',
        params: {if (_estado != 'todos') 'state': _estado},
        vacio: 'No hay lotes en esta lista.',
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            width: double.infinity,
            child: SegmentedButton<String>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: 'published', label: Text('Publicados')),
                ButtonSegment(value: 'draft', label: Text('Borrador')),
                ButtonSegment(value: 'sold', label: Text('Vendidos')),
                ButtonSegment(value: 'todos', label: Text('Todos')),
              ],
              selected: {_estado},
              onSelectionChanged: (s) => setState(() => _estado = s.first),
            ),
          ),
        ),
        tarjeta: (c, p) => TarjetaRegistro(
          titulo: (p['name'] ?? '') as String,
          subtitulo: [
            nombreDe(p['species']),
            if (p['stage'] is Map) nombreDe(p['stage']),
            if (p['presentation'] != null) presentacion(p['presentation'] as String?),
            if (p['size_grade'] is Map) 'talla ${nombreDe(p['size_grade'])}',
          ].join(' · '),
          estado: (p['state'] ?? '') as String,
          estadoTexto: estadoProducto(p['state'] as String?),
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaLote(id: p['id'] as String))),
          datos: [
            Dato(icono: Icons.scale_outlined, texto: '${cantidad(p['available_qty'], p['uom'])} disponibles de ${cantidad(p['initial_qty'], p['uom'])}'),
            Dato(icono: Icons.attach_money, texto: precioPorUnidad(p['price'], p['uom'])),
            if (p['origin_pond'] is Map) Dato(icono: Icons.water_outlined, texto: '${nombreDe(p['origin_facility'])} · ${nombreDe(p['origin_pond'])}'),
          ],
        ),
      ),
    );
  }
}

/// Detalle de un lote propio (`GET /products/{id}`), solo lectura.
class PantallaLote extends StatelessWidget {
  final String id;
  const PantallaLote({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Lote')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/products/$id'),
        hijo: (c, r, _, _, recargar) {
          final p = r.datos.first;
          final certificados = (p['certificates'] as List?) ?? [];
          return RefreshIndicator(
            color: context.colores.marcaTexto,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Row(children: [
                  Expanded(child: Text((p['name'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge)),
                  Insignia(estado: (p['state'] ?? '') as String, texto: estadoProducto(p['state'] as String?)),
                ]),
                const Rotulo('Producto'),
                SeccionDatos(filas: [
                  ('Especie', nombreDe(p['species'])),
                  ('Estadío', nombreDe(p['stage'])),
                  ('Genética', nombreDe(p['genetics_line'])),
                  if (p['presentation'] != null) ('Presentación', presentacion(p['presentation'] as String?)),
                  if (p['size_grade'] is Map) ('Talla', nombreDe(p['size_grade'])),
                  if (p['avg_size_mg'] != null) ('Tamaño medio', '${numero(p['avg_size_mg'])} mg'),
                  if (p['survival_rate'] != null) ('Supervivencia', porcentaje(p['survival_rate'])),
                  if (p['health_status'] != null) ('Sanidad', '${p['health_status']}'),
                ]),
                const Rotulo('Cantidad y precio'),
                SeccionDatos(filas: [
                  ('Disponible', cantidad(p['available_qty'], p['uom'])),
                  ('Reservado', cantidad(p['reserved_qty'], p['uom'])),
                  ('Inicial', cantidad(p['initial_qty'], p['uom'])),
                  ('Precio', precioPorUnidad(p['price'], p['uom'])),
                  ('Requiere verificación', p['requires_verification'] == true ? 'Sí' : 'No'),
                  if (p['published_at'] != null) ('Publicado', fechaHora(p['published_at'])),
                ]),
                const Rotulo('Origen'),
                SeccionDatos(filas: [
                  ('Finca', nombreDe(p['origin_facility'])),
                  ('Piscina', nombreDe(p['origin_pond'])),
                  if (p['batch_code'] != null) ('Código de lote', '${p['batch_code']}'),
                  if (p['production_date'] != null) ('Fecha de producción', fecha(p['production_date'])),
                  if (p['location'] != null) ('Ubicación', '${p['location']}'),
                ]),
                if (certificados.isNotEmpty) ...[
                  const Rotulo('Certificados'),
                  SeccionDatos(filas: [
                    for (final c in certificados.whereType<Map>())
                      (nombreDe(c['certificate']), '${c['issuer'] ?? ''} ${c['number'] ?? ''} · vence ${fecha(c['expiry_date'])}'.trim()),
                  ]),
                ],
                const SizedBox(height: 16),
                const AvisoWeb(texto: 'Editar el lote, publicarlo o darlo de baja se hace desde el portal web.'),
              ],
            ),
          );
        },
      ),
    );
  }
}
