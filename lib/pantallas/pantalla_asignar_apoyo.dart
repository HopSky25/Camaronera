import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/api_cliente.dart';
import '../datos/sesion.dart';
import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
import 'comunes/widgets.dart';
import 'pantalla_asignar_tecnico.dart';

/// Equipo de campo de una verificación (administrador de la verificadora):
/// el responsable y los técnicos de apoyo. Los de apoyo inician el campo,
/// cargan el informe y toman fotos, pero no firman el veredicto.
/// `POST /verifications/{id}:support-technicians {"add"|"remove": [...]}`.
class PantallaAsignarApoyo extends StatefulWidget {
  final String id;
  const PantallaAsignarApoyo({super.key, required this.id});
  @override
  State<PantallaAsignarApoyo> createState() => _PantallaAsignarApoyoState();
}

class _PantallaAsignarApoyoState extends State<PantallaAsignarApoyo> {
  bool _cargando = true;
  bool _ocupado = false;
  List<Referencia> _tecnicos = [];
  String? _avisoLista;

  @override
  void initState() {
    super.initState();
    _cargarTecnicos();
  }

  Future<void> _cargarTecnicos() async {
    final api = context.read<Sesion>().apiOperativa;
    final sinc = context.read<Sincronizador>();
    var lista = <Referencia>[];
    if (api == null) {
      _avisoLista = 'La sesión caducó: se muestran los técnicos ya vistos en la bandeja.';
    } else {
      try {
        final resp = await api.get('/me/technicians');
        final datos = resp['data'] is List ? resp['data'] as List : const [];
        lista = [
          for (final t in datos.whereType<Map>())
            if (t['active'] != false && (t['id'] is String)) Referencia.desde(t),
        ];
      } catch (e) {
        _avisoLista = e is SinConexion
            ? 'Sin internet: se muestran los técnicos ya vistos en la bandeja.'
            : 'No se pudo bajar la lista de técnicos (${mensajeDeError(e)}).';
      }
    }
    if (lista.isEmpty) lista = sinc.tecnicosConocidos();
    if (!mounted) return;
    setState(() {
      _tecnicos = lista;
      _cargando = false;
    });
  }

  Future<void> _cambiar(Verificacion v, {String? sumar, String? quitar}) async {
    if (_ocupado) return;
    setState(() => _ocupado = true);
    final r = await context.read<Sincronizador>().cambiarEquipoApoyo(
          v,
          sumar: [?sumar],
          quitar: [?quitar],
        );
    if (!mounted) return;
    setState(() => _ocupado = false);
    avisarResultado(context, r, hecho: sumar != null ? 'Técnico de apoyo sumado' : 'Técnico de apoyo quitado');
  }

  @override
  Widget build(BuildContext context) {
    final v = context.watch<Sincronizador>().bandeja.where((e) => e.id == widget.id).firstOrNull;
    if (v == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Equipo de campo')),
        body: const VacioLista(texto: 'Esta verificación ya no está en tu bandeja.'),
      );
    }
    final c = context.colores;
    final apoyo = v.tecnicosApoyo;
    final enEquipo = {v.tecnico?.id, for (final t in apoyo) t.id};
    final disponibles = _tecnicos.where((t) => !enEquipo.contains(t.id)).toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Equipo de campo')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          const CajaAviso(
            tipo: TipoAviso.info,
            icono: Icons.info_outline,
            texto: 'Todos los técnicos del equipo cargan muestreo, fotos, sabor y notas. Solo el responsable o la empresa '
                'firman el veredicto.',
          ),
          const Rotulo('Responsable'),
          Tarjeta(
            child: Row(children: [
              CajaIcono(Icons.star_rounded, fondo: c.advertenciaSuave, color: c.advertenciaTexto),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(v.tecnico?.nombre ?? 'Sin técnico responsable', style: TextStyle(fontWeight: FontWeight.w700, color: c.titulo)),
                  Text(v.tecnico == null ? 'Asígnalo primero: el apoyo se suma después.' : 'Dirige la inspección y firma el veredicto',
                      style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                ]),
              ),
              TextButton(
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaAsignarTecnico(id: v.id))),
                child: Text(v.tecnico == null ? 'Asignar' : 'Cambiar'),
              ),
            ]),
          ),
          Rotulo('De apoyo (${apoyo.length})'),
          if (apoyo.isEmpty)
            Tarjeta(child: Text('Nadie va de apoyo todavía.', style: TextStyle(color: c.tintaSuave)))
          else
            Tarjeta(
              padding: EdgeInsets.zero,
              child: Column(children: [
                for (final t in apoyo)
                  ListTile(
                    leading: const Icon(Icons.person_outline),
                    title: Text(t.nombre, style: const TextStyle(fontWeight: FontWeight.w600)),
                    subtitle: const Text('Carga datos de campo, no firma'),
                    trailing: TextButton(
                      style: TextButton.styleFrom(foregroundColor: c.peligroTexto),
                      onPressed: _ocupado || t.id == null ? null : () => _cambiar(v, quitar: t.id),
                      child: const Text('Quitar'),
                    ),
                  ),
              ]),
            ),
          if (v.tecnico != null) ...[
            const Rotulo('Sumar técnico de apoyo'),
            if (_avisoLista != null) ...[
              CajaAviso(tipo: TipoAviso.advertencia, icono: Icons.wifi_off_rounded, texto: _avisoLista!),
              const SizedBox(height: 8),
            ],
            if (_cargando)
              const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator()))
            else if (disponibles.isEmpty)
              Tarjeta(
                child: Text('No hay más técnicos activos para sumar. Dalos de alta en el portal (Mi equipo).',
                    style: TextStyle(color: c.tintaSuave)),
              )
            else
              Tarjeta(
                padding: EdgeInsets.zero,
                child: Column(children: [
                  for (final t in disponibles)
                    ListTile(
                      leading: const Icon(Icons.person_add_alt_1_outlined),
                      title: Text(t.nombre),
                      trailing: TextButton(
                        onPressed: _ocupado ? null : () => _cambiar(v, sumar: t.id),
                        child: const Text('Sumar'),
                      ),
                    ),
                ]),
              ),
          ],
        ],
      ),
    );
  }
}
