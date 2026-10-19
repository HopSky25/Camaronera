import 'dart:isolate';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';

import '../../datos/api_cliente.dart';
import '../../datos/lotes_vendedor.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/gestion_lote.dart';
import '../../modelos/lote_nuevo.dart';
import '../../tema.dart';
import '../comunes/widgets.dart';

/// Cómo se eligen las imágenes: cámara (una) o galería (varias, hasta
/// [maximo]). Devuelve los bytes originales. Se cambia en las pruebas.
typedef ElegirImagenes = Future<List<Uint8List>> Function(ImageSource origen, int maximo);

/// Comprime una imagen para subirla (por defecto [prepararFotoLote] fuera
/// del hilo de la pantalla). Se cambia en las pruebas.
typedef ComprimirImagen = Future<Uint8List> Function(Uint8List original);

Future<List<Uint8List>> elegirConImagePicker(ImageSource origen, int maximo) async {
  final picker = ImagePicker();
  if (origen == ImageSource.camera) {
    final f = await picker.pickImage(source: ImageSource.camera, maxWidth: 2400, maxHeight: 2400, imageQuality: 90);
    return f == null ? const [] : [await f.readAsBytes()];
  }
  final fs = maximo > 1
      ? await picker.pickMultiImage(maxWidth: 2400, maxHeight: 2400, imageQuality: 90, limit: maximo)
      : [?await picker.pickImage(source: ImageSource.gallery, maxWidth: 2400, maxHeight: 2400, imageQuality: 90)];
  return [for (final f in fs.take(maximo)) await f.readAsBytes()];
}

Future<Uint8List> comprimirEnSegundoPlano(Uint8List original) => Isolate.run(() => prepararFotoLote(original));

/// Miniaturas ya bajadas en esta sesión de la app (producto/foto → bytes).
final _miniaturas = <String, Uint8List>{};

/// Miniatura de una foto del lote (`GET /products/{id}/photos/{foto}?thumb=1`).
class MiniaturaFotoLote extends StatefulWidget {
  final String productoId;
  final String fotoId;
  final double lado;
  const MiniaturaFotoLote({super.key, required this.productoId, required this.fotoId, this.lado = 72});
  @override
  State<MiniaturaFotoLote> createState() => _MiniaturaFotoLoteState();
}

class _MiniaturaFotoLoteState extends State<MiniaturaFotoLote> {
  late Future<Uint8List> _bytes = _bajar();

  Future<Uint8List> _bajar() async {
    final clave = '${widget.productoId}/${widget.fotoId}';
    final enMemoria = _miniaturas[clave];
    if (enMemoria != null) return enMemoria;
    final b = await context.read<Sincronizador>().bajarFotoLote(widget.productoId, widget.fotoId);
    _miniaturas[clave] = b;
    return b;
  }

  @override
  void didUpdateWidget(MiniaturaFotoLote vieja) {
    super.didUpdateWidget(vieja);
    if (vieja.fotoId != widget.fotoId) _bytes = _bajar();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        width: widget.lado,
        height: widget.lado,
        child: ColoredBox(
          color: c.fondoAlt,
          child: FutureBuilder<Uint8List>(
            future: _bytes,
            builder: (context, s) {
              if (s.hasData) {
                return Image.memory(s.data!, fit: BoxFit.cover, gaplessPlayback: true,
                    errorBuilder: (_, _, _) => Icon(Icons.broken_image_outlined, color: c.tintaTenue));
              }
              return Icon(s.hasError ? Icons.broken_image_outlined : Icons.image_outlined, color: c.tintaTenue);
            },
          ),
        ),
      ),
    );
  }
}

/// Fotos de un lote propio: subir (cámara o galería), ordenar (la primera
/// es la portada) y quitar. Como el formulario del portal: hasta 20 fotos,
/// JPG o PNG de hasta 5 MB (la app las comprime a 1600 px). No se firman.
class PantallaFotosLote extends StatefulWidget {
  final String productoId;
  final String nombre;
  final ElegirImagenes elegir;
  final ComprimirImagen comprimir;
  const PantallaFotosLote({
    super.key,
    required this.productoId,
    this.nombre = '',
    this.elegir = elegirConImagePicker,
    this.comprimir = comprimirEnSegundoPlano,
  });
  @override
  State<PantallaFotosLote> createState() => _PantallaFotosLoteState();
}

class _PantallaFotosLoteState extends State<PantallaFotosLote> {
  List<Map<String, dynamic>> _fotos = const [];
  bool _cargando = true;
  String? _error;
  String? _progreso;
  bool _ocupado = false;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    setState(() {
      _cargando = true;
      _error = null;
    });
    try {
      final fotos = await context.read<Sincronizador>().fotosLote(widget.productoId);
      if (mounted) setState(() => _fotos = fotos);
    } catch (e) {
      if (mounted) setState(() => _error = e is ErrorApi ? mensajeLote(e) : mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(texto),
        backgroundColor: error ? context.colores.peligro : null,
        duration: Duration(seconds: error ? 6 : 3),
      ));
  }

  String _mensaje(Object e) => switch (e) {
        SinConexion() => 'Sin internet: no se guardó el cambio. Inténtalo cuando tengas señal.',
        final ErrorApi err => mensajeLote(err),
        _ => mensajeDeError(e),
      };

  Future<void> _agregar(ImageSource origen) async {
    final restantes = maximoFotosLote - _fotos.length;
    if (_ocupado) return;
    if (restantes <= 0) {
      _aviso('El lote ya tiene $maximoFotosLote fotos. Quita una para agregar otra.', error: true);
      return;
    }
    final sinc = context.read<Sincronizador>();
    List<Uint8List> elegidas;
    try {
      elegidas = await widget.elegir(origen, restantes);
    } catch (_) {
      _aviso(origen == ImageSource.camera
          ? 'No se pudo abrir la cámara. Revisa el permiso e inténtalo otra vez.'
          : 'No se pudo abrir la galería. Revisa el permiso e inténtalo otra vez.', error: true);
      return;
    }
    if (elegidas.isEmpty || !mounted) return;
    setState(() => _ocupado = true);
    var subidas = 0;
    try {
      for (final (i, original) in elegidas.take(restantes).indexed) {
        setState(() => _progreso = elegidas.length == 1 ? 'Subiendo la foto…' : 'Subiendo ${i + 1} de ${elegidas.length}…');
        Uint8List jpeg;
        try {
          jpeg = await widget.comprimir(original);
        } on FormatException {
          _aviso('Una de las imágenes no se pudo leer o es demasiado grande; se omitió.', error: true);
          continue;
        }
        final fotos = await sinc.subirFotoLote(widget.productoId, jpeg,
            nombreArchivo: 'lote_${DateTime.now().millisecondsSinceEpoch}_$i.jpg', idempotencia: const Uuid().v4());
        subidas++;
        if (mounted) setState(() => _fotos = fotos);
      }
      if (subidas > 0) _aviso(subidas == 1 ? 'Foto agregada.' : '$subidas fotos agregadas.');
    } catch (e) {
      _aviso(_mensaje(e), error: true);
    } finally {
      if (mounted) {
        setState(() {
          _ocupado = false;
          _progreso = null;
        });
      }
    }
  }

  Future<void> _ordenar(List<String> orden, {String? hecho}) async {
    if (_ocupado) return;
    final antes = _fotos;
    final porId = {for (final f in _fotos) f['id']: f};
    setState(() {
      _ocupado = true;
      _fotos = [for (final id in orden) ?porId[id]];
    });
    try {
      final fotos = await context.read<Sincronizador>().ordenarFotosLote(widget.productoId, orden, idempotencia: const Uuid().v4());
      if (mounted) setState(() => _fotos = fotos);
      if (hecho != null) _aviso(hecho);
    } catch (e) {
      if (mounted) setState(() => _fotos = antes);
      _aviso(_mensaje(e), error: true);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  List<String> get _ids => [for (final f in _fotos) '${f['id']}'];

  Future<void> _quitar(Map<String, dynamic> foto) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Quitar la foto'),
        content: Text(_fotos.first == foto
            ? 'Es la portada del lote: la siguiente foto pasará a ser la portada.'
            : 'La foto deja de verse en el lote.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Quitar')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _ocupado = true);
    try {
      final fotos = await context.read<Sincronizador>().quitarFotoLote(widget.productoId, '${foto['id']}');
      if (mounted) setState(() => _fotos = fotos);
      _aviso('Foto quitada.');
    } catch (e) {
      _aviso(_mensaje(e), error: true);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    final c = context.colores;
    final lleno = _fotos.length >= maximoFotosLote;
    return Scaffold(
      appBar: AppBar(title: const Text('Fotos del lote')),
      bottomNavigationBar: soloLectura || _cargando || _error != null
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  if (_progreso != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Row(children: [
                        const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                        const SizedBox(width: 10),
                        Text(_progreso!, style: TextStyle(color: c.tintaSuave)),
                      ]),
                    ),
                  Row(children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _ocupado || lleno ? null : () => _agregar(ImageSource.camera),
                        icon: const Icon(Icons.photo_camera_outlined),
                        label: const Text('Tomar foto'),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _ocupado || lleno ? null : () => _agregar(ImageSource.gallery),
                        icon: const Icon(Icons.photo_library_outlined),
                        label: const Text('Galería'),
                      ),
                    ),
                  ]),
                ]),
              ),
            ),
      body: _cargando
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? ListView(padding: const EdgeInsets.all(16), children: [CajaError(texto: _error!, reintentar: _cargar)])
              : ReorderableListView.builder(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                  buildDefaultDragHandles: !soloLectura && !_ocupado,
                  header: Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                      if (widget.nombre.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 4, bottom: 8),
                          child: Text(widget.nombre, style: Theme.of(context).textTheme.titleLarge),
                        ),
                      if (soloLectura)
                        const AvisoSoloLectura()
                      else
                        CajaAviso(
                          tipo: TipoAviso.info,
                          icono: Icons.photo_outlined,
                          texto: 'La primera foto es la portada: la que ven los compradores en el marketplace. '
                              'Mantén presionada una foto y arrástrala para cambiar el orden. '
                              'Hasta $maximoFotosLote fotos (${_fotos.length} de $maximoFotosLote).',
                        ),
                      if (_fotos.isEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 24),
                          child: Text('Este lote todavía no tiene fotos.', textAlign: TextAlign.center, style: TextStyle(color: c.tintaSuave)),
                        ),
                    ]),
                  ),
                  itemCount: _fotos.length,
                  onReorderItem: (desde, hasta) => _ordenar(moverFoto(_ids, desde, hasta)),
                  itemBuilder: (context, i) {
                    final f = _fotos[i];
                    return Padding(
                      key: ValueKey(f['id']),
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Tarjeta(
                        padding: const EdgeInsets.all(10),
                        child: Row(children: [
                          MiniaturaFotoLote(productoId: widget.productoId, fotoId: '${f['id']}'),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Text('Foto ${i + 1}', style: TextStyle(fontWeight: FontWeight.w700, color: c.titulo)),
                              const SizedBox(height: 4),
                              if (i == 0)
                                const Insignia(estado: 'portada', texto: 'Portada', tipo: TipoPildora.hecho)
                              else if (!soloLectura)
                                TextButton(
                                  style: TextButton.styleFrom(padding: EdgeInsets.zero, visualDensity: VisualDensity.compact),
                                  onPressed: _ocupado
                                      ? null
                                      : () => _ordenar(moverFoto(_ids, i, 0), hecho: 'Ahora es la portada del lote.'),
                                  child: const Text('Hacer portada'),
                                ),
                            ]),
                          ),
                          if (!soloLectura)
                            IconButton(
                              tooltip: 'Quitar la foto',
                              onPressed: _ocupado ? null : () => _quitar(f),
                              icon: Icon(Icons.delete_outline, color: c.peligroTexto),
                            ),
                        ]),
                      ),
                    );
                  },
                ),
    );
  }
}
