import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sesion.dart';
import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firmador.dart';
import '../tema.dart';
import 'comunes/widgets.dart';
import 'pantalla_asignar_tecnico.dart';
import 'pantalla_fotos.dart';
import 'pantalla_informe.dart';
import 'pantalla_veredicto.dart';

/// Detalle de una verificación y las acciones del técnico, en el orden del
/// trabajo real: iniciar campo → llegada del camión → informe → veredicto.
class PantallaVerificacion extends StatelessWidget {
  final String id;
  const PantallaVerificacion({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    final v = sinc.bandeja.where((e) => e.id == id).firstOrNull;
    if (v == null) {
      return Scaffold(appBar: AppBar(), body: const Center(child: Text('Esta verificación ya no está en tu bandeja.')));
    }
    final fmt = DateFormat('dd/MM/yyyy HH:mm');
    final pesos = (v.crudo['weights'] ?? {}) as Map;
    final clasif = (v.crudo['classification'] ?? {}) as Map;
    final larva = v.crudo['larvae'] is Map ? v.crudo['larvae'] as Map : const {};
    final hayInforme = _hay(pesos['sent_lb']) ||
        _hay(pesos['plant_lb']) ||
        ((clasif['lines'] as List?)?.isNotEmpty ?? false) ||
        _hay(larva['qty_verified']);

    return Scaffold(
      appBar: AppBar(title: Text(v.referencia)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(children: [
            Expanded(child: Text(v.producto.nombre, style: Theme.of(context).textTheme.titleMedium)),
            const SizedBox(width: 10),
            Insignia(estado: v.estado, texto: v.estadoEtiqueta),
          ]),
          const Rotulo('Lote'),
          SeccionDatos(filas: [
            ('Camaronera', v.vendedor.nombre),
            ('Compra de', v.comprador.nombre),
            if (v.instalacion != null) ('Finca', v.instalacion!),
            if (v.piscina != null) ('Piscina', v.piscina!),
            if (v.loteCodigo != null) ('Código de lote', v.loteCodigo!),
            if (v.soyVerificadora) ('Técnico', v.tecnico?.nombre ?? 'Sin asignar'),
          ]),
          if (v.despachoId != null) ...[
            const Rotulo('Camión'),
            SeccionDatos(filas: [
              ('Llegada prevista', v.despachoEta == null ? 'Sin programar' : fmt.format(v.despachoEta!)),
            ]),
          ],
          const Rotulo('Informe en el servidor'),
          if (!hayInforme)
            Tarjeta(
              child: Text('Todavía no se ha cargado ningún dato de campo.', style: TextStyle(color: context.colores.tintaSuave)),
            )
          else if (v.esLarva)
            SeccionDatos(filas: [
              ('Cantidad verificada', _hay(larva['qty_verified']) ? NumberFormat('#,##0').format(larva['qty_verified']) : '—'),
              ('Supervivencia', _pct(larva['survival_rate'])),
              ('Tamaño promedio', _hay(larva['avg_size_mg']) ? '${NumberFormat('#,##0.##').format(larva['avg_size_mg'])} mg' : '—'),
              ('Estado sanitario', _sanidad(larva['health_status'])),
              ('Fotos', '${v.crudo['photo_count'] ?? 0}'),
            ])
          else
            SeccionDatos(filas: [
              ('Libras enviadas', _lb(pesos['sent_lb'])),
              ('Libras en planta', _lb(pesos['plant_lb'])),
              ('Basura', _lb(pesos['trash_lb'])),
              ('Rendimiento', _pct(clasif['yield_pct'])),
              ('Clase A', _pct(clasif['yield_class_a_pct'])),
              ('Clasificación', '${(clasif['lines'] as List?)?.length ?? 0} líneas'),
              ('Conteos', '${(v.crudo['counts'] as List?)?.length ?? 0}'),
              ('Fotos', '${v.crudo['photo_count'] ?? 0}'),
            ]),
          const Rotulo('Acciones'),
          if (soloLectura) ...[
            // Modo supervisor: ni asignar, ni campo, ni informe, ni fotos, ni veredicto.
            const AvisoSoloLectura(),
            if (v.cerrada) const SizedBox(height: 10),
          ] else ...[
          if (v.soyVerificadora && !v.cerrada) ...[
            OutlinedButton.icon(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaAsignarTecnico(id: v.id))),
              icon: const Icon(Icons.engineering_outlined),
              label: Text(v.tecnicoAsignado ? 'Cambiar técnico' : 'Asignar técnico'),
            ),
            const SizedBox(height: 10),
          ],
          if (v.esperaTecnico && !v.soyVerificadora)
            Tarjeta(
              child: Text('Todavía no tiene técnico asignado. Cuando la verificadora lo asigne podrás iniciar el trabajo de campo.',
                  style: TextStyle(color: context.colores.tintaSuave)),
            ),
          if (v.esperaTecnico && v.soyVerificadora)
            Tarjeta(
              child: Text('Asigna un técnico para poder iniciar el trabajo de campo.', style: TextStyle(color: context.colores.tintaSuave)),
            ),
          if (v.esperaTecnico) const SizedBox(height: 10),
          if (v.puedeIniciarCampo) ...[
            BotonAccion(
              onPressed: () async {
                try {
                  await sinc.iniciarCampo(v);
                } on ErrorSeguridad catch (e) {
                  if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje)));
                  return;
                }
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Trabajo de campo iniciado')));
                }
              },
              icono: Icons.play_arrow_rounded,
              texto: 'Iniciar trabajo de campo',
              textoOcupado: 'Iniciando…',
            ),
            const SizedBox(height: 10),
          ],
          if (v.despachoId != null && !v.cerrada) ...[
            BotonAccion(
              relleno: false,
              onPressed: () => _registrarLlegada(context, sinc, v),
              icono: Icons.local_shipping_outlined,
              texto: 'Registrar llegada del camión',
            ),
            const SizedBox(height: 10),
          ],
          if (!v.cerrada) ...[
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: v.enCampo ? context.colores.acento : context.colores.marca),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInforme(id: v.id))),
              icon: const Icon(Icons.edit_note_rounded),
              label: const Text('Informe de campo'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaFotos(id: v.id))),
              icon: const Icon(Icons.photo_camera_outlined),
              label: const Text('Fotos de campo'),
            ),
            const SizedBox(height: 10),
          ],
          if (v.enCampo || v.estado == 'done')
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: context.colores.exito),
              onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVeredicto(id: v.id))),
              icon: const Icon(Icons.gavel_rounded),
              label: const Text('Dar veredicto'),
            ),
          ],
          if (v.cerrada)
            Tarjeta(
              child: Text('Esta verificación está cerrada. Puedes consultarla, pero ya no se edita.',
                  style: TextStyle(color: context.colores.tintaSuave)),
            ),
        ],
      ),
    );
  }

  static bool _hay(dynamic v) => v != null && v is num && v > 0;
  static String _sanidad(dynamic v) => switch (v) {
        'excellent' => 'Excelente',
        'good' => 'Bueno',
        'acceptable' => 'Aceptable',
        'rejected' => 'Rechazado',
        _ => '—',
      };
  static String _lb(dynamic v) => !_hay(v) ? '—' : '${NumberFormat('#,##0.##').format(v)} lb';
  static String _pct(dynamic v) => !_hay(v) ? '—' : '${NumberFormat('#,##0.#').format(v)} %';

  Future<void> _registrarLlegada(BuildContext context, Sincronizador sinc, Verificacion v) async {
    final ahora = DateTime.now();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Llegada del camión'),
        content: Text('Se registrará la llegada ahora, ${DateFormat('dd/MM/yyyy').format(ahora)} a las ${DateFormat('HH:mm').format(ahora)}.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Registrar')),
        ],
      ),
    );
    if (ok == true) {
      final bool registrada;
      try {
        registrada = await sinc.registrarLlegadaCamion(v, ahora);
      } on ErrorSeguridad catch (e) {
        if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje)));
        return;
      }
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(registrada ? 'Llegada registrada' : 'Esta verificación no tiene despacho asociado.')));
      }
    }
  }
}
