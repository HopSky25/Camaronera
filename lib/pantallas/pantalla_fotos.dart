import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../tema.dart';

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

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    final sinc = context.read<Sincronizador>();
    final v = sinc.bandeja.firstWhere((e) => e.id == widget.id);
    final rutas = await sinc.fotosDe(v);
    setState(() => _rutas = rutas);
  }

  Future<void> _tomar() async {
    final foto = await ImagePicker().pickImage(source: ImageSource.camera, imageQuality: 70, maxWidth: 1600);
    if (foto == null || !mounted) return;
    final dir = await getApplicationDocumentsDirectory();
    final carpeta = Directory(p.join(dir.path, 'fotos', widget.id));
    await carpeta.create(recursive: true);
    final destino = p.join(carpeta.path, '${DateTime.now().millisecondsSinceEpoch}.jpg');
    await File(foto.path).copy(destino);
    if (!mounted) return;
    final sinc = context.read<Sincronizador>();
    await sinc.guardarFoto(sinc.bandeja.firstWhere((e) => e.id == widget.id), destino);
    await _cargar();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Fotos de campo')),
      body: _rutas.isEmpty
          ? const Center(child: Text('Todavía no hay fotos de este lote.', style: TextStyle(color: Marca.tintaSuave)))
          : GridView.builder(
              padding: const EdgeInsets.all(8),
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(crossAxisCount: 3, crossAxisSpacing: 6, mainAxisSpacing: 6),
              itemCount: _rutas.length,
              itemBuilder: (_, i) => ClipRRect(borderRadius: BorderRadius.circular(10), child: Image.file(File(_rutas[i]), fit: BoxFit.cover)),
            ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _tomar,
        icon: const Icon(Icons.photo_camera),
        label: const Text('Tomar foto'),
      ),
    );
  }
}
