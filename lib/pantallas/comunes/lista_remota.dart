import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/control_legal.dart';
import '../../datos/repositorio.dart';
import '../../tema.dart';
import 'widgets.dart';

/// Lista de la API con paginación por cursor, deslizar para actualizar y la
/// última respuesta guardada cuando no hay señal.
/// Recarga la pantalla cuando se quita el bloqueo de documentos legales: lo
/// que se pidió mientras tanto llegó vacío o con error (403
/// legal-acceptance-required). Sin [ControlLegal] (pruebas) no hace nada.
mixin RecargaTrasDocumentos<W extends StatefulWidget> on State<W> {
  ControlLegal? _legal;
  bool _legalPendiente = false;

  Future<void> recargarTrasDocumentos();

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    ControlLegal? cl;
    try {
      cl = context.read<ControlLegal>();
    } on ProviderNotFoundException {
      cl = null;
    }
    if (identical(cl, _legal)) return;
    _legal?.removeListener(_alCambiarLegal);
    _legal = cl;
    _legalPendiente = cl?.pendiente ?? false;
    cl?.addListener(_alCambiarLegal);
  }

  void _alCambiarLegal() {
    final ahora = _legal?.pendiente ?? false;
    final antes = _legalPendiente;
    _legalPendiente = ahora;
    if (antes && !ahora && mounted) recargarTrasDocumentos();
  }

  @override
  void dispose() {
    _legal?.removeListener(_alCambiarLegal);
    super.dispose();
  }
}

class ListaRemota extends StatefulWidget {
  final String ruta;
  final Map<String, String>? params;
  final Widget Function(BuildContext, Map<String, dynamic>) tarjeta;

  /// Filtro local sobre lo bajado (p. ej. solo las que esperan mi decisión).
  final bool Function(Map<String, dynamic>)? filtro;
  final String vacio;
  final Widget? cabecera;
  const ListaRemota({
    super.key,
    required this.ruta,
    this.params,
    required this.tarjeta,
    this.filtro,
    this.vacio = 'No hay nada en esta lista.',
    this.cabecera,
  });

  @override
  State<ListaRemota> createState() => ListaRemotaState();
}

class ListaRemotaState extends State<ListaRemota> with RecargaTrasDocumentos<ListaRemota> {
  @override
  Future<void> recargarTrasDocumentos() => recargar();

  List<Map<String, dynamic>> _datos = [];
  String? _cursor;
  bool _cargando = true;
  bool _cargandoMas = false;
  bool _desdeCache = false;
  DateTime? _guardadoEn;
  String? _error;

  /// Sube con cada recarga: una respuesta de una petición anterior (otro
  /// filtro) que llega tarde se ignora.
  int _generacion = 0;

  @override
  void initState() {
    super.initState();
    recargar();
  }

  @override
  void didUpdateWidget(covariant ListaRemota oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.ruta != widget.ruta || Repositorio.claveDe('', oldWidget.params) != Repositorio.claveDe('', widget.params)) {
      recargar();
    }
  }

  Future<void> recargar() async {
    final g = ++_generacion;
    setState(() {
      _cargando = true;
      _error = null;
    });
    try {
      final r = await context.read<Repositorio>().pagina(widget.ruta, params: widget.params, limite: 100);
      if (!mounted || g != _generacion) return;
      setState(() {
        _datos = r.datos;
        _cursor = r.siguienteCursor;
        _desdeCache = r.desdeCache;
        _guardadoEn = r.guardadoEn;
      });
    } catch (e) {
      if (!mounted || g != _generacion) return;
      setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted && g == _generacion) setState(() => _cargando = false);
    }
  }

  Future<void> _cargarMas() async {
    final cursor = _cursor;
    if (cursor == null || _cargandoMas) return;
    final g = _generacion;
    setState(() => _cargandoMas = true);
    try {
      final r = await context.read<Repositorio>().pagina(widget.ruta, params: widget.params, cursor: cursor, limite: 100);
      if (!mounted || g != _generacion) return;
      setState(() {
        _datos = [..._datos, ...r.datos];
        _cursor = r.siguienteCursor;
      });
    } catch (e) {
      if (!mounted || g != _generacion) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(mensajeDeError(e))));
    } finally {
      if (mounted && g == _generacion) setState(() => _cargandoMas = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final lista = widget.filtro == null ? _datos : _datos.where(widget.filtro!).toList();
    Widget cuerpo;
    if (_cargando && _datos.isEmpty) {
      cuerpo = const Center(child: CircularProgressIndicator());
    } else if (_error != null && _datos.isEmpty) {
      cuerpo = ListView(padding: const EdgeInsets.all(16), children: [CajaError(texto: _error!, reintentar: recargar)]);
    } else if (lista.isEmpty) {
      cuerpo = VacioLista(texto: '${widget.vacio}\nDesliza hacia abajo para actualizar.');
    } else {
      cuerpo = ListView.separated(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        itemCount: lista.length + (_cursor != null ? 1 : 0),
        separatorBuilder: (_, _) => const SizedBox(height: 10),
        itemBuilder: (c, i) {
          if (i == lista.length) {
            return Center(
              child: _cargandoMas
                  ? const Padding(padding: EdgeInsets.all(12), child: CircularProgressIndicator())
                  : TextButton(onPressed: _cargarMas, child: const Text('Cargar más')),
            );
          }
          return widget.tarjeta(c, lista[i]);
        },
      );
    }
    return Column(children: [
      if (_desdeCache) AvisoCache(guardadoEn: _guardadoEn),
      if (_error != null && _datos.isNotEmpty)
        Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 0), child: CajaError(texto: _error!, reintentar: recargar)),
      ?widget.cabecera,
      Expanded(child: RefreshIndicator(color: Marca.brand, onRefresh: recargar, child: cuerpo)),
    ]);
  }
}

/// Carga un detalle o varias listas para un panel y muestra carga / error /
/// datos de caché de forma uniforme.
class CargaRemota<T> extends StatefulWidget {
  final Future<T> Function(Repositorio) cargar;
  final Widget Function(BuildContext, T, bool desdeCache, DateTime? guardadoEn, Future<void> Function() recargar) hijo;

  /// Dentro de un ListView (sin alto fijo): no usa Expanded.
  final bool compacto;
  const CargaRemota({super.key, required this.cargar, required this.hijo, this.compacto = false});

  @override
  State<CargaRemota<T>> createState() => _CargaRemotaState<T>();
}

class _CargaRemotaState<T> extends State<CargaRemota<T>> with RecargaTrasDocumentos<CargaRemota<T>> {
  @override
  Future<void> recargarTrasDocumentos() => _recargar();

  T? _datos;
  bool _cargando = true;
  String? _error;
  bool _desdeCache = false;
  DateTime? _guardadoEn;

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
    try {
      final r = await widget.cargar(context.read<Repositorio>());
      if (!mounted) return;
      setState(() {
        _datos = r;
        if (r is ResultadoLista) {
          _desdeCache = r.desdeCache;
          _guardadoEn = r.guardadoEn;
        } else if (r is List<ResultadoLista>) {
          _desdeCache = r.any((x) => x.desdeCache);
          _guardadoEn = r.map((x) => x.guardadoEn).whereType<DateTime>().fold<DateTime?>(null, (a, b) => a == null || b.isBefore(a) ? b : a);
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final datos = _datos;
    if (_cargando && datos == null) {
      return const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator()));
    }
    if (datos == null) {
      final caja = CajaError(texto: _error ?? 'Sin datos', reintentar: _recargar);
      return widget.compacto ? caja : ListView(padding: const EdgeInsets.all(16), children: [caja]);
    }
    final contenido = widget.hijo(context, datos, _desdeCache, _guardadoEn, _recargar);
    return Column(mainAxisSize: widget.compacto ? MainAxisSize.min : MainAxisSize.max, children: [
      if (_desdeCache) AvisoCache(guardadoEn: _guardadoEn),
      if (_error != null) Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 0), child: CajaError(texto: _error!, reintentar: _recargar)),
      if (widget.compacto) contenido else Expanded(child: contenido),
    ]);
  }
}
