import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../datos/api_cliente.dart';
import '../../modelos/compra_directa.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../seguridad/firmador.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/pantalla_transacciones.dart';
import '../comunes/widgets.dart';

/// Catálogo de lotes publicados (`GET /public/products`): compra con
/// verificación (`POST /verifications`) o, para larva, nauplio y lotes sin
/// verificación obligatoria, compra directa (`POST /purchases`, C5).
class PantallaCatalogo extends StatefulWidget {
  /// Filtro inicial por tipo de etapa (`stage_type` de la API): camaron,
  /// larva (zoea, mysis, PL), nauplio o todos. Lo venda quien lo venda: un
  /// laboratorio también vende nauplio.
  final String tipoInicial;
  final String titulo;
  const PantallaCatalogo({super.key, this.tipoInicial = 'camaron', this.titulo = 'Catálogo de lotes'});
  @override
  State<PantallaCatalogo> createState() => _PantallaCatalogoState();
}

class _PantallaCatalogoState extends State<PantallaCatalogo> {
  late String _tipo = widget.tipoInicial;
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
      appBar: AppBar(title: Text(widget.titulo)),
      body: ListaRemota(
        ruta: '/public/products',
        params: {if (_tipo != 'todos') 'stage_type': _tipo, if (_q.isNotEmpty) 'q': _q},
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
                  ButtonSegment(value: 'camaron', label: Text('Camarón')),
                  ButtonSegment(value: 'larva', label: Text('Larva')),
                  ButtonSegment(value: 'nauplio', label: Text('Nauplio')),
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
          estadoTexto: p['requires_verification'] == true ? 'Con verificación' : 'Compra directa',
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaLotePublico(id: p['id'] as String))),
          datos: [
            Dato(icono: Icons.water_outlined, texto: '${nombreDe(p['seller'])}${p['location'] != null ? ' · ${p['location']}' : ''}'),
            Dato(icono: Icons.scale_outlined, texto: '${cantidad(p['available_qty'], p['uom'])} · ${precioPorUnidad(p['price'], p['uom'])}'),
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
            color: context.colores.marcaTexto,
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
                  ('Precio', precioPorUnidad(p['price'], p['uom'])),
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
                if (context.read<Sesion>().soloLectura)
                  const AvisoSoloLectura()
                else if (p['requires_verification'] == true)
                  FilledButton.icon(
                    onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaComprar(p: p))),
                    icon: const Icon(Icons.verified_outlined),
                    label: const Text('Comprar con verificación'),
                  )
                else
                  FilledButton.icon(
                    onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaCompraDirecta(p: p))),
                    icon: const Icon(Icons.shopping_cart_outlined),
                    label: const Text('Comprar'),
                  ),
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
        content: Text('Compras ${cantidad(qty, widget.p['uom'])} de "${widget.p['name']}" a ${precioPorUnidad(widget.p['price'], widget.p['uom'])}. '
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
            ('Precio', precioPorUnidad(p['price'], p['uom'])),
          ]),
          const Rotulo('Cantidad'),
          TextField(
            controller: _qty,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(labelText: 'Cantidad (${unidadPlural(p['uom'])})', prefixIcon: const Icon(Icons.scale_outlined)),
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

/// Compra directa (C5): cantidad (validada contra el disponible y la
/// unidad) → confirmación → huella / PIN → `POST /purchases` firmado →
/// compra registrada. Sin señal no se compra; el reintento usa la misma
/// operación firmada (misma Idempotency-Key: nunca compra dos veces).
class PantallaCompraDirecta extends StatefulWidget {
  final Map<String, dynamic> p;
  const PantallaCompraDirecta({super.key, required this.p});
  @override
  State<PantallaCompraDirecta> createState() => _PantallaCompraDirectaState();
}

class _PantallaCompraDirectaState extends State<PantallaCompraDirecta> {
  final _qty = TextEditingController();
  final _notas = TextEditingController();
  final _form = GlobalKey<FormState>();
  bool _ocupado = false;
  String? _error;
  bool _exigeVerificacion = false;

  /// Operación ya firmada que no llegó por falta de señal: "Reintentar" la
  /// reenvía tal cual.
  OperacionPendiente? _firmada;

  @override
  void dispose() {
    _qty.dispose();
    _notas.dispose();
    super.dispose();
  }

  Future<void> _comprar() async {
    if (_ocupado) return;
    final sinc = context.read<Sincronizador>();
    var op = _firmada;
    if (op == null) {
      if (!(_form.currentState?.validate() ?? false)) return;
      final qty = numeroEscrito(_qty.text)!;
      final total = totalEstimado(widget.p, qty);
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Confirmar la compra'),
          content: Text('Compras ${cantidad(qty, widget.p['uom'])} de "${widget.p['name']}" a '
              '${precioPorUnidad(widget.p['price'], widget.p['uom'])}'
              '${total == null ? '' : ' (unos ${dinero({'amount': total, 'currency': (widget.p['price'] as Map)['currency']})})'}. '
              'El precio final lo confirma CamaronMarket. Se te pedirá tu huella o el PIN del teléfono.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Comprar')),
          ],
        ),
      );
      if (ok != true || !mounted) return;
      setState(() {
        _ocupado = true;
        _error = null;
      });
      try {
        op = await sinc.firmarCompraDirecta(productoId: '${widget.p['id']}', cantidad: qty, notas: _notas.text);
      } on ErrorSeguridad catch (e) {
        if (mounted) setState(() => _error = e.cancelado ? null : e.mensaje);
        if (mounted && e.cancelado) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje)));
        }
        if (mounted) setState(() => _ocupado = false);
        return;
      } catch (e) {
        if (mounted) setState(() => _ocupado = false);
        if (mounted) setState(() => _error = mensajeDeError(e));
        return;
      }
    } else {
      setState(() {
        _ocupado = true;
        _error = null;
      });
    }
    try {
      final tx = await sinc.enviarCompraDirecta(op);
      if (!mounted) return;
      await Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => PantallaCompraHecha(tx: tx, lote: widget.p)));
    } on SinConexion {
      if (mounted) {
        setState(() {
          _firmada = op;
          _error = 'Sin internet: la compra NO se hizo. Cuando tengas señal pulsa "Reintentar" '
              '(es la misma compra ya confirmada: no se duplica).';
        });
      }
    } on ErrorApi catch (e) {
      if (mounted) {
        setState(() {
          _firmada = null;
          _exigeVerificacion = e.tipo == 'verification-required';
          _error = _exigeVerificacion ? 'Este lote exige verificación en campo antes de comprarlo.' : e.mensaje;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.p;
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    return Scaffold(
      appBar: AppBar(title: const Text('Compra directa')),
      body: Form(
        key: _form,
        autovalidateMode: AutovalidateMode.onUserInteraction,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
          children: [
            const Rotulo('Lote'),
            SeccionDatos(filas: [
              ('Lote', '${p['name']}'),
              ('Vendedor', nombreDe(p['seller'])),
              if (p['stage'] is Map) ('Estadío', nombreDe(p['stage'])),
              ('Disponible', cantidad(p['available_qty'], p['uom'])),
              ('Precio', precioPorUnidad(p['price'], p['uom'])),
            ]),
            if (soloLectura) ...[
              const SizedBox(height: 16),
              const AvisoSoloLectura(),
            ] else ...[
              const Rotulo('Cantidad'),
              TextFormField(
                controller: _qty,
                enabled: _firmada == null && !_ocupado,
                keyboardType: TextInputType.numberWithOptions(decimal: decimalesDeUnidad(p['uom']) > 0),
                validator: (v) => validarCantidadCompra(v, p),
                decoration: InputDecoration(
                  labelText: 'Cantidad (${unidadPlural(p['uom'])})',
                  helperText: 'Máximo ${cantidad(p['available_qty'], p['uom'])}',
                  prefixIcon: const Icon(Icons.scale_outlined),
                ),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _notas,
                enabled: _firmada == null && !_ocupado,
                maxLength: 500,
                maxLines: 2,
                decoration: const InputDecoration(labelText: 'Nota para el vendedor (opcional)'),
              ),
              if (_error != null) ...[const SizedBox(height: 12), CajaError(texto: _error!)],
              if (_exigeVerificacion) ...[
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: () => Navigator.pushReplacement(
                      context, MaterialPageRoute(builder: (_) => PantallaComprar(p: {...p, 'requires_verification': true}))),
                  icon: const Icon(Icons.verified_outlined),
                  label: const Text('Comprar con verificación'),
                ),
              ],
              const SizedBox(height: 20),
              FilledButton.icon(
                onPressed: _ocupado ? null : _comprar,
                icon: _ocupado
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : Icon(_firmada == null ? Icons.fingerprint : Icons.refresh),
                label: Text(_ocupado ? 'Comprando…' : (_firmada == null ? 'Comprar' : 'Reintentar')),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Confirmación de la compra directa: lo que devolvió el servidor.
class PantallaCompraHecha extends StatelessWidget {
  final Map<String, dynamic> tx;
  final Map<String, dynamic> lote;
  const PantallaCompraHecha({super.key, required this.tx, required this.lote});

  @override
  Widget build(BuildContext context) {
    final id = tx['id'] is String ? tx['id'] as String : null;
    return Scaffold(
      appBar: AppBar(title: const Text('Compra registrada')),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 24, 16, 32), children: [
        Icon(Icons.check_circle_outline, size: 64, color: context.colores.exitoTexto),
        const SizedBox(height: 12),
        Text('¡Compra registrada!', textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 16),
        SeccionDatos(filas: [
          if (tx['reference'] is String) ('Referencia', tx['reference'] as String) else if (tx['name'] is String) ('Referencia', tx['name'] as String),
          ('Lote', '${lote['name']}'),
          ('Cantidad', cantidad(tx['qty'] ?? tx['quantity'], lote['uom'])),
          if (tx['amount_total'] != null) ('Total', dinero(tx['amount_total'])) else if (tx['total'] != null) ('Total', dinero(tx['total'])),
          ('Estado', estadoTransaccion(tx['state'] as String?)),
        ]),
        const SizedBox(height: 20),
        if (id != null)
          FilledButton.icon(
            onPressed: () => Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => PantallaTransaccion(id: id))),
            icon: const Icon(Icons.receipt_long_outlined),
            label: const Text('Ver la compra'),
          ),
        const SizedBox(height: 8),
        TextButton(onPressed: () => Navigator.popUntil(context, (r) => r.isFirst), child: const Text('Volver al inicio')),
      ]),
    );
  }
}
