import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/cuerpos.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
import 'comunes/widgets.dart';

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
  bool _noEsta = false;
  bool _buscandoGps = false;
  bool _guardando = false;

  // Controladores de texto (uno por campo numérico/texto).
  final _planta = TextEditingController();
  final _enviadas = TextEditingController();
  final _enPlanta = TextEditingController();
  final _basura = TextEditingController();
  final _ppm = TextEditingController();
  final _ppmNotas = TextEditingController();
  final _saborNotas = TextEditingController();
  final _gFinca = TextEditingController();
  final _gPlanta1 = TextEditingController();
  final _gPlanta2 = TextEditingController();
  final _incidencias = TextEditingController();
  final _larvaCantidad = TextEditingController();
  final _larvaSupervivencia = TextEditingController();
  final _larvaTamano = TextEditingController();
  final _larvaNotas = TextEditingController();

  List<TextEditingController> get _controladores => [
        _planta, _enviadas, _enPlanta, _basura, _ppm, _ppmNotas, _saborNotas, _gFinca, _gPlanta1, _gPlanta2, _incidencias,
        _larvaCantidad, _larvaSupervivencia, _larvaTamano, _larvaNotas,
      ];

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  @override
  void dispose() {
    for (final c in _controladores) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _cargar() async {
    final sinc = context.read<Sincronizador>();
    final v = sinc.bandeja.where((e) => e.id == widget.id).firstOrNull;
    if (v == null) {
      if (mounted) setState(() => _noEsta = true);
      return;
    }
    final inf = await sinc.informeDe(v);
    if (!mounted) return;
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
    _larvaCantidad.text = t(inf.larvaCantidad);
    _larvaSupervivencia.text = t(inf.larvaSupervivencia);
    _larvaTamano.text = t(inf.larvaTamanoMg);
    _larvaNotas.text = inf.larvaSanidadNotas ?? '';
    setState(() {
      _v = v;
      _inf = inf;
    });
  }

  void _volcar(InformeCampo inf) {
    inf.plantaNombre = _planta.text.trim().isEmpty ? null : _planta.text.trim();
    inf.librasEnviadas = numeroEscrito(_enviadas.text);
    inf.librasPlanta = numeroEscrito(_enPlanta.text);
    inf.librasBasura = numeroEscrito(_basura.text);
    inf.metabisulfitoPpm = numeroEscrito(_ppm.text);
    inf.metabisulfitoNotas = _ppmNotas.text.trim();
    inf.saborNotas = _saborNotas.text.trim();
    inf.gramosFinca = numeroEscrito(_gFinca.text);
    inf.gramosPlanta1 = numeroEscrito(_gPlanta1.text);
    inf.gramosPlanta2 = numeroEscrito(_gPlanta2.text);
    inf.incidencias = _incidencias.text.trim();
    inf.larvaCantidad = numeroEscrito(_larvaCantidad.text);
    inf.larvaSupervivencia = numeroEscrito(_larvaSupervivencia.text);
    inf.larvaTamanoMg = numeroEscrito(_larvaTamano.text);
    inf.larvaSanidadNotas = _larvaNotas.text.trim();
  }

  Future<void> _guardar({required bool enviar}) async {
    final inf = _inf;
    final v = _v;
    if (inf == null || v == null || _guardando) return;
    if (!(_form.currentState?.validate() ?? false)) {
      _aviso('Revisa los campos marcados en rojo.');
      return;
    }
    _volcar(inf);
    final clases = inf.lineas.map((l) => l.clase).toList();
    if (clases.length != clases.toSet().length) {
      _aviso('Solo puede haber una fila por clase (A, B, C).');
      return;
    }
    setState(() => _guardando = true);
    try {
      await context.read<Sincronizador>().guardarInforme(v, inf, enviar: enviar);
    } catch (_) {
      if (mounted) _aviso('No se pudo guardar en el teléfono. Inténtalo otra vez.');
      return;
    } finally {
      if (mounted) setState(() => _guardando = false);
    }
    if (!mounted) return;
    _aviso(enviar ? 'Informe guardado; se enviará cuando haya internet' : 'Borrador guardado en el teléfono');
    Navigator.pop(context);
  }

  void _aviso(String texto) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(texto)));
  }

  Future<void> _tomarGps() async {
    if (_buscandoGps) return;
    setState(() => _buscandoGps = true);
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        _aviso('La ubicación del teléfono está apagada. Enciéndela e inténtalo otra vez.');
        return;
      }
      var permiso = await Geolocator.checkPermission();
      if (permiso == LocationPermission.denied) permiso = await Geolocator.requestPermission();
      if (permiso == LocationPermission.denied || permiso == LocationPermission.deniedForever) {
        _aviso('Sin permiso de ubicación. Actívalo en los ajustes del teléfono.');
        return;
      }
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high, timeLimit: Duration(seconds: 25)),
      );
      if (!mounted) return;
      setState(() {
        _inf?.latitud = pos.latitude;
        _inf?.longitud = pos.longitude;
      });
    } catch (_) {
      _aviso('No se pudo leer el GPS. Sal a cielo abierto e inténtalo otra vez.');
    } finally {
      if (mounted) setState(() => _buscandoGps = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_noEsta) {
      return Scaffold(
        appBar: AppBar(title: const Text('Informe de campo')),
        body: const VacioLista(texto: 'Esta verificación ya no está en tu bandeja.'),
      );
    }
    final inf = _inf;
    final v = _v;
    if (inf == null || v == null) {
      return Scaffold(appBar: AppBar(), body: const Center(child: CircularProgressIndicator()));
    }
    final numero = const TextInputType.numberWithOptions(decimal: true);
    String? positivo(String? s) {
      if (s == null || s.trim().isEmpty) return null;
      final d = numeroEscrito(s);
      return d == null || d < 0 ? 'Escribe un número' : null;
    }

    String? porcentaje(String? s) {
      if (s == null || s.trim().isEmpty) return null;
      final d = numeroEscrito(s);
      return d == null || d < 0 || d > 100 ? 'Entre 0 y 100' : null;
    }

    // A la larva (scope larvae) el servidor le pide cantidad, supervivencia,
    // tamaño y sanidad; no tallas, metabisulfito ni sabor.
    final larva = v.esLarva;
    return Scaffold(
      appBar: AppBar(title: Text('Informe · ${v.referencia}')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
          children: [
            if (larva) ...[
              const Rotulo('Larva'),
              TextFormField(controller: _planta, decoration: const InputDecoration(labelText: 'Laboratorio / lugar de la revisión')),
              const SizedBox(height: 10),
              TextFormField(controller: _larvaCantidad, keyboardType: numero, validator: positivo,
                  decoration: const InputDecoration(labelText: 'Cantidad verificada (larvas)')),
              const SizedBox(height: 10),
              Row(children: [
                Expanded(child: TextFormField(controller: _larvaSupervivencia, keyboardType: numero, validator: porcentaje,
                    decoration: const InputDecoration(labelText: 'Supervivencia (%)'))),
                const SizedBox(width: 10),
                Expanded(child: TextFormField(controller: _larvaTamano, keyboardType: numero, validator: positivo,
                    decoration: const InputDecoration(labelText: 'Tamaño promedio (mg)'))),
              ]),
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                initialValue: inf.larvaSanidad,
                decoration: const InputDecoration(labelText: 'Estado sanitario'),
                items: const [
                  DropdownMenuItem(value: 'excellent', child: Text('Excelente')),
                  DropdownMenuItem(value: 'good', child: Text('Bueno')),
                  DropdownMenuItem(value: 'acceptable', child: Text('Aceptable')),
                  DropdownMenuItem(value: 'rejected', child: Text('Rechazado')),
                ],
                onChanged: (v) => setState(() => inf.larvaSanidad = v),
              ),
              const SizedBox(height: 10),
              TextFormField(controller: _larvaNotas, maxLines: 2, decoration: const InputDecoration(labelText: 'Notas de sanidad')),
            ],
            if (!larva) ...[
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
            for (final linea in inf.lineas)
              _FilaLinea(
                key: ObjectKey(linea),
                linea: linea,
                onBorrar: () => setState(() => inf.lineas.remove(linea)),
                onCambio: () => setState(() {}),
              ),
            OutlinedButton.icon(
              onPressed: inf.lineas.length >= 3
                  ? null
                  : () => setState(() {
                        final usadas = inf.lineas.map((l) => l.clase).toSet();
                        final libre = ['a', 'b', 'c'].where((c) => !usadas.contains(c)).firstOrNull ?? 'a';
                        inf.lineas.add(LineaClasificacion(clase: libre, talla: '', libras: 0));
                      }),
              icon: const Icon(Icons.add),
              label: const Text('Agregar clase'),
            ),
            const Rotulo('Conteos (camarones por libra)'),
            for (final conteo in inf.conteos)
              _FilaConteo(
                key: ObjectKey(conteo),
                conteo: conteo,
                onBorrar: () => setState(() => inf.conteos.remove(conteo)),
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
            ],
            const Rotulo('Incidencias'),
            TextFormField(controller: _incidencias, maxLines: 3,
                decoration: const InputDecoration(labelText: 'Lo que haya que dejar por escrito')),
            const Rotulo('Ubicación'),
            Row(children: [
              Expanded(
                child: Text(
                    inf.latitud == null || inf.longitud == null
                        ? 'Sin ubicación todavía'
                        : '${inf.latitud!.toStringAsFixed(5)}, ${inf.longitud!.toStringAsFixed(5)}',
                    style: const TextStyle(color: Marca.tintaSuave)),
              ),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
                onPressed: _buscandoGps ? null : _tomarGps,
                icon: _buscandoGps
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.my_location),
                label: const Text('Tomar GPS'),
              ),
            ]),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _guardando ? null : () => _guardar(enviar: true),
              icon: _guardando
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.save),
              label: Text(_guardando ? 'Guardando…' : 'Guardar y enviar cuando haya internet'),
            ),
            const SizedBox(height: 8),
            TextButton(onPressed: _guardando ? null : () => _guardar(enviar: false), child: const Text('Guardar solo como borrador')),
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
  const _FilaLinea({super.key, required this.linea, required this.onBorrar, required this.onCambio});

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
            validator: (v) => (v == null || v.trim().isEmpty) ? 'Falta la talla' : null,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: TextFormField(
            initialValue: linea.libras == 0 ? '' : linea.libras.toString(),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Libras'),
            onChanged: (v) => linea.libras = numeroEscrito(v) ?? 0,
            validator: (v) => (numeroEscrito(v) ?? 0) <= 0 ? 'Más de 0' : null,
          ),
        ),
        IconButton(tooltip: 'Quitar esta clase', onPressed: onBorrar, icon: const Icon(Icons.delete_outline)),
      ]),
    );
  }
}

class _FilaConteo extends StatelessWidget {
  final Conteo conteo;
  final VoidCallback onBorrar;
  const _FilaConteo({super.key, required this.conteo, required this.onBorrar});

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
            onChanged: (v) => conteo.valor = numeroEscrito(v) ?? 0,
            validator: (v) => (numeroEscrito(v) ?? 0) <= 0 ? 'Más de 0' : null,
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
        IconButton(tooltip: 'Quitar este conteo', onPressed: onBorrar, icon: const Icon(Icons.delete_outline)),
      ]),
    );
  }
}
