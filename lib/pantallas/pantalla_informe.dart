import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';

/// Captura del informe en campo. Todo se guarda en el teléfono al pulsar
/// Guardar; el envío al servidor lo hace la cola cuando hay internet.
class PantallaInforme extends StatefulWidget {
  final String id;
  const PantallaInforme({super.key, required this.id});
  @override
  State<PantallaInforme> createState() => _PantallaInformeState();
}

class _PantallaInformeState extends State<PantallaInforme> {
  final _form = GlobalKey<FormState>();
  InformeCampo? _inf;
  Verificacion? _v;
  bool _buscandoGps = false;

  // Controladores de texto (uno por campo numérico/texto).
  late final _planta = TextEditingController();
  late final _enviadas = TextEditingController();
  late final _enPlanta = TextEditingController();
  late final _basura = TextEditingController();
  late final _ppm = TextEditingController();
  late final _ppmNotas = TextEditingController();
  late final _saborNotas = TextEditingController();
  late final _gFinca = TextEditingController();
  late final _gPlanta1 = TextEditingController();
  late final _gPlanta2 = TextEditingController();
  late final _incidencias = TextEditingController();

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    final sinc = context.read<Sincronizador>();
    _v = sinc.bandeja.firstWhere((e) => e.id == widget.id);
    final inf = await sinc.informeDe(_v!);
    String t(double? d) => d == null ? '' : (d % 1 == 0 ? d.toInt().toString() : d.toString());
    _planta.text = inf.plantaNombre ?? '';
    _enviadas.text = t(inf.librasEnviadas);
    _enPlanta.text = t(inf.librasPlanta);
    _basura.text = t(inf.librasBasura);
    _ppm.text = t(inf.metabisulfitoPpm);
    _ppmNotas.text = inf.metabisulfitoNotas ?? '';
    _saborNotas.text = inf.saborNotas ?? '';
    _gFinca.text = t(inf.gramosFinca);
    _gPlanta1.text = t(inf.gramosPlanta1);
    _gPlanta2.text = t(inf.gramosPlanta2);
    _incidencias.text = inf.incidencias ?? '';
    setState(() => _inf = inf);
  }

  double? _d(TextEditingController c) => double.tryParse(c.text.replaceAll(',', '.').trim());

  void _volcar() {
    final inf = _inf!;
    inf.plantaNombre = _planta.text.trim().isEmpty ? null : _planta.text.trim();
    inf.librasEnviadas = _d(_enviadas);
    inf.librasPlanta = _d(_enPlanta);
    inf.librasBasura = _d(_basura);
    inf.metabisulfitoPpm = _d(_ppm);
    inf.metabisulfitoNotas = _ppmNotas.text.trim();
    inf.saborNotas = _saborNotas.text.trim();
    inf.gramosFinca = _d(_gFinca);
    inf.gramosPlanta1 = _d(_gPlanta1);
    inf.gramosPlanta2 = _d(_gPlanta2);
    inf.incidencias = _incidencias.text.trim();
  }

  Future<void> _guardar({required bool enviar}) async {
    if (!_form.currentState!.validate()) return;
    _volcar();
    final clases = _inf!.lineas.map((l) => l.clase).toList();
    if (clases.length != clases.toSet().length) {
      _aviso('Solo puede haber una fila por clase (A, B, C).');
      return;
    }
    await context.read<Sincronizador>().guardarInforme(_v!, _inf!, enviar: enviar);
    if (!mounted) return;
    _aviso(enviar ? 'Informe guardado; se enviará cuando haya internet' : 'Borrador guardado en el teléfono');
    Navigator.pop(context);
  }

  void _aviso(String texto) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(texto)));

  Future<void> _tomarGps() async {
    setState(() => _buscandoGps = true);
    try {
      var permiso = await Geolocator.checkPermission();
      if (permiso == LocationPermission.denied) permiso = await Geolocator.requestPermission();
      if (permiso == LocationPermission.denied || permiso == LocationPermission.deniedForever) {
        _aviso('Sin permiso de ubicación');
        return;
      }
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high, timeLimit: Duration(seconds: 25)),
      );
      setState(() {
        _inf!.latitud = pos.latitude;
        _inf!.longitud = pos.longitude;
      });
    } catch (e) {
      _aviso('No se pudo leer el GPS: $e');
    } finally {
      if (mounted) setState(() => _buscandoGps = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final inf = _inf;
    if (inf == null) return Scaffold(appBar: AppBar(), body: const Center(child: CircularProgressIndicator()));
    final numero = const TextInputType.numberWithOptions(decimal: true);
    String? positivo(String? s) {
      if (s == null || s.trim().isEmpty) return null;
      final d = double.tryParse(s.replaceAll(',', '.'));
      return d == null || d < 0 ? 'Número no válido' : null;
    }

    return Scaffold(
      appBar: AppBar(title: Text('Informe · ${_v!.referencia}')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
          children: [
            const Rotulo('Pesos'),
            TextFormField(controller: _planta, decoration: const InputDecoration(labelText: 'Planta / lugar de pesaje')),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(child: TextFormField(controller: _enviadas, keyboardType: numero, validator: positivo,
                  decoration: const InputDecoration(labelText: 'Libras enviadas'))),
              const SizedBox(width: 10),
              Expanded(child: TextFormField(controller: _enPlanta, keyboardType: numero, validator: positivo,
                  decoration: const InputDecoration(labelText: 'Libras en planta'))),
            ]),
            const SizedBox(height: 10),
            TextFormField(controller: _basura, keyboardType: numero, validator: positivo,
                decoration: const InputDecoration(labelText: 'Basura (lb)')),
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              initialValue: inf.presentacion,
              decoration: const InputDecoration(labelText: 'Presentación'),
              items: const [
                DropdownMenuItem(value: 'entero', child: Text('Entero')),
                DropdownMenuItem(value: 'cola', child: Text('Cola')),
              ],
              onChanged: (v) => setState(() => inf.presentacion = v),
            ),
            const Rotulo('Clasificación por talla (una fila por clase)'),
            for (var i = 0; i < inf.lineas.length; i++) _FilaLinea(
              linea: inf.lineas[i],
              onBorrar: () => setState(() => inf.lineas.removeAt(i)),
              onCambio: () => setState(() {}),
            ),
            OutlinedButton.icon(
              onPressed: inf.lineas.length >= 3
                  ? null
                  : () => setState(() {
                        final usadas = inf.lineas.map((l) => l.clase).toSet();
                        final libre = ['a', 'b', 'c'].firstWhere((c) => !usadas.contains(c));
                        inf.lineas.add(LineaClasificacion(clase: libre, talla: '', libras: 0));
                      }),
              icon: const Icon(Icons.add),
              label: const Text('Agregar clase'),
            ),
            const Rotulo('Conteos (camarones por libra)'),
            for (var i = 0; i < inf.conteos.length; i++) _FilaConteo(
              conteo: inf.conteos[i],
              onBorrar: () => setState(() => inf.conteos.removeAt(i)),
            ),
            OutlinedButton.icon(
              onPressed: () => setState(() => inf.conteos.add(Conteo(valor: 0))),
              icon: const Icon(Icons.add),
              label: const Text('Agregar conteo'),
            ),
            const Rotulo('Metabisulfito'),
            TextFormField(controller: _ppm, keyboardType: numero, validator: positivo,
                decoration: const InputDecoration(labelText: 'ppm')),
            const SizedBox(height: 10),
            TextFormField(controller: _ppmNotas, decoration: const InputDecoration(labelText: 'Notas')),
            const Rotulo('Sabor'),
            DropdownButtonFormField<String>(
              initialValue: inf.sabor,
              decoration: const InputDecoration(labelText: 'Resultado'),
              items: const [
                DropdownMenuItem(value: 'excellent', child: Text('Excelente')),
                DropdownMenuItem(value: 'good', child: Text('Bueno')),
                DropdownMenuItem(value: 'acceptable', child: Text('Aceptable')),
                DropdownMenuItem(value: 'rejected', child: Text('Rechazado')),
              ],
              onChanged: (v) => setState(() => inf.sabor = v),
            ),
            const SizedBox(height: 10),
            TextFormField(controller: _saborNotas, decoration: const InputDecoration(labelText: 'Notas de sabor')),
            const Rotulo('Gramaje'),
            Row(children: [
              Expanded(child: TextFormField(controller: _gFinca, keyboardType: numero, validator: positivo,
                  decoration: const InputDecoration(labelText: 'Finca (g)'))),
              const SizedBox(width: 8),
              Expanded(child: TextFormField(controller: _gPlanta1, keyboardType: numero, validator: positivo,
                  decoration: const InputDecoration(labelText: 'Planta 1 (g)'))),
              const SizedBox(width: 8),
              Expanded(child: TextFormField(controller: _gPlanta2, keyboardType: numero, validator: positivo,
                  decoration: const InputDecoration(labelText: 'Planta 2 (g)'))),
            ]),
            const Rotulo('Incidencias'),
            TextFormField(controller: _incidencias, maxLines: 3,
                decoration: const InputDecoration(labelText: 'Lo que haya que dejar por escrito')),
            const Rotulo('Ubicación'),
            Row(children: [
              Expanded(
                child: Text(inf.latitud == null
                    ? 'Sin ubicación todavía'
                    : '${inf.latitud!.toStringAsFixed(5)}, ${inf.longitud!.toStringAsFixed(5)}',
                    style: const TextStyle(color: Marca.tintaSuave)),
              ),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(minimumSize: const Size(0, 44)),
                onPressed: _buscandoGps ? null : _tomarGps,
                icon: _buscandoGps
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.my_location),
                label: const Text('Tomar GPS'),
              ),
            ]),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: () => _guardar(enviar: true),
              icon: const Icon(Icons.save),
              label: const Text('Guardar y enviar cuando haya internet'),
            ),
            const SizedBox(height: 8),
            TextButton(onPressed: () => _guardar(enviar: false), child: const Text('Guardar solo como borrador')),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}

class _FilaLinea extends StatelessWidget {
  final LineaClasificacion linea;
  final VoidCallback onBorrar;
  final VoidCallback onCambio;
  const _FilaLinea({required this.linea, required this.onBorrar, required this.onCambio});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(children: [
        SizedBox(
          width: 90,
          child: DropdownButtonFormField<String>(
            initialValue: linea.clase,
            decoration: const InputDecoration(labelText: 'Clase'),
            items: const [
              DropdownMenuItem(value: 'a', child: Text('A')),
              DropdownMenuItem(value: 'b', child: Text('B')),
              DropdownMenuItem(value: 'c', child: Text('C')),
            ],
            onChanged: (v) {
              linea.clase = v ?? linea.clase;
              onCambio();
            },
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: TextFormField(
            initialValue: linea.talla,
            decoration: const InputDecoration(labelText: 'Talla', hintText: '21/25'),
            onChanged: (v) => linea.talla = v.trim(),
            validator: (v) => (v == null || v.trim().isEmpty) ? 'Falta' : null,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: TextFormField(
            initialValue: linea.libras == 0 ? '' : linea.libras.toString(),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Libras'),
            onChanged: (v) => linea.libras = double.tryParse(v.replaceAll(',', '.')) ?? 0,
            validator: (v) => (double.tryParse((v ?? '').replaceAll(',', '.')) ?? 0) <= 0 ? '> 0' : null,
          ),
        ),
        IconButton(onPressed: onBorrar, icon: const Icon(Icons.delete_outline)),
      ]),
    );
  }
}

class _FilaConteo extends StatelessWidget {
  final Conteo conteo;
  final VoidCallback onBorrar;
  const _FilaConteo({required this.conteo, required this.onBorrar});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(children: [
        SizedBox(
          width: 130,
          child: TextFormField(
            initialValue: conteo.valor == 0 ? '' : conteo.valor.toString(),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Camarones/lb'),
            onChanged: (v) => conteo.valor = double.tryParse(v.replaceAll(',', '.')) ?? 0,
            validator: (v) => (double.tryParse((v ?? '').replaceAll(',', '.')) ?? 0) <= 0 ? '> 0' : null,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: TextFormField(
            initialValue: conteo.nota ?? '',
            decoration: const InputDecoration(labelText: 'Nota'),
            onChanged: (v) => conteo.nota = v,
          ),
        ),
        IconButton(onPressed: onBorrar, icon: const Icon(Icons.delete_outline)),
      ]),
    );
  }
}
