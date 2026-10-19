import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/avisos.dart';
import '../../modelos/aviso.dart';
import '../../navegacion/destinos.dart';
import '../../navegacion/enrutador.dart';
import '../../tema.dart';
import 'formato.dart';
import 'widgets.dart';

IconData iconoDeAviso(Aviso a) {
  final t = a.tipo;
  if (t.contains('assign')) return Icons.engineering_outlined;
  if (t.contains('report') || t.contains('accept') || t.contains('deadline')) return Icons.how_to_vote_outlined;
  if (t.contains('dispatch') || t.contains('eta') || t.contains('truck') || t.contains('arrival')) return Icons.local_shipping_outlined;
  if (t.contains('copack') || t.contains('packing') || t.contains('offer')) return Icons.inventory_2_outlined;
  if (t.contains('purchase') || t.contains('transaction') || t.contains('sale')) return Icons.shopping_bag_outlined;
  return Icons.notifications_outlined;
}

/// Bandeja de avisos (C3): lista paginada (más nuevos primero), un toque
/// abre el destino y lo marca como leído; "Marcar todo como leído" arriba.
/// En modo supervisor se ve, pero no se marca nada.
class PantallaAvisos extends StatefulWidget {
  const PantallaAvisos({super.key});
  @override
  State<PantallaAvisos> createState() => _PantallaAvisosState();
}

class _PantallaAvisosState extends State<PantallaAvisos> {
  final _avisos = <Aviso>[];
  String? _cursor;
  bool _cargando = true;
  bool _cargandoMas = false;
  String? _error;
  bool _soloNoLeidos = false;

  @override
  void initState() {
    super.initState();
    _recargar();
  }

  Future<void> _recargar() async {
    setState(() {
      _cargando = true;
      _error = null;
    });
    final servicio = context.read<ServicioAvisos>();
    try {
      final p = await servicio.pagina(soloNoLeidos: _soloNoLeidos);
      if (!mounted) return;
      setState(() {
        _avisos
          ..clear()
          ..addAll(p.avisos);
        _cursor = p.siguienteCursor;
      });
      // El contador de la campana, al día.
      await servicio.refrescar();
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  Future<void> _cargarMas() async {
    final cursor = _cursor;
    if (cursor == null || _cargandoMas) return;
    setState(() => _cargandoMas = true);
    try {
      final p = await context.read<ServicioAvisos>().pagina(cursor: cursor, soloNoLeidos: _soloNoLeidos);
      if (!mounted) return;
      setState(() {
        _avisos.addAll(p.avisos);
        _cursor = p.siguienteCursor;
      });
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(mensajeDeError(e))));
    } finally {
      if (mounted) setState(() => _cargandoMas = false);
    }
  }

  Future<void> _abrir(Aviso a) async {
    final servicio = context.read<ServicioAvisos>();
    final enrutador = context.read<Enrutador>();
    if (!a.leido && !servicio.soloLectura) {
      try {
        if (await servicio.marcarLeido(a)) {
          final i = _avisos.indexWhere((x) => x.id == a.id);
          if (i >= 0 && mounted) setState(() => _avisos[i] = a.comoLeido());
        }
      } catch (_) {
        // Sin señal: se abre igual; se marcará la próxima vez.
      }
    }
    final d = destinoDeAviso(a);
    if (d == null || d.tipo == TipoDestino.avisos) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Este aviso no tiene una pantalla en la app.')));
      }
      return;
    }
    if (!mounted) return;
    await Navigator.push(context, MaterialPageRoute(builder: (_) => pantallaDe(d, perfil: enrutador.sesion.perfil)));
  }

  Future<void> _marcarTodo() async {
    try {
      await context.read<ServicioAvisos>().marcarTodo();
      if (!mounted) return;
      setState(() {
        for (var i = 0; i < _avisos.length; i++) {
          _avisos[i] = _avisos[i].comoLeido();
        }
        if (_soloNoLeidos) _avisos.clear();
      });
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(mensajeDeError(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final servicio = context.watch<ServicioAvisos>();
    final soloLectura = servicio.soloLectura;
    Widget cuerpo;
    if (_cargando && _avisos.isEmpty) {
      cuerpo = const Center(child: CircularProgressIndicator());
    } else if (_error != null && _avisos.isEmpty) {
      cuerpo = ListView(padding: const EdgeInsets.all(16), children: [CajaError(texto: _error!, reintentar: _recargar)]);
    } else if (_avisos.isEmpty) {
      cuerpo = VacioLista(
          texto: _soloNoLeidos ? 'No tienes avisos sin leer.' : 'Todavía no tienes avisos.', icono: Icons.notifications_none);
    } else {
      cuerpo = ListView.separated(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        itemCount: _avisos.length + (_cursor != null ? 1 : 0),
        separatorBuilder: (_, _) => const SizedBox(height: 8),
        itemBuilder: (c, i) {
          if (i == _avisos.length) {
            return Center(
              child: _cargandoMas
                  ? const Padding(padding: EdgeInsets.all(12), child: CircularProgressIndicator())
                  : TextButton(onPressed: _cargarMas, child: const Text('Cargar más')),
            );
          }
          return _FilaAviso(aviso: _avisos[i], onTap: () => _abrir(_avisos[i]));
        },
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('Avisos'),
        actions: [
          if (!soloLectura)
            IconButton(
              tooltip: 'Marcar todo como leído',
              onPressed: servicio.noLeidos == 0 && !_avisos.any((a) => !a.leido) ? null : _marcarTodo,
              icon: const Icon(Icons.done_all),
            ),
        ],
      ),
      body: Column(children: [
        if (soloLectura)
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 10, 16, 0),
            child: AvisoSoloLectura(),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
          child: SizedBox(
            width: double.infinity,
            child: SegmentedButton<bool>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: false, label: Text('Todos')),
                ButtonSegment(value: true, label: Text('Sin leer')),
              ],
              selected: {_soloNoLeidos},
              onSelectionChanged: (s) {
                setState(() => _soloNoLeidos = s.first);
                _recargar();
              },
            ),
          ),
        ),
        Expanded(child: RefreshIndicator(color: context.colores.marcaTexto, onRefresh: _recargar, child: cuerpo)),
      ]),
    );
  }
}

class _FilaAviso extends StatelessWidget {
  final Aviso aviso;
  final VoidCallback onTap;
  const _FilaAviso({required this.aviso, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final a = aviso;
    return Tarjeta(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      onTap: onTap,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          width: 38,
          height: 38,
          decoration: BoxDecoration(color: a.leido ? context.colores.fondoAlt : context.colores.marcaSuave, borderRadius: BorderRadius.circular(11)),
          child: Icon(iconoDeAviso(a), color: a.leido ? context.colores.tintaTenue : context.colores.marcaTexto, size: 20),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(a.titulo,
                style: TextStyle(
                    fontSize: 15, fontWeight: a.leido ? FontWeight.w600 : FontWeight.w800, color: context.colores.titulo)),
            if (a.cuerpo.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(a.cuerpo, style: TextStyle(fontSize: 13.5, color: context.colores.tintaSuave)),
              ),
            if (a.creadoEn != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(fechaHoraDe(a.creadoEn!), style: TextStyle(fontSize: 12, color: context.colores.tintaTenue)),
              ),
          ]),
        ),
        if (!a.leido)
          Container(
            margin: const EdgeInsets.only(left: 8, top: 6),
            width: 9,
            height: 9,
            decoration: BoxDecoration(color: context.colores.acentoTexto, shape: BoxShape.circle),
          ),
      ]),
    );
  }
}

/// Campana de la barra de cuenta con el contador de no leídos.
class CampanaAvisos extends StatelessWidget {
  const CampanaAvisos({super.key});

  @override
  Widget build(BuildContext context) {
    final servicio = context.watch<ServicioAvisos>();
    return IconButton(
      tooltip: 'Avisos',
      onPressed: () => Navigator.push(
          context, MaterialPageRoute(settings: const RouteSettings(name: 'avisos'), builder: (_) => const PantallaAvisos())),
      icon: Badge(
        isLabelVisible: servicio.noLeidos > 0,
        backgroundColor: context.colores.peligro,
        label: Text(servicio.textoContador),
        child: Icon(servicio.noLeidos > 0 ? Icons.notifications : Icons.notifications_none),
      ),
    );
  }
}
