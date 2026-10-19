import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';

import '../../datos/api_cliente.dart';
import '../../datos/cuenta_vendedor.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/gestion_lote.dart';
import '../../modelos/instalaciones.dart';
import '../../modelos/lote_nuevo.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/widgets.dart';
import 'formulario.dart';
import 'pantalla_fotos_lote.dart';

String _mensaje(Object e) => switch (e) {
      SinConexion() => 'Sin internet: no se guardó. Inténtalo cuando tengas señal.',
      final ErrorApi err => mensajeLote(err),
      _ => mensajeDeError(e),
    };

String _capacidad(Map<String, dynamic> p) {
  final area = p['area_m2'], vol = p['volume_m3'];
  if (p['capacity_mode'] == 'volume' || (area is! num || area == 0)) {
    return vol is num && vol > 0 ? '${numero(vol)} m³' : 'Sin capacidad indicada';
  }
  return '${numero(area)} m²${vol is num && vol > 0 ? ' · ${numero(vol)} m³' : ''}';
}

/// Instalaciones del vendedor (fincas, laboratorios, centros de maduración,
/// bodegas…) con sus piscinas o tanques: listar, crear, editar y archivar,
/// como «Instalaciones» en Mi cuenta del portal. No se firma: son datos de
/// la cuenta, no lo que se vende (el origen de un lote con compras ya queda
/// bloqueado en el lote).
class PantallaInstalaciones extends StatefulWidget {
  const PantallaInstalaciones({super.key});
  @override
  State<PantallaInstalaciones> createState() => _PantallaInstalacionesState();
}

class _PantallaInstalacionesState extends State<PantallaInstalaciones> {
  List<Map<String, dynamic>>? _instalaciones;
  List<Map<String, dynamic>> _piscinas = const [];
  String? _error;
  bool _archivadas = false;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    setState(() => _error = null);
    final sinc = context.read<Sincronizador>();
    try {
      final r = await Future.wait([sinc.instalaciones(archivadas: _archivadas), sinc.piscinas(archivadas: _archivadas)]);
      if (mounted) {
        setState(() {
          _instalaciones = r[0];
          _piscinas = r[1];
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = _mensaje(e));
    }
  }

  Future<void> _nueva() async {
    final r = await Navigator.push<Map<String, dynamic>>(context, MaterialPageRoute(builder: (_) => const PantallaFormInstalacion()));
    if (r != null && mounted) {
      avisar(context, 'Instalación creada.');
      await _cargar();
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final v = vocabularioDe(sesion.perfil);
    final c = context.colores;
    final lista = _instalaciones;
    return Scaffold(
      appBar: AppBar(title: Text('${v.instalaciones} y ${v.piscinas.toLowerCase()}')),
      floatingActionButton: sesion.soloLectura || lista == null
          ? null
          : FloatingActionButton.extended(
              onPressed: _nueva,
              icon: const Icon(Icons.add),
              label: Text('Agregar ${v.instalacion}'),
            ),
      body: lista == null && _error == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              color: c.marcaTexto,
              onRefresh: _cargar,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 96),
                children: [
                  if (_error != null) ...[const SizedBox(height: 12), CajaError(texto: _error!, reintentar: _cargar)],
                  if (sesion.soloLectura) ...[const SizedBox(height: 12), const AvisoSoloLectura()],
                  const SizedBox(height: 8),
                  Text('Son el origen que eliges al publicar un lote. Archivar conserva el historial (siembras y origen de los lotes).',
                      style: TextStyle(color: c.tintaSuave)),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Ver también las archivadas'),
                    value: _archivadas,
                    onChanged: (x) {
                      setState(() => _archivadas = x);
                      _cargar();
                    },
                  ),
                  if (lista != null && lista.isEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 16),
                      child: Text('Todavía no registras ${v.instalaciones.toLowerCase()}.', style: TextStyle(color: c.tintaSuave)),
                    ),
                  for (final f in lista ?? const <Map<String, dynamic>>[])
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: TarjetaRegistro(
                        titulo: '${f['name'] ?? ''}',
                        subtitulo: [
                          tiposInstalacion[f['facility_type']] ?? '',
                          if (f['city'] is String) f['city'] as String,
                          if (f['province'] is String) f['province'] as String,
                        ].where((t) => t.isNotEmpty).join(' · '),
                        estado: f['active'] == false ? 'archived' : 'published',
                        estadoTexto: f['active'] == false ? 'Archivada' : 'Activa',
                        datos: [
                          Dato(
                            icono: Icons.water_outlined,
                            texto: '${_piscinas.where((p) => idDe(p['facility']) == f['id']).length} ${v.piscinas.toLowerCase()}',
                          ),
                        ],
                        onTap: () async {
                          await Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInstalacion(instalacion: f)));
                          if (mounted) await _cargar();
                        },
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}

/// Una instalación con sus piscinas o tanques.
class PantallaInstalacion extends StatefulWidget {
  final Map<String, dynamic> instalacion;
  const PantallaInstalacion({super.key, required this.instalacion});
  @override
  State<PantallaInstalacion> createState() => _PantallaInstalacionState();
}

class _PantallaInstalacionState extends State<PantallaInstalacion> {
  late Map<String, dynamic> _f = widget.instalacion;
  List<Map<String, dynamic>>? _piscinas;
  String? _error;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    setState(() => _error = null);
    try {
      final todas = await context.read<Sincronizador>().piscinas(archivadas: _f['active'] == false);
      if (mounted) setState(() => _piscinas = todas.where((p) => idDe(p['facility']) == _f['id']).toList());
    } catch (e) {
      if (mounted) setState(() => _error = _mensaje(e));
    }
  }

  Future<void> _editar() async {
    final r = await Navigator.push<Map<String, dynamic>>(context, MaterialPageRoute(builder: (_) => PantallaFormInstalacion(inicial: _f)));
    if (r != null && mounted) {
      setState(() => _f = r);
      avisar(context, 'Cambios guardados.');
    }
  }

  Future<void> _piscina(Vocabulario v, {Map<String, dynamic>? inicial}) async {
    final r = await Navigator.push<Map<String, dynamic>>(
        context, MaterialPageRoute(builder: (_) => PantallaFormPiscina(instalacionId: '${_f['id']}', inicial: inicial)));
    if (r != null && mounted) {
      avisar(context, r['active'] == false ? '${_mayuscula(v.piscina)} archivado.' : 'Cambios guardados.');
      await _cargar();
    }
  }

  Future<void> _archivar(Vocabulario v) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Archivar ${v.instalacion}'),
        content: Text('«${_f['name']}» y sus ${v.piscinas.toLowerCase()} dejan de ofrecerse como origen de un lote. '
            'Siguen en el historial (siembras y origen de los lotes que ya publicaste).'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Archivar')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      final r = await context.read<Sincronizador>().archivarInstalacion('${_f['id']}');
      if (!mounted) return;
      avisar(context, '${_mayuscula(v.instalacion)} archivada.');
      setState(() => _f = r);
      await _cargar();
    } catch (e) {
      if (mounted) avisar(context, _mensaje(e), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final v = vocabularioDe(sesion.perfil);
    final c = context.colores;
    final f = _f;
    final activa = f['active'] != false;
    final editable = !sesion.soloLectura && activa;
    return Scaffold(
      appBar: AppBar(title: Text('${f['name'] ?? ''}')),
      body: RefreshIndicator(
        color: c.marcaTexto,
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: [
            if (!activa) const CajaAviso(tipo: TipoAviso.advertencia, icono: Icons.archive_outlined, texto: 'Archivada: ya no se ofrece como origen de un lote.'),
            const Rotulo('Datos'),
            SeccionDatos(filas: [
              ('Tipo', tiposInstalacion[f['facility_type']] ?? '—'),
              if (f['code'] is String) ('Código', '${f['code']}'),
              if (f['city'] is String) ('Ciudad', '${f['city']}'),
              if (f['province'] is String) ('Provincia', '${f['province']}'),
              if (f['address'] is String) ('Dirección', '${f['address']}'),
              if (f['notes'] is String) ('Notas', '${f['notes']}'),
            ]),
            if (editable) ...[
              const SizedBox(height: 12),
              Wrap(spacing: 8, runSpacing: 8, children: [
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
                  onPressed: _editar,
                  icon: const Icon(Icons.edit_outlined),
                  label: const Text('Editar'),
                ),
                OutlinedButton.icon(
                  style: EstilosBoton.peligro(context).merge(OutlinedButton.styleFrom(minimumSize: const Size(0, 48))),
                  onPressed: () => _archivar(v),
                  icon: const Icon(Icons.archive_outlined),
                  label: const Text('Archivar'),
                ),
              ]),
            ],
            Rotulo(v.piscinas),
            if (_error != null) CajaError(texto: _error!, reintentar: _cargar),
            if (_piscinas == null && _error == null) const Center(child: CircularProgressIndicator()),
            if (_piscinas != null && _piscinas!.isEmpty)
              Text('Todavía no tiene ${v.piscinas.toLowerCase()}.', style: TextStyle(color: c.tintaSuave)),
            for (final p in _piscinas ?? const <Map<String, dynamic>>[])
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: TarjetaRegistro(
                  titulo: '${p['name'] ?? ''}',
                  subtitulo: [tiposPiscina[p['pond_type']] ?? '', if (p['code'] is String) '${p['code']}'].where((t) => t.isNotEmpty).join(' · '),
                  estado: p['active'] == false ? 'archived' : 'published',
                  estadoTexto: p['active'] == false ? 'Archivado' : 'Activo',
                  datos: [
                    Dato(icono: Icons.straighten, texto: _capacidad(p)),
                    if (p['has_image'] == true) const Dato(icono: Icons.photo_outlined, texto: 'Con foto'),
                  ],
                  onTap: editable && p['active'] != false ? () => _piscina(v, inicial: p) : null,
                ),
              ),
            if (editable) ...[
              const SizedBox(height: 4),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: () => _piscina(v),
                  icon: const Icon(Icons.add),
                  label: Text('Agregar ${v.piscina}'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

String _mayuscula(String s) => s.isEmpty ? s : '${s[0].toUpperCase()}${s.substring(1)}';

/// Selector de un valor de una lista (tipo de instalación o de piscina),
/// con los naturales del perfil primero.
Widget _tipo(String etiqueta, Map<String, String> tipos, List<String> orden, String valor, String? Function() error,
        ValueChanged<String> alCambiar) =>
    DropdownButtonFormField<String>(
      initialValue: tipos.containsKey(valor) ? valor : null,
      isExpanded: true,
      validator: (_) => error(),
      decoration: InputDecoration(labelText: etiqueta),
      items: [for (final k in [...orden, ...tipos.keys.where((k) => !orden.contains(k))]) DropdownMenuItem(value: k, child: Text(tipos[k]!))],
      onChanged: (x) {
        if (x != null) alCambiar(x);
      },
    );

/// Crear o editar una instalación. Devuelve la instalación guardada.
class PantallaFormInstalacion extends StatefulWidget {
  final Map<String, dynamic>? inicial;
  const PantallaFormInstalacion({super.key, this.inicial});
  @override
  State<PantallaFormInstalacion> createState() => _PantallaFormInstalacionState();
}

class _PantallaFormInstalacionState extends State<PantallaFormInstalacion> {
  final _form = GlobalKey<FormState>();
  late final InstalacionForm _f;

  /// Cómo estaba (para mandar solo lo que cambió).
  late final Map<String, dynamic> _antes;
  final _errServidor = <String, String>{};
  bool _ocupado = false;
  String? _error;
  String? _idem;

  @override
  void initState() {
    super.initState();
    _f = widget.inicial == null
        ? InstalacionForm(tipo: vocabularioDe(context.read<Sesion>().perfil).tipoInstalacion)
        : InstalacionForm.desde(widget.inicial!);
    _antes = _f.aCuerpoApi();
  }

  String? _err(String campo) => _f.errores()[campo] ?? _errServidor[campo];

  Future<void> _guardar() async {
    final form = _form.currentState!;
    final conError = form.validateGranularly();
    if (conError.isNotEmpty) {
      FocusManager.instance.primaryFocus?.unfocus();
      avisar(context, 'Revisa los campos marcados en rojo.', error: true);
      irAlPrimerError(conError);
      return;
    }
    final sinc = context.read<Sincronizador>();
    final cuerpo = _f.aCuerpoApi();
    final cambios = cambiosLote(_antes, cuerpo);
    if (widget.inicial != null && cambios.isEmpty) {
      avisar(context, 'No cambiaste nada.');
      return;
    }
    _idem ??= const Uuid().v4();
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final r = widget.inicial == null
          ? await sinc.crearInstalacion(cuerpo, idempotencia: _idem!)
          : await sinc.editarInstalacion('${widget.inicial!['id']}', cambios);
      if (mounted) Navigator.pop(context, r);
    } on ErrorApi catch (e) {
      if (!mounted) return;
      final campos = erroresPorCampo(e, campoInstalacionDeApi);
      setState(() {
        _errServidor
          ..clear()
          ..addAll(campos);
        _error = 'No se guardó. ${mensajeLote(e)}';
      });
      irAlPrimerError(form.validateGranularly());
    } catch (e) {
      if (mounted) setState(() => _error = _mensaje(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Widget _texto(String etiqueta, String inicial, String campo, ValueChanged<String> alCambiar, {int lineas = 1, int? max}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: TextFormField(
          initialValue: inicial,
          maxLines: lineas,
          maxLength: max,
          validator: (_) => _err(campo),
          onChanged: (x) => setState(() {
            alCambiar(x);
            _errServidor.remove(campo);
          }),
          decoration: InputDecoration(labelText: etiqueta),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final v = vocabularioDe(context.watch<Sesion>().perfil);
    final c = context.colores;
    return Scaffold(
      appBar: AppBar(title: Text(widget.inicial == null ? 'Agregar ${v.instalacion}' : 'Editar ${v.instalacion}')),
      body: Form(
        key: _form,
        autovalidateMode: AutovalidateMode.onUserInteraction,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                _texto('Nombre *', _f.nombre, 'nombre', (x) => _f.nombre = x, max: 128),
                _texto('Código (opcional)', _f.codigo, 'codigo', (x) => _f.codigo = x, max: 32),
                _tipo('Tipo', tiposInstalacion, v.ordenInstalacion, _f.tipo, () => _err('tipo'), (x) => setState(() => _f.tipo = x)),
                const SizedBox(height: 10),
                DropdownButtonFormField<String>(
                  initialValue: provincias.contains(_f.provincia) ? _f.provincia : null,
                  isExpanded: true,
                  validator: (_) => _errServidor['provincia'],
                  decoration: const InputDecoration(labelText: 'Provincia (opcional)'),
                  items: [
                    const DropdownMenuItem(value: null, child: Text('Sin indicar')),
                    for (final p in provincias) DropdownMenuItem(value: p, child: Text(p)),
                  ],
                  onChanged: (x) => setState(() => _f.provincia = x ?? ''),
                ),
                const SizedBox(height: 10),
                _texto('Ciudad (opcional)', _f.ciudad, 'ciudad', (x) => _f.ciudad = x, max: 128),
                _texto('Dirección (opcional)', _f.direccion, 'direccion', (x) => _f.direccion = x),
                _texto('Notas (opcional)', _f.notas, 'notas', (x) => _f.notas = x, lineas: 3),
              ]),
            ),
            if (_error != null) ...[const SizedBox(height: 12), CajaError(texto: _error!)],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _ocupado ? null : _guardar,
                icon: _ocupado
                    ? SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: c.tintaSuave))
                    : const Icon(Icons.save_outlined),
                label: Text(_ocupado ? 'Guardando…' : 'Guardar'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Crear o editar una piscina o tanque de [instalacionId]: tipo, capacidad
/// (por dimensiones o volumen), ubicación y foto. Desde aquí también se
/// archiva. Devuelve la piscina guardada.
class PantallaFormPiscina extends StatefulWidget {
  final String instalacionId;
  final Map<String, dynamic>? inicial;
  final ElegirImagenes elegirImagen;
  final ComprimirImagen comprimir;
  const PantallaFormPiscina({
    super.key,
    required this.instalacionId,
    this.inicial,
    this.elegirImagen = elegirConImagePicker,
    this.comprimir = comprimirEnSegundoPlano,
  });
  @override
  State<PantallaFormPiscina> createState() => _PantallaFormPiscinaState();
}

class _PantallaFormPiscinaState extends State<PantallaFormPiscina> {
  final _form = GlobalKey<FormState>();
  late final PiscinaForm _p;

  /// Cómo estaba (para mandar solo lo que cambió).
  late final Map<String, dynamic> _antes;
  final _errServidor = <String, String>{};
  bool _ocupado = false;
  String? _error;
  String? _idem;
  Uint8List? _fotoActual;

  bool get _editando => widget.inicial != null;

  @override
  void initState() {
    super.initState();
    final v = vocabularioDe(context.read<Sesion>().perfil);
    _p = widget.inicial == null
        ? PiscinaForm(tipo: v.tipoPiscina, modo: v.modo, instalacionId: widget.instalacionId)
        : PiscinaForm.desde(widget.inicial!);
    _antes = _p.aCuerpoApi();
    if (widget.inicial?['has_image'] == true) {
      context.read<Sincronizador>().bajarFotoPiscina('${widget.inicial!['id']}').then((b) {
        if (mounted) setState(() => _fotoActual = b);
      }, onError: (_) {});
    }
  }

  String? _err(String campo) => _p.errores()[campo] ?? _errServidor[campo];

  Future<void> _elegirFoto(ImageSource origen) async {
    try {
      final fotos = await widget.elegirImagen(origen, 1);
      if (fotos.isEmpty) return;
      final jpeg = await widget.comprimir(fotos.first);
      if (mounted) {
        setState(() {
          _p
            ..foto = jpeg
            ..quitarFoto = false;
          _errServidor.remove('foto');
        });
      }
    } on FormatException {
      if (mounted) avisar(context, 'No se pudo leer esa imagen.', error: true);
    } catch (_) {
      if (mounted) avisar(context, 'No se pudo abrir la cámara o la galería. Revisa el permiso.', error: true);
    }
  }

  Future<void> _guardar() async {
    final form = _form.currentState!;
    final conError = form.validateGranularly();
    if (conError.isNotEmpty) {
      FocusManager.instance.primaryFocus?.unfocus();
      avisar(context, 'Revisa los campos marcados en rojo.', error: true);
      irAlPrimerError(conError);
      return;
    }
    final sinc = context.read<Sincronizador>();
    final cuerpo = _p.aCuerpoApi();
    final cambios = _p.conFoto(cambiosLote(_antes, cuerpo));
    if (_editando && cambios.isEmpty) {
      avisar(context, 'No cambiaste nada.');
      return;
    }
    _idem ??= const Uuid().v4();
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final r = _editando
          ? await sinc.editarPiscina('${widget.inicial!['id']}', cambios)
          : await sinc.crearPiscina(_p.conFoto(cuerpo), idempotencia: _idem!);
      if (mounted) Navigator.pop(context, r);
    } on ErrorApi catch (e) {
      if (!mounted) return;
      final campos = erroresPorCampo(e, campoInstalacionDeApi);
      setState(() {
        _errServidor
          ..clear()
          ..addAll(campos);
        _error = 'No se guardó. ${mensajeLote(e)}';
      });
      irAlPrimerError(form.validateGranularly());
    } catch (e) {
      if (mounted) setState(() => _error = _mensaje(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _archivar(Vocabulario v) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('Archivar ${v.piscina}'),
        content: Text('«${widget.inicial!['name']}» deja de ofrecerse como origen. Sigue en el historial de siembras y lotes.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Archivar')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      final r = await context.read<Sincronizador>().archivarPiscina('${widget.inicial!['id']}');
      if (mounted) Navigator.pop(context, r);
    } catch (e) {
      if (mounted) avisar(context, _mensaje(e), error: true);
    }
  }

  Widget _campo(String etiqueta, String inicial, String campo, ValueChanged<String> alCambiar,
          {bool numero = false, String? sufijo, int? max}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: TextFormField(
          initialValue: inicial,
          maxLength: max,
          keyboardType: numero ? const TextInputType.numberWithOptions(decimal: true) : null,
          validator: (_) => _err(campo),
          onChanged: (x) => setState(() {
            alCambiar(x);
            _errServidor.remove(campo);
          }),
          decoration: InputDecoration(labelText: etiqueta, suffixText: sufijo),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final v = vocabularioDe(context.watch<Sesion>().perfil);
    final c = context.colores;
    final foto = _p.foto ?? (_p.quitarFoto ? null : _fotoActual);
    final area = _p.area;
    return Scaffold(
      appBar: AppBar(title: Text(_editando ? 'Editar ${v.piscina}' : 'Agregar ${v.piscina}')),
      body: Form(
        key: _form,
        autovalidateMode: AutovalidateMode.onUserInteraction,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                _campo('Nombre *', _p.nombre, 'nombre', (x) => _p.nombre = x, max: 128),
                _campo('Código (opcional)', _p.codigo, 'codigo', (x) => _p.codigo = x, max: 32),
                _tipo('Tipo', tiposPiscina, v.ordenPiscina, _p.tipo, () => _err('tipo'), (x) => setState(() => _p.tipo = x)),
              ]),
            ),
            const Rotulo('Capacidad'),
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                SegmentedButton<String>(
                  showSelectedIcon: false,
                  segments: const [
                    ButtonSegment(value: 'dimensions', label: Text('Por dimensiones')),
                    ButtonSegment(value: 'volume', label: Text('Volumen directo')),
                  ],
                  selected: {_p.modo},
                  onSelectionChanged: (x) => setState(() => _p.modo = x.first),
                ),
                const SizedBox(height: 12),
                if (_p.modo == 'dimensions') ...[
                  _campo('Largo', _p.largo, 'largo', (x) => _p.largo = x, numero: true, sufijo: 'm'),
                  _campo('Ancho', _p.ancho, 'ancho', (x) => _p.ancho = x, numero: true, sufijo: 'm'),
                  _campo('Profundidad', _p.profundidad, 'profundidad', (x) => _p.profundidad = x, numero: true, sufijo: 'm'),
                  Text('Área: ${area == null ? '—' : '${numero(area)} m²'}', style: TextStyle(color: c.tintaSuave)),
                ] else
                  _campo('Volumen', _p.volumen, 'volumen', (x) => _p.volumen = x, numero: true, sufijo: 'm³'),
              ]),
            ),
            const Rotulo('Ubicación y foto'),
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                _campo('Ubicación (opcional)', _p.ubicacion, 'ubicacion', (x) => _p.ubicacion = x),
                FormField<Object>(
                  validator: (_) => _errServidor['foto'],
                  builder: (estado) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    if (foto != null)
                      ClipRRect(
                        borderRadius: BorderRadius.circular(10),
                        child: Image.memory(foto, height: 140, width: double.infinity, fit: BoxFit.cover),
                      )
                    else
                      Text('Sin foto.', style: TextStyle(color: c.tintaSuave)),
                    if (estado.hasError) ...[
                      const SizedBox(height: 6),
                      Text(estado.errorText!, style: TextStyle(fontSize: 12.5, color: c.peligroTexto)),
                    ],
                    const SizedBox(height: 8),
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
                        onPressed: _ocupado ? null : () => _elegirFoto(ImageSource.camera),
                        icon: const Icon(Icons.photo_camera_outlined),
                        label: const Text('Tomar foto'),
                      ),
                      OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
                        onPressed: _ocupado ? null : () => _elegirFoto(ImageSource.gallery),
                        icon: const Icon(Icons.photo_library_outlined),
                        label: const Text('Galería'),
                      ),
                      if (foto != null)
                        TextButton(
                          onPressed: () => setState(() {
                            _p
                              ..foto = null
                              ..quitarFoto = true;
                          }),
                          child: const Text('Quitar la foto'),
                        ),
                    ]),
                  ]),
                ),
              ]),
            ),
            const SizedBox(height: 10),
            Tarjeta(child: _campo('Notas (opcional)', _p.notas, 'notas', (x) => _p.notas = x)),
            if (_error != null) ...[const SizedBox(height: 12), CajaError(texto: _error!)],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _ocupado ? null : _guardar,
                icon: _ocupado
                    ? SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: c.tintaSuave))
                    : const Icon(Icons.save_outlined),
                label: Text(_ocupado ? 'Guardando…' : 'Guardar'),
              ),
            ),
            if (_editando) ...[
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  style: EstilosBoton.peligro(context),
                  onPressed: _ocupado ? null : () => _archivar(v),
                  icon: const Icon(Icons.archive_outlined),
                  label: Text('Archivar ${v.piscina}'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
