import 'package:flutter/material.dart';
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

/// Qué venció, con las palabras del portal («Plazos de la verificadora»).
String textoVerificadoraVencida(String? vencido, {String? motivo}) => switch (vencido) {
      'assign' => 'La verificadora no asignó un técnico a tiempo.',
      'declined' => motivo == null ? 'La verificadora declinó la orden.' : 'La verificadora declinó la orden: «$motivo».',
      'response' => 'La verificadora no aceptó ni declinó la orden a tiempo.',
      _ => 'La verificadora no emitió el veredicto a tiempo.',
    };

/// La verificadora declinó la orden o no cumplió su plazo: quien eligió la
/// verificadora (el comprador; en una segunda opinión, el vendedor) cambia
/// a otra acreditada, y el comprador puede cancelar la compra sin costo.
/// Lo mismo que «Plazos de la verificadora» en la trazabilidad del portal.
/// `GET /transactions/{id}/verifier-options` → `POST :switch-verifier` o
/// `POST :cancel` (las dos firmadas). Devuelve `true` si algo llegó al
/// servidor.
class PantallaVerificadoraVencida extends StatelessWidget {
  final String transaccionId;
  const PantallaVerificadoraVencida({super.key, required this.transaccionId});

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Plazos de la verificadora')),
        body: CargaRemota<ResultadoLista>(
          cargar: (repo) => repo.detalle('/transactions/$transaccionId/verifier-options'),
          hijo: (c, r, desdeCache, guardadoEn, _) =>
              _Opciones(transaccionId: transaccionId, datos: r.datos.first, desdeCache: desdeCache, guardadoEn: guardadoEn),
        ),
      );
}

class _Opciones extends StatefulWidget {
  final String transaccionId;
  final Map<String, dynamic> datos;
  final bool desdeCache;
  final DateTime? guardadoEn;
  const _Opciones({required this.transaccionId, required this.datos, required this.desdeCache, this.guardadoEn});
  @override
  State<_Opciones> createState() => _OpcionesState();
}

class _OpcionesState extends State<_Opciones> {
  final _lista = GlobalKey();
  final _nota = TextEditingController();
  String? _elegida;
  bool _faltaElegir = false;

  @override
  void dispose() {
    _nota.dispose();
    super.dispose();
  }

  List<Map> get _opciones => (widget.datos['options'] is List ? widget.datos['options'] as List : const []).whereType<Map>().toList();
  String? _idDe(Map o) => o['verifier'] is Map ? o['verifier']['id'] as String? : null;
  String _nombreDe(Map o) => o['verifier'] is Map ? '${o['verifier']['name'] ?? ''}' : '';

  void _aviso(String texto, {bool error = false}) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(texto), backgroundColor: error ? context.colores.peligro : null));
  }

  Future<void> _cambiar() async {
    final elegida = _opciones.where((o) => _idDe(o) == _elegida).firstOrNull;
    if (elegida == null) {
      setState(() => _faltaElegir = true);
      _aviso('Elige la nueva verificadora.', error: true);
      final lista = _lista.currentContext;
      if (lista != null) await Scrollable.ensureVisible(lista, duration: const Duration(milliseconds: 350), alignment: 0.2);
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cambiar verificadora'),
        content: Text('La orden pasa a ${_nombreDe(elegida)}, con plazos desde ahora. El honorario se recalcula con su tarifa '
            '(${dinero(elegida['fee'])}): si cambia, se anula el cobro anterior y se emite uno por el importe nuevo.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cambiar verificadora')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final r = await context
        .read<Sincronizador>()
        .ejecutar(TipoOperacion.cambiarVerificadora, widget.transaccionId, cuerpoCambiarVerificadora(_elegida!));
    if (!mounted) return;
    avisarResultado(context, r, hecho: 'Listo: la orden pasó a la nueva verificadora, con plazos desde ahora.');
    if (r.error == null) Navigator.pop(context, r.enviado);
  }

  Future<void> _cancelar() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cancelar compra sin costo'),
        content: const Text('La compra y la verificación quedan canceladas. No se puede deshacer.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Confirmar cancelación')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.cancelarCompra, widget.transaccionId, cuerpoCancelarCompra(_nota.text));
    if (!mounted) return;
    avisarResultado(context, r, hecho: 'Compra cancelada sin costo.');
    if (r.error == null) Navigator.pop(context, r.enviado);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    final d = widget.datos;
    final vencido = d['overdue'] as String?;
    final puedeCambiar = d['can_switch'] == true;
    final puedeCancelar = d['can_cancel'] == true;
    final auto = aFecha(d['auto_cancel_at']);
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: [
        if (widget.desdeCache) ...[AvisoCache(guardadoEn: widget.guardadoEn), const SizedBox(height: 10)],
        SeccionDatos(filas: [
          ('Verificadora', d['verifier'] is Map ? '${d['verifier']['name'] ?? ''}' : '—'),
          ('Estado', vencido == null ? 'En plazo' : 'Plazo vencido'),
          if (auto != null) ('Se cancela sola el', fechaHoraDe(auto)),
        ]),
        const SizedBox(height: 12),
        if (vencido == null)
          const CajaAviso(
            tipo: TipoAviso.info,
            icono: Icons.schedule_rounded,
            texto: 'Si la verificadora no cumple estos plazos, la plataforma recibe un aviso y el comprador puede cancelar sin costo '
                'o cambiar de verificadora.',
          )
        else if (!puedeCambiar && !puedeCancelar)
          CajaAviso(
            tipo: TipoAviso.advertencia,
            icono: Icons.schedule_rounded,
            texto: '${vencido == 'declined' ? 'La verificadora declinó la orden.' : 'La verificadora no cumplió su plazo.'} '
                'El comprador puede cancelar sin costo o cambiar de verificadora; no es un incumplimiento del vendedor.',
          )
        else
          CajaAviso(
            tipo: TipoAviso.peligro,
            icono: Icons.lock_open_rounded,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(textoVerificadoraVencida(vencido, motivo: d['decline_reason'] as String?),
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              const Text('Puedes cambiar a otra verificadora acreditada (el honorario se recalcula con su tarifa; plazos desde '
                  'ahora) o cancelar la compra sin costo.'),
            ]),
          ),
        if (soloLectura && (puedeCambiar || puedeCancelar)) ...[
          const SizedBox(height: 16),
          const AvisoSoloLectura(),
        ] else ...[
          if (puedeCambiar) ...[
            Rotulo('Cambiar de verificadora', key: _lista),
            if (_opciones.isEmpty)
              Tarjeta(child: Text('No hay otra verificadora acreditada disponible.', style: TextStyle(color: c.tintaSuave)))
            else
              Tarjeta(
                padding: EdgeInsets.zero,
                colorBorde: _faltaElegir ? c.peligro : null,
                child: RadioGroup<String>(
                  groupValue: _elegida,
                  onChanged: (e) => setState(() {
                    _elegida = e;
                    _faltaElegir = false;
                  }),
                  child: Column(children: [
                    for (final o in _opciones)
                      if (_idDe(o) != null)
                        RadioListTile<String>(
                          value: _idDe(o)!,
                          title: Text(_nombreDe(o), style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: Text('Honorario ${dinero(o['fee'])}'),
                        ),
                  ]),
                ),
              ),
            if (_faltaElegir) ...[
              const SizedBox(height: 6),
              Text('Elige una verificadora', style: TextStyle(fontSize: 12.5, color: c.peligroTexto)),
            ],
            const SizedBox(height: 10),
            Text(
                'El honorario se recalcula con la tarifa de la nueva verificadora (hoy pagaste ${dinero(d['current_fee'])}): '
                'si cambia, se anula el cobro anterior y se emite uno por el importe nuevo.',
                style: TextStyle(fontSize: 13, color: c.tintaSuave)),
            const SizedBox(height: 14),
            BotonAccion(
              onPressed: _opciones.isEmpty ? null : _cambiar,
              icono: Icons.swap_horiz_rounded,
              texto: 'Cambiar verificadora',
              textoOcupado: 'Enviando…',
            ),
          ],
          if (puedeCancelar) ...[
            const Rotulo('Cancelar compra sin costo'),
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                for (final efecto in const [
                  'La compra y la verificación quedan canceladas.',
                  'Se anula el honorario de verificación (nota de crédito si ya estaba facturado).',
                  'Se libera el stock reservado del vendedor; el vendedor no recibe strike.',
                ])
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Text('• $efecto', style: TextStyle(color: c.tinta)),
                  ),
              ]),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _nota,
              maxLines: 2,
              maxLength: 2000,
              decoration: const InputDecoration(labelText: 'Nota (opcional)'),
            ),
            const SizedBox(height: 8),
            BotonAccion(
              estilo: EstilosBoton.peligroRelleno(context),
              onPressed: _cancelar,
              icono: Icons.block_rounded,
              texto: 'Confirmar cancelación',
              textoOcupado: 'Enviando…',
            ),
          ],
        ],
      ],
    );
  }
}
