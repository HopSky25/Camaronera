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

/// El vendedor pide la segunda opinión sobre un rechazo por el informe:
/// elige otra verificadora acreditada (con su honorario) dentro del plazo.
/// `GET /verifications/{id}/second-opinion` → `POST /verifications/{id}:second-opinion`.
/// Devuelve `true` (al cerrar) si el pedido llegó al servidor.
class PantallaSegundaOpinion extends StatelessWidget {
  final String id;
  const PantallaSegundaOpinion({super.key, required this.id});

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Pedir segunda opinión')),
        body: CargaRemota<ResultadoLista>(
          cargar: (repo) => repo.detalle('/verifications/$id/second-opinion'),
          hijo: (c, r, desdeCache, guardadoEn, _) => _Eleccion(
            id: id,
            datos: r.datos.first,
            desdeCache: desdeCache,
            guardadoEn: guardadoEn,
          ),
        ),
      );
}

class _Eleccion extends StatefulWidget {
  final String id;
  final Map<String, dynamic> datos;
  final bool desdeCache;
  final DateTime? guardadoEn;
  const _Eleccion({required this.id, required this.datos, required this.desdeCache, this.guardadoEn});
  @override
  State<_Eleccion> createState() => _EleccionState();
}

class _EleccionState extends State<_Eleccion> {
  final _lista = GlobalKey();
  String? _elegida;
  bool _faltaElegir = false;

  List<Map> get _opciones => (widget.datos['options'] is List ? widget.datos['options'] as List : const []).whereType<Map>().toList();

  String? _idDe(Map o) => o['verifier'] is Map ? o['verifier']['id'] as String? : null;
  String _nombreDe(Map o) => o['verifier'] is Map ? '${o['verifier']['name'] ?? ''}' : '';

  Future<void> _pedir() async {
    final elegida = _opciones.where((o) => _idDe(o) == _elegida).firstOrNull;
    if (elegida == null) {
      setState(() => _faltaElegir = true);
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: const Text('Elige la verificadora de la segunda opinión.'), backgroundColor: context.colores.peligro));
      final lista = _lista.currentContext;
      if (lista != null) await Scrollable.ensureVisible(lista, duration: const Duration(milliseconds: 350), alignment: 0.2);
      return;
    }
    final honorario = dinero(elegida['fee']);
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Pedir segunda opinión'),
        content: Text('${_nombreDe(elegida)} vuelve a verificar tu producto. Su honorario ($honorario) lo pagas tú, '
            'cualquiera sea el resultado. Si aprueba, la compra sigue; si rechaza, el rechazo se mantiene.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Pedir')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final r = await context.read<Sincronizador>().ejecutar(TipoOperacion.segundaOpinion, widget.id, cuerpoSegundaOpinion(_elegida!));
    if (!mounted) return;
    avisarResultado(context, r, hecho: 'Listo: pediste la segunda opinión. La nueva verificadora ya recibió la orden.');
    if (r.error == null) Navigator.pop(context, r.enviado);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    final d = widget.datos;
    final puede = d['can_request'] == true;
    final plazo = aFecha(d['contest_deadline']);
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: [
        if (widget.desdeCache) ...[AvisoCache(guardadoEn: widget.guardadoEn), const SizedBox(height: 10)],
        if (!puede)
          CajaAviso(
            tipo: TipoAviso.advertencia,
            icono: Icons.info_outline,
            texto: (d['problem'] as String?) ?? 'Este rechazo ya no admite segunda opinión.',
          )
        else ...[
          CajaAviso(
            tipo: TipoAviso.info,
            icono: Icons.balance_rounded,
            texto: 'Otra verificadora acreditada vuelve a verificar tu producto${plazo == null ? '' : '. Puedes pedirla hasta el ${fechaHoraDe(plazo)}'}. '
                'Si no la pides, el rechazo queda firme, la compra se cancela y el honorario de la verificación lo pagas tú.',
          ),
          Rotulo('Verificadora de la segunda opinión', key: _lista),
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
          Text('El honorario de la segunda opinión lo pagas tú, con la tarifa de la verificadora que elijas, cualquiera sea el resultado.',
              style: TextStyle(fontSize: 13, color: c.tintaSuave)),
          const SizedBox(height: 20),
          if (soloLectura)
            const AvisoSoloLectura()
          else
            BotonAccion(
              onPressed: _opciones.isEmpty ? null : _pedir,
              icono: Icons.balance_rounded,
              texto: 'Pedir segunda opinión',
              textoOcupado: 'Enviando…',
            ),
        ],
      ],
    );
  }
}
