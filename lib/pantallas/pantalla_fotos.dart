import 'dart:io';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path/path.dart' as p;
import 'package:provider/provider.dart';

import '../datos/api_cliente.dart';
import '../datos/base_local.dart';
import '../datos/sincronizador.dart';
import '../modelos/foto_campo.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firmador.dart';
import '../tema.dart';
import 'comunes/widgets.dart';
import 'pantalla_pendientes.dart';

/// Fotos de campo (C4). Cada foto se guarda primero en el teléfono (nunca se
/// pierde) y después, confirmada con la huella / PIN, pasa por la cola
/// firmada (`POST /verifications/{id}/photos`, comprimida a 1600 px y JPEG
/// 75). Cada foto muestra su estado: sin confirmar, pendiente, subida o
/// con error.
class PantallaFotos extends StatefulWidget {
  final String id;
  const PantallaFotos({super.key, required this.id});
  @override
  State<PantallaFotos> createState() => _PantallaFotosState();
}

class _PantallaFotosState extends State<PantallaFotos> {
  List<FotoCampo> _fotos = [];
  bool _tomando = false;
  bool _subiendo = false;
  bool _noEsta = false;
  Sincronizador? _sinc;

  @override
  void initState() {
    super.initState();
    _sinc = context.read<Sincronizador>()..addListener(_alCambiarCola);
    _cargar();
  }

  @override
  void dispose() {
    _sinc?.removeListener(_alCambiarCola);
    super.dispose();
  }

  /// La cola cambió (se subió algo o el servidor lo rechazó): estados al día.
  void _alCambiarCola() {
    if (mounted) _cargar();
  }

  Verificacion? get _verificacion => context.read<Sincronizador>().bandeja.where((e) => e.id == widget.id).firstOrNull;

  Future<void> _cargar() async {
    final v = _verificacion;
    if (v == null) {
      if (mounted) setState(() => _noEsta = true);
      return;
    }
    final fotos = await context.read<Sincronizador>().fotosDe(v.id);
    if (!mounted) return;
    setState(() => _fotos = fotos);
  }

  /// Última posición conocida (sin pedir permiso aquí: lo pide el informe).
  Future<Position?> _posicion() async {
    try {
      final permiso = await Geolocator.checkPermission();
      if (permiso != LocationPermission.always && permiso != LocationPermission.whileInUse) return null;
      return await Geolocator.getLastKnownPosition().timeout(const Duration(seconds: 3));
    } catch (_) {
      return null;
    }
  }

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(texto),
      backgroundColor: error ? Marca.rojo : null,
      duration: Duration(seconds: error ? 6 : 3),
    ));
  }

  Future<void> _tomar() async {
    if (_tomando) return;
    final v = _verificacion;
    if (v == null) return;
    setState(() => _tomando = true);
    final sinc = context.read<Sincronizador>();
    FotoCampo? guardada;
    try {
      final foto = await ImagePicker().pickImage(source: ImageSource.camera, maxWidth: 2400, maxHeight: 2400, imageQuality: 90);
      if (foto == null || !mounted) return;
      final carpeta = Directory(p.join((await BaseLocal.carpetaFotos()).path, widget.id));
      await carpeta.create(recursive: true);
      final ahora = DateTime.now();
      final destino = p.join(carpeta.path, '${ahora.millisecondsSinceEpoch}.jpg');
      await File(foto.path).copy(destino);
      final pos = await _posicion();
      final id = await sinc.guardarFoto(v, destino, tomadaEn: ahora, latitud: pos?.latitude, longitud: pos?.longitude);
      guardada = FotoCampo(
          id: id, verificacionId: v.id, ruta: destino, tomadaEn: ahora, latitud: pos?.latitude, longitud: pos?.longitude);
      await _cargar();
    } catch (_) {
      _aviso('No se pudo tomar la foto. Revisa el permiso de la cámara e inténtalo otra vez.');
      return;
    } finally {
      if (mounted) setState(() => _tomando = false);
    }
    // Guardada: ahora se confirma con la huella para subirla.
    await _subir([guardada]);
  }

  /// Firma y encola las fotos (una huella por foto). Si la persona no
  /// confirma, las fotos siguen guardadas "sin confirmar".
  Future<void> _subir(List<FotoCampo> fotos) async {
    if (_subiendo || fotos.isEmpty) return;
    setState(() => _subiendo = true);
    final sinc = context.read<Sincronizador>();
    var hechas = 0;
    try {
      for (final f in fotos) {
        await sinc.subirFoto(f);
        hechas++;
      }
      _aviso(fotos.length == 1 ? 'Foto lista para subir: se envía en cuanto haya señal.' : '$hechas fotos listas para subir.');
    } on ErrorSeguridad catch (e) {
      _aviso(e.cancelado ? 'La foto quedó guardada en el teléfono. Confírmala con tu huella para subirla.' : e.mensaje);
    } on FormatException catch (e) {
      _aviso('No se pudo preparar la foto: ${e.message}', error: true);
    } catch (e) {
      _aviso(mensajeDeError(e), error: true);
    } finally {
      if (mounted) setState(() => _subiendo = false);
      await _cargar();
    }
  }

  Future<void> _tocar(FotoCampo f) async {
    switch (f.estado) {
      case EstadoFoto.sinConfirmar:
        await _subir([f]);
      case EstadoFoto.error:
        final ir = await showDialog<bool>(
          context: context,
          builder: (c) => AlertDialog(
            title: const Text('El servidor no aceptó esta foto'),
            content: Text('${f.error ?? ''}\n\nPuedes descartarla en Pendientes; la foto sigue guardada en el teléfono.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cerrar')),
              FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Ir a Pendientes')),
            ],
          ),
        );
        if (ir == true && mounted) {
          await Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaPendientes()));
          await _cargar();
        }
      case EstadoFoto.pendiente:
        _aviso('Se sube en cuanto haya señal.');
      case EstadoFoto.subida:
        _aviso('Esta foto ya está en el servidor.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final sinConfirmar = _fotos.where((f) => f.estado == EstadoFoto.sinConfirmar).toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Fotos de campo')),
      body: _noEsta
          ? const VacioLista(texto: 'Esta verificación ya no está en tu bandeja.')
          : _fotos.isEmpty
              ? const VacioLista(texto: 'Todavía no hay fotos de este lote.', icono: Icons.photo_camera_outlined)
              : Column(children: [
                  if (sinConfirmar.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
                      child: Tarjeta(
                        padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
                        child: Row(children: [
                          const Icon(Icons.fingerprint, color: Marca.ambar),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                                sinConfirmar.length == 1
                                    ? '1 foto guardada sin confirmar.'
                                    : '${sinConfirmar.length} fotos guardadas sin confirmar.',
                                style: const TextStyle(color: Marca.ambar, fontWeight: FontWeight.w600)),
                          ),
                          TextButton(onPressed: _subiendo ? null : () => _subir(sinConfirmar), child: const Text('Subir')),
                        ]),
                      ),
                    ),
                  Expanded(
                    child: GridView.builder(
                      padding: const EdgeInsets.all(8),
                      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: 3, crossAxisSpacing: 6, mainAxisSpacing: 6),
                      itemCount: _fotos.length,
                      itemBuilder: (_, i) => _Miniatura(foto: _fotos[i], onTap: () => _tocar(_fotos[i])),
                    ),
                  ),
                ]),
      floatingActionButton: _noEsta
          ? null
          : FloatingActionButton.extended(
              onPressed: _tomando || _subiendo ? null : _tomar,
              icon: const Icon(Icons.photo_camera),
              label: Text(_tomando ? 'Abriendo la cámara…' : (_subiendo ? 'Preparando…' : 'Tomar foto')),
            ),
    );
  }
}

/// Texto, icono y color de cada estado.
(String, IconData, Color) aspectoDeEstadoFoto(EstadoFoto e) => switch (e) {
      EstadoFoto.sinConfirmar => ('Sin confirmar', Icons.fingerprint, Marca.ambar),
      EstadoFoto.pendiente => ('Pendiente', Icons.cloud_upload_outlined, Marca.navy),
      EstadoFoto.subida => ('Subida', Icons.cloud_done_outlined, Marca.verde),
      EstadoFoto.error => ('Error', Icons.error_outline, Marca.rojo),
    };

class _Miniatura extends StatelessWidget {
  final FotoCampo foto;
  final VoidCallback onTap;
  const _Miniatura({required this.foto, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final (texto, icono, color) = aspectoDeEstadoFoto(foto.estado);
    return GestureDetector(
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: Stack(fit: StackFit.expand, children: [
          Image.file(
            File(foto.ruta),
            fit: BoxFit.cover,
            cacheWidth: 300,
            errorBuilder: (_, _, _) => const ColoredBox(
              color: Marca.fondoSuave,
              child: Icon(Icons.broken_image_outlined, color: Marca.gris),
            ),
          ),
          Positioned(
            left: 4,
            right: 4,
            bottom: 4,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
              decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.92), borderRadius: BorderRadius.circular(8)),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(icono, size: 14, color: color),
                const SizedBox(width: 4),
                Flexible(
                  child: Text(texto,
                      overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: color)),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }
}
