import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/operacion_pendiente.dart';
import '../tema.dart';
import 'comunes/formato.dart';
import 'comunes/widgets.dart';

/// Lo que todavía no se ha enviado al servidor, en el orden en que se hizo.
/// Lo rechazado por el servidor (dato inválido, sin permiso...) se queda a la
/// vista con el motivo y se puede descartar; lo que dependa de ese mismo
/// registro espera.
class PantallaPendientes extends StatelessWidget {
  const PantallaPendientes({super.key});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final ops = sinc.cola;
    final conError = ops.where((o) => o.conError).length;
    return Scaffold(
      appBar: AppBar(title: const Text('Pendientes de enviar')),
      body: ops.isEmpty
          ? const VacioLista(texto: 'Todo está enviado al servidor.', icono: Icons.cloud_done_outlined)
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
              children: [
                if (!sinc.hayRed)
                  const Padding(
                    padding: EdgeInsets.only(bottom: 10),
                    child: AvisoCache(),
                  ),
                if (conError > 0) ...[
                  CajaError(
                    texto: conError == 1
                        ? 'El servidor rechazó 1 envío. No se reintenta solo: corrige el dato desde la pantalla donde lo capturaste o descártalo.'
                        : 'El servidor rechazó $conError envíos. No se reintentan solos: corrige el dato desde la pantalla donde lo capturaste o descártalos.',
                  ),
                  const SizedBox(height: 10),
                ],
                ..._filas(context, sinc, ops),
              ],
            ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: sinc.sincronizando || ops.isEmpty ? null : sinc.sincronizar,
        icon: sinc.sincronizando
            ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
            : const Icon(Icons.sync),
        label: Text(sinc.sincronizando ? 'Enviando…' : 'Enviar ahora'),
      ),
    );
  }

  List<Widget> _filas(BuildContext context, Sincronizador sinc, List<OperacionPendiente> ops) {
    final recursosConError = <String>{};
    final filas = <Widget>[];
    for (final op in ops) {
      final bloqueada = !op.conError && recursosConError.contains(op.recursoId);
      if (op.conError) recursosConError.add(op.recursoId);
      filas.add(Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: _FilaPendiente(op: op, bloqueada: bloqueada, onDescartar: () => _descartar(context, sinc, op)),
      ));
    }
    return filas;
  }

  Future<void> _descartar(BuildContext context, Sincronizador sinc, OperacionPendiente op) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Descartar envío'),
        content: Text('El servidor no aceptó "${op.descripcion}". Si lo descartas, tendrás que volver a capturarlo. ¿Descartar?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('No')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Marca.rojo),
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Descartar'),
          ),
        ],
      ),
    );
    if (ok == true && op.idLocal != null) await sinc.descartarPendiente(op.idLocal!);
  }
}

class _FilaPendiente extends StatelessWidget {
  final OperacionPendiente op;
  final bool bloqueada;
  final VoidCallback onDescartar;
  const _FilaPendiente({required this.op, required this.bloqueada, required this.onDescartar});

  @override
  Widget build(BuildContext context) {
    final (icono, color, estado) = op.conError
        ? (Icons.error_outline, Marca.rojo, 'El servidor no lo aceptó: ${op.ultimoError}')
        : bloqueada
            ? (Icons.hourglass_top_outlined, Marca.ambar, 'Espera: antes hay un envío rechazado de este mismo registro.')
            : (Icons.schedule, Marca.brand, 'Se enviará cuando haya internet.');
    return Tarjeta(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(icono, color: color),
          const SizedBox(width: 10),
          Expanded(child: Text(op.descripcion, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: Marca.navy))),
          Text(fechaHoraDe(op.creadaEn), style: const TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
        ]),
        const SizedBox(height: 6),
        Text(estado, style: TextStyle(fontSize: 14, color: op.conError ? Marca.rojo : Marca.tintaSuave)),
        if (op.conError) ...[
          const SizedBox(height: 4),
          Text('Intentos: ${op.intentos}', style: const TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              style: TextButton.styleFrom(foregroundColor: Marca.rojo),
              onPressed: onDescartar,
              icon: const Icon(Icons.delete_outline),
              label: const Text('Descartar'),
            ),
          ),
        ],
      ]),
    );
  }
}
