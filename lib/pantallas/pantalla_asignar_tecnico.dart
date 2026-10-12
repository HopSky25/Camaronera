import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/api_cliente.dart';
import '../datos/sesion.dart';
import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
import 'comunes/widgets.dart';

/// La empresa verificadora asigna el técnico de campo:
/// `POST /verifications/{id}:assign {"technician": "<uuid>"}`.
///
/// La lista de técnicos sale de `GET /me/technicians` → `[{id, name, role,
/// active}]`. Si el servidor aún no la tiene (o no hay señal), se ofrecen los
/// técnicos que ya aparecen asignados en la bandeja de la empresa y un campo
/// para pegar el identificador.
class PantallaAsignarTecnico extends StatefulWidget {
  final String id;
  const PantallaAsignarTecnico({super.key, required this.id});
  @override
  State<PantallaAsignarTecnico> createState() => _PantallaAsignarTecnicoState();
}

class _PantallaAsignarTecnicoState extends State<PantallaAsignarTecnico> {
  String? _elegido;
  final _uuid = TextEditingController();
  bool _ocupado = false;
  bool _cargando = true;
  List<Referencia> _tecnicos = [];
  String? _avisoLista;

  @override
  void initState() {
    super.initState();
    _cargarTecnicos();
  }

  @override
  void dispose() {
    _uuid.dispose();
    super.dispose();
  }

  Future<void> _cargarTecnicos() async {
    final sinc = context.read<Sincronizador>();
    final api = context.read<Sesion>().api;
    List<Referencia> lista = [];
    try {
      final resp = await api!.get('/me/technicians');
      final datos = (resp['data'] ?? []) as List;
      lista = datos
          .whereType<Map>()
          .where((t) => t['active'] != false)
          .map((t) => Referencia(id: t['id'] as String?, nombre: (t['name'] ?? '') as String))
          .where((t) => t.id != null)
          .toList();
    } catch (e) {
      _avisoLista = e is SinConexion
          ? 'Sin internet: se muestran los técnicos ya vistos en la bandeja.'
          : 'No se pudo bajar la lista de técnicos (${mensajeDeError(e)}). Se muestran los ya vistos en la bandeja.';
    }
    if (lista.isEmpty) lista = sinc.tecnicosConocidos();
    if (!mounted) return;
    setState(() {
      _tecnicos = lista;
      _cargando = false;
    });
  }

  Future<void> _asignar() async {
    final id = (_elegido ?? _uuid.text).trim();
    if (id.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Elige un técnico o pega su identificador.')));
      return;
    }
    setState(() => _ocupado = true);
    final sinc = context.read<Sincronizador>();
    final v = sinc.bandeja.firstWhere((e) => e.id == widget.id);
    final r = await sinc.asignarTecnico(v, id);
    if (!mounted) return;
    setState(() => _ocupado = false);
    avisarResultado(context, r, hecho: 'Técnico asignado');
    if (r.error == null) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final v = sinc.bandeja.where((e) => e.id == widget.id).firstOrNull;
    return Scaffold(
      appBar: AppBar(title: const Text('Asignar técnico')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
        children: [
          if (v != null) ...[
            const Rotulo('Verificación'),
            SeccionDatos(filas: [
              ('Referencia', v.referencia),
              ('Lote', v.producto.nombre),
              ('Técnico actual', v.tecnico?.nombre ?? 'Sin asignar'),
            ]),
          ],
          const Rotulo('Técnicos de la empresa'),
          if (_avisoLista != null) ...[
            Text(_avisoLista!, style: const TextStyle(fontSize: 13, color: Marca.ambar)),
            const SizedBox(height: 8),
          ],
          if (_cargando)
            const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator()))
          else if (_tecnicos.isEmpty)
            const Tarjeta(
              child: Text('No hay técnicos disponibles. Pega abajo el identificador del técnico '
                  '(lo ves en el portal, en Mi equipo).', style: TextStyle(color: Marca.tintaSuave)),
            )
          else
            Tarjeta(
              padding: EdgeInsets.zero,
              child: RadioGroup<String>(
                groupValue: _elegido,
                onChanged: (id) => setState(() => _elegido = id),
                child: Column(children: [
                  for (final t in _tecnicos)
                    RadioListTile<String>(
                      value: t.id!,
                      title: Text(t.nombre, style: const TextStyle(fontWeight: FontWeight.w600)),
                    ),
                ]),
              ),
            ),
          const Rotulo('Otro técnico'),
          TextField(
            controller: _uuid,
            autocorrect: false,
            onChanged: (_) => setState(() => _elegido = null),
            decoration: const InputDecoration(
              labelText: 'Identificador del técnico',
              helperText: 'Solo si no aparece en la lista: pega su identificador (portal, Mi equipo).',
              prefixIcon: Icon(Icons.badge_outlined),
            ),
          ),
          const SizedBox(height: 12),
          const AvisoWeb(texto: 'Dar de alta o quitar técnicos se hace desde el portal web (Mi equipo).'),
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: _ocupado ? null : _asignar,
            icon: const Icon(Icons.person_add_alt_1_outlined),
            label: Text(_ocupado ? 'Enviando…' : 'Asignar'),
          ),
        ],
      ),
    );
  }
}
