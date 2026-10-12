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

/// Catálogo de lotes publicados (`GET /public/products`) y compra con
/// verificación (`POST /verifications`).
class PantallaCatalogo extends StatefulWidget {
  const PantallaCatalogo({super.key});
  @override
  State<PantallaCatalogo> createState() => _PantallaCatalogoState();
}

class _PantallaCatalogoState extends State<PantallaCatalogo> {
  String _tipo = 'camaronera';
  final _busqueda = TextEditingController();
  String _q = '';

  @override
  void dispose() {
    _busqueda.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Catálogo de lotes')),
      body: ListaRemota(
        ruta: '/public/products',
        params: {if (_tipo != 'todos') 'seller_type': _tipo, if (_q.isNotEmpty) 'q': _q},
        vacio: 'No hay lotes publicados con ese filtro.',
        cabecera: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Column(children: [
            TextField(
              controller: _busqueda,
              textInputAction: TextInputAction.search,
              onSubmitted: (v) => setState(() => _q = v.trim()),
              decoration: InputDecoration(
                hintText: 'Buscar por nombre',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _q.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.clear),
                        onPressed: () => setState(() {
                          _busqueda.clear();
                          _q = '';
                        })),
                isDense: true,
              ),
            ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: SegmentedButton<String>(
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'camaronera', label: Text('Camarón')),
                  ButtonSegment(value: 'laboratorio', label: Text('Larva')),
                  ButtonSegment(value: 'todos', label: Text('Todos')),
                ],
                selected: {_tipo},
                onSelectionChanged: (s) => setState(() => _tipo = s.first),
              ),
            ),
          ]),
        ),
        tarjeta: (c, p) => TarjetaRegistro(
          titulo: (p['name'] ?? '') as String,
          subtitulo: [
            nombreDe(p['species']),
            if (p['presentation'] != null) presentacion(p['presentation'] as String?),
            if (p['size_grade'] is Map) 'talla ${nombreDe(p['size_grade'])}',
          ].join(' · '),
          estado: p['requires_verification'] == true ? 'assigned' : 'published',
          estadoTexto: p['requires_verification'] == true ? 'Con verificación' : 'Publicado',
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaLotePublico(id: p['id'] as String))),
          datos: [
            Dato(icono: Icons.water_outlined, texto: '${nombreDe(p['seller'])}${p['location'] != null ? ' · ${p['location']}' : ''}'),
            Dato(icono: Icons.scale_outlined, texto: '${cantidad(p['available_qty'], p['uom'])} · ${dinero(p['price'])} por ${nombreDe(p['uom'])}'),
          ],
        ),
      ),
    );
  }
}

/// Detalle público del lote (`GET /public/products/{id}`) y botón de compra.
class PantallaLotePublico extends StatelessWidget {
  final String id;
  const PantallaLotePublico({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Lote publicado')),
      body: CargaRemota<ResultadoLista>(
        cargar: (repo) => repo.detalle('/public/products/$id'),
        hijo: (c, r, _, _, recargar) {
          final p = r.datos.first;
          final certificados = (p['certificates'] as List?) ?? [];
          final valoracion = p['seller_rating'] is Map ? p['seller_rating'] as Map : {};
          return RefreshIndicator(
            color: Marca.brand,
            onRefresh: recargar,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Text((p['name'] ?? '') as String, style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 4),
                Text('${nombreDe(p['seller'])} · ${numero(valoracion['avg'])} ★ (${entero(valoracion['count'])})', style: Theme.of(context).textTheme.bodySmall),
                const Rotulo('Producto'),
                SeccionDatos(filas: [
                  ('Especie', nombreDe(p['species'])),
                  ('Estadío', nombreDe(p['stage'])),
                  ('Genética', nombreDe(p['genetics_line'])),
                  if (p['presentation'] != null) ('Presentación', presentacion(p['presentation'] as String?)),
                  if (p['size_grade'] is Map) ('Talla', nombreDe(p['size_grade'])),
                  if (p['avg_size_mg'] != null) ('Tamaño medio', '${numero(p['avg_size_mg'])} mg'),
                  if (p['survival_rate'] != null) ('Supervivencia', porcentaje(p['survival_rate'])),
                  ('Disponible', cantidad(p['available_qty'], p['uom'])),
                  ('Precio', '${dinero(p['price'])} por ${nombreDe(p['uom'])}'),
                  if (p['location'] != null) ('Ubicación', '${p['location']}'),
                  if (p['expected_delivery_date'] != null) ('Entrega prevista', fecha(p['expected_delivery_date'])),
                  ('Verificación', p['requires_verification'] == true ? 'Se compra con verificación' : 'No requiere'),
                ]),
                if (certificados.isNotEmpty) ...[
                  const Rotulo('Certificados'),
                  SeccionDatos(filas: [
                    for (final c in certificados.whereType<Map>())
                      (nombreDe(c['certificate']), '${c['issuer'] ?? ''} · vence ${fecha(c['expiry_date'])}${c['valid'] == false ? ' (vencido)' : ''}'),
                  ]),
                ],
                const SizedBox(height: 16),
                if (p['requires_verification'] == true)
                  FilledButton.icon(
                    onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaComprar(p: p))),
                    icon: const Icon(Icons.verified_outlined),
                    label: const Text('Comprar con verificación'),
                  )
                else
                  const AvisoWeb(texto: 'La compra de este lote (sin verificación en campo) se hace desde el portal web.'),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// Compra con verificación: `POST /verifications {product, qty,
/// verification_mode, verifier}`. Las verificadoras acreditadas salen de
/// `GET /public/directory/verifiers`.
class PantallaComprar extends StatefulWidget {
  final Map<String, dynamic> p;
  const PantallaComprar({super.key, required this.p});
  @override
  State<PantallaComprar> createState() => _PantallaComprarState();
}

class _PantallaComprarState extends State<PantallaComprar> {
  final _qty = TextEditingController();
  String _modo = 'platform';
  String? _verificadora;
  bool _ocupado = false;

  @override
  void dispose() {
    _qty.dispose();
    super.dispose();
  }

  Future<void> _comprar() async {
    if (_ocupado) return;
    final qty = numeroEscrito(_qty.text);
    final disponible = (widget.p['available_qty'] is num ? widget.p['available_qty'] as num : 0).toDouble();
    if (qty == null || qty <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Escribe la cantidad a comprar.')));
      return;
    }
    if (qty > disponible) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Solo hay ${numero(disponible)} disponibles.')));
      return;
    }
    if (_modo == 'platform' && _verificadora == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Elige la verificadora.')));
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Confirmar la compra'),
        content: Text('Compras ${numero(qty)} ${nombreDe(widget.p['uom'])} de "${widget.p['name']}" a ${dinero(widget.p['price'])} por ${nombreDe(widget.p['uom'])}. '
            '${_modo == 'platform' ? 'La verificación en campo la hace la verificadora elegida y su honorario se factura al iniciarla.' : 'La verificación la declaran las partes (sin verificadora de la plataforma).'}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Comprar')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _ocupado = true);
    final productoId = '${widget.p['id']}';
    final r = await context.read<Sincronizador>().ejecutar(
          TipoOperacion.compraVerificada,
          productoId,
          cuerpoCompra(productoId: productoId, cantidad: qty, modo: _modo, verificadoraId: _verificadora),
        );
    if (!mounted) return;
    setState(() => _ocupado = false);
    avisarResultado(context, r, hecho: 'Compra registrada: queda pendiente de verificación');
    if (r.error == null) Navigator.popUntil(context, (ruta) => ruta.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.p;
    return Scaffold(
      appBar: AppBar(title: const Text('Comprar con verificación')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
        children: [
          const Rotulo('Lote'),
          SeccionDatos(filas: [
            ('Lote', '${p['name']}'),
            ('Vendedor', nombreDe(p['seller'])),
            ('Disponible', cantidad(p['available_qty'], p['uom'])),
            ('Precio', '${dinero(p['price'])} por ${nombreDe(p['uom'])}'),
          ]),
          const Rotulo('Cantidad'),
          TextField(
            controller: _qty,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(labelText: 'Cantidad (${nombreDe(p['uom'])})', prefixIcon: const Icon(Icons.scale_outlined)),
          ),
          const Rotulo('Verificación'),
          Tarjeta(
            padding: EdgeInsets.zero,
            child: RadioGroup<String>(
              groupValue: _modo,
              onChanged: (v) => setState(() => _modo = v ?? 'platform'),
              child: const Column(children: [
                RadioListTile(value: 'platform', title: Text('Verificadora de la plataforma'), subtitle: Text('Un técnico acreditado va a la finca o a la planta')),
                RadioListTile(value: 'declared', title: Text('Declarada por las partes'), subtitle: Text('Comprador o vendedor cargan el informe; la otra parte lo acepta')),
              ]),
            ),
          ),
          if (_modo == 'platform') ...[
            const Rotulo('Verificadora'),
            CargaRemota<ResultadoLista>(
              compacto: true,
              cargar: (repo) => repo.todo('/public/directory/verifiers'),
              hijo: (c, r, _, _, _) {
                final lista = r.datos;
                if (lista.isEmpty) {
                  return const AvisoWeb(texto: 'No hay verificadoras acreditadas en el directorio. Elige la verificadora desde el portal web.');
                }
                return Tarjeta(
                  padding: EdgeInsets.zero,
                  child: RadioGroup<String>(
                    groupValue: _verificadora,
                    onChanged: (v) => setState(() => _verificadora = v),
                    child: Column(children: [
                      for (final v in lista.where((v) => v['id'] is String))
                        RadioListTile<String>(
                          value: v['id'] as String,
                          title: Text((v['name'] ?? '') as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: Text([
                            if (v['province'] != null) '${v['province']}',
                            if (v['verifier'] is Map && v['verifier']['coverage'] != null) 'cobertura: ${v['verifier']['coverage']}',
                            if (v['ratings'] is Map && v['ratings']['as_verifier'] is Map) '${numero(v['ratings']['as_verifier']['avg'])} ★',
                          ].join(' · ')),
                        ),
                    ]),
                  ),
                );
              },
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
