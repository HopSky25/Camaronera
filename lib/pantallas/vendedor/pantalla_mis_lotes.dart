import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../modelos/gestion_lote.dart';
import '../../modelos/lote_nuevo.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';
import 'pantalla_fotos_lote.dart';
import 'pantalla_publicar_lote.dart';

/// Mis lotes (`GET /products`): publicados con las unidades disponibles,
/// borradores, vendidos y archivados (los retirados, que se reactivan), y
/// el botón «Publicar lote» (no en modo supervisor).
class PantallaMisLotes extends StatefulWidget {
  const PantallaMisLotes({super.key});
  @override
  State<PantallaMisLotes> createState() => _PantallaMisLotesState();
}

class _PantallaMisLotesState extends State<PantallaMisLotes> {
  String _estado = 'published';
  final _lista = GlobalKey<ListaRemotaState>();

  Future<void> _abrir(Widget pantalla) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => pantalla));
    // Se pudo publicar o retirar un lote: la lista se pone al día.
    await _lista.currentState?.recargar();
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final publica = !sesion.soloLectura && puedePublicarLotes(sesion.perfil);
    return Scaffold(
      appBar: AppBar(title: const Text('Mis lotes')),
      body: ListaRemota(
        key: _lista,
        ruta: '/products',
        params: {if (_estado != 'todos') 'state': _estado},
        vacio: 'No hay lotes en esta lista.',
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Column(children: [
            if (publica) ...[
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  icon: const Icon(Icons.add),
                  label: const Text('Publicar lote'),
                  onPressed: () => _abrir(const PantallaPublicarLote()),
                ),
              ),
              const SizedBox(height: 10),
            ],
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: SegmentedButton<String>(
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'published', label: Text('Publicados')),
                  ButtonSegment(value: 'draft', label: Text('Borrador')),
                  ButtonSegment(value: 'sold', label: Text('Vendidos')),
                  ButtonSegment(value: 'archived', label: Text('Archivados')),
                  ButtonSegment(value: 'todos', label: Text('Todos')),
                ],
                selected: {_estado},
                onSelectionChanged: (s) => setState(() => _estado = s.first),
              ),
            ),
          ]),
        ),
        tarjeta: (c, p) {
          final (estado, estadoTexto) = estadoDeLote(p);
          return TarjetaRegistro(
          titulo: (p['name'] ?? '') as String,
          subtitulo: [
            nombreDe(p['species']),
            if (p['stage'] is Map) nombreDe(p['stage']),
            if (p['presentation'] != null) presentacion(p['presentation'] as String?),
            if (p['size_grade'] is Map) 'talla ${nombreDe(p['size_grade'])}',
          ].join(' · '),
          estado: estado,
          estadoTexto: estadoTexto,
          onTap: () => _abrir(PantallaLote(id: p['id'] as String)),
          datos: [
            Dato(icono: Icons.scale_outlined, texto: '${cantidad(p['available_qty'], p['uom'])} disponibles de ${cantidad(p['initial_qty'], p['uom'])}'),
            Dato(icono: Icons.attach_money, texto: precioPorUnidad(p['price'], p['uom'])),
            if (p['origin_pond'] is Map) Dato(icono: Icons.water_outlined, texto: '${nombreDe(p['origin_facility'])} · ${nombreDe(p['origin_pond'])}'),
            if (p['photos'] is List && (p['photos'] as List).isEmpty && p['state'] != 'cancel')
              Dato(icono: Icons.hide_image_outlined, texto: 'Sin fotos', color: context.colores.advertenciaTexto),
          ],
        );
        },
      ),
    );
  }
}

/// Detalle de un lote propio (`GET /products/{id}`) con sus fotos, sus
/// certificados y sus acciones ([AccionesLote]): publicar, editar, fotos,
/// certificados, retirar, reactivar y eliminar el borrador.
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
          final (estado, estadoTexto) = estadoDeLote(p);
          final lineas = ((p['certificate_lines'] ?? p['certificates']) as List?) ?? [];
          final fotos = fotosDeLote(p);
          final colores = context.colores;
          return RefreshIndicator(
            color: colores.marcaTexto,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Row(children: [
                  Expanded(child: Text((p['name'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge)),
                  Insignia(estado: estado, texto: estadoTexto),
                ]),
                if (fotos.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  SizedBox(
                    height: 96,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: fotos.length,
                      separatorBuilder: (_, _) => const SizedBox(width: 8),
                      itemBuilder: (_, i) => MiniaturaFotoLote(productoId: id, fotoId: '${fotos[i]['id']}', lado: 96),
                    ),
                  ),
                ],
                if (motivoBloqueo(p) != null) ...[
                  const SizedBox(height: 12),
                  CajaAviso(
                    tipo: TipoAviso.advertencia,
                    icono: Icons.lock_outline,
                    texto: 'Precio y datos del lote bloqueados: el lote ${motivoBloqueo(p)}.',
                  ),
                ],
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
                  if (p['expected_delivery_date'] != null) ('Entrega estimada', fecha(p['expected_delivery_date'])),
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
                if (lineas.isNotEmpty) ...[
                  const Rotulo('Certificados'),
                  SeccionDatos(filas: [
                    for (final c in lineas.whereType<Map>())
                      (
                        nombreDe(c['certificate']),
                        [
                          '${c['issuer'] ?? ''} ${c['number'] ?? ''}'.trim(),
                          'vence ${fecha(c['expiry_date'])}',
                          if (c['status'] is String) estadoCertificado(c['status'] as String),
                        ].where((t) => t.isNotEmpty).join(' · '),
                      ),
                  ]),
                ],
                const SizedBox(height: 16),
                AccionesLote(p: p, alCambiar: recargar, alEliminar: () => Navigator.pop(context)),
              ],
            ),
          );
        },
      ),
    );
  }
}
