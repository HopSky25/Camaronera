import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path/path.dart' as p;
import 'package:provider/provider.dart';

import '../datos/base_local.dart';
import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
import 'comunes/widgets.dart';

/// Fotos de campo. Se guardan comprimidas en el teléfono. La API de
/// CamaronMarket todavía no recibe archivos: cuando exista el endpoint, la
/// cola las subirá; mientras tanto quedan aquí como respaldo del técnico.
class PantallaFotos extends StatefulWidget {
  final String id;
  const PantallaFotos({super.key, required this.id});
  @override
  State<PantallaFotos> createState() => _PantallaFotosState();
}

class _PantallaFotosState extends State<PantallaFotos> {
  List<String> _rutas = [];
  bool _tomando = false;
  bool _noEsta = false;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Verificacion? get _verificacion => context.read<Sincronizador>().bandeja.where((e) => e.id == widget.id).firstOrNull;

  Future<void> _cargar() async {
    final v = _verificacion;
    if (v == null) {
      if (mounted) setState(() => _noEsta = true);
      return;
    }
    final rutas = await context.read<Sincronizador>().fotosDe(v);
    if (!mounted) return;
    setState(() => _rutas = rutas);
  }

  Future<void> _tomar() async {
    if (_tomando) return;
    final v = _verificacion;
    if (v == null) return;
    setState(() => _tomando = true);
    try {
      final foto = await ImagePicker().pickImage(source: ImageSource.camera, imageQuality: 70, maxWidth: 1600);
      if (foto == null || !mounted) return;
      final carpeta = Directory(p.join((await BaseLocal.carpetaFotos()).path, widget.id));
      await carpeta.create(recursive: true);
      final destino = p.join(carpeta.path, '${DateTime.now().millisecondsSinceEpoch}.jpg');
      await File(foto.path).copy(destino);
      if (!mounted) return;
      await context.read<Sincronizador>().guardarFoto(v, destino);
      await _cargar();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('No se pudo tomar la foto. Revisa el permiso de la cámara e inténtalo otra vez.')));
      }
    } finally {
      if (mounted) setState(() => _tomando = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Fotos de campo')),
      body: _noEsta
          ? const VacioLista(texto: 'Esta verificación ya no está en tu bandeja.')
          : _rutas.isEmpty
              ? const VacioLista(texto: 'Todavía no hay fotos de este lote.', icono: Icons.photo_camera_outlined)
              : GridView.builder(
                  padding: const EdgeInsets.all(8),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(crossAxisCount: 3, crossAxisSpacing: 6, mainAxisSpacing: 6),
                  itemCount: _rutas.length,
                  itemBuilder: (_, i) => ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Image.file(
                      File(_rutas[i]),
                      fit: BoxFit.cover,
                      errorBuilder: (_, _, _) => const ColoredBox(
                        color: Marca.fondoSuave,
                        child: Icon(Icons.broken_image_outlined, color: Marca.gris),
                      ),
                    ),
                  ),
                ),
      floatingActionButton: _noEsta
          ? null
          : FloatingActionButton.extended(
              onPressed: _tomando ? null : _tomar,
              icon: const Icon(Icons.photo_camera),
              label: Text(_tomando ? 'Abriendo la cámara…' : 'Tomar foto'),
            ),
    );
  }
}
