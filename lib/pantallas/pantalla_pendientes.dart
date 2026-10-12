import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sesion.dart';
import '../datos/sincronizador.dart';
import '../modelos/operacion_pendiente.dart';
import '../seguridad/firmador.dart';
import '../tema.dart';
import 'comunes/formato.dart';
import 'comunes/widgets.dart';

/// Lo que todavía no se ha enviado al servidor, en el orden en que se hizo.
/// Lo rechazado por el servidor (dato inválido, sin permiso...) se queda a la
/// vista con el motivo y se puede descartar; lo que dependa de ese mismo
/// registro espera.
///
/// Lo que necesita la huella (operaciones de la versión anterior sin firma,
/// firmas de más de 7 días o rechazadas por el servidor) tiene el botón
/// "Confirmar con huella": se firma de nuevo el mismo cuerpo, con la misma
/// Idempotency-Key y la hora actual.
class PantallaPendientes extends StatelessWidget {
  const PantallaPendientes({super.key});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    context.watch<Sesion>(); // el estado de la firma depende del dispositivo
    final ops = sinc.cola;
    final porConfirmar = sinc.porConfirmar;
    final conError = ops.where((o) => o.conError && !sinc.necesitaConfirmar(o)).length;
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
                if (porConfirmar.isNotEmpty) ...[
                  Tarjeta(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(children: [
                        const Icon(Icons.fingerprint, color: Marca.ambar),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            porConfirmar.length == 1
                                ? '1 envío espera tu confirmación con huella o PIN.'
                                : '${porConfirmar.length} envíos esperan tu confirmación con huella o PIN.',
                            style: const TextStyle(fontWeight: FontWeight.w700, color: Marca.navy),
                          ),
                        ),
                      ]),
                      if (porConfirmar.length > 1)
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton.icon(
                            onPressed: () => _confirmarTodas(context, sinc, porConfirmar),
                            icon: const Icon(Icons.done_all),
                            label: const Text('Confirmar todos'),
                          ),
                        ),
                    ]),
                  ),
                  const SizedBox(height: 10),
                ],
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
      final confirmar = sinc.necesitaConfirmar(op);
      final bloqueada = !op.conError && !confirmar && recursosConError.contains(op.recursoId);
      if (op.conError || confirmar) recursosConError.add(op.recursoId);
      filas.add(Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: _FilaPendiente(
          op: op,
          bloqueada: bloqueada,
          porConfirmar: confirmar,
          onDescartar: () => _descartar(context, sinc, op),
          onConfirmar: () => _confirmar(context, sinc, op),
        ),
      ));
    }
    return filas;
  }

  Future<void> _confirmar(BuildContext context, Sincronizador sinc, OperacionPendiente op) async {
    try {
      await sinc.volverAFirmar(op);
    } on ErrorSeguridad catch (e) {
      if (context.mounted && !e.cancelado) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje), backgroundColor: Marca.rojo));
      }
    }
  }

  /// Una confirmación por envío (cada firma exige su propia huella); se
  /// para en cuanto la persona cancela.
  Future<void> _confirmarTodas(BuildContext context, Sincronizador sinc, List<OperacionPendiente> ops) async {
    for (final op in ops) {
      try {
        await sinc.volverAFirmar(op);
      } on ErrorSeguridad catch (e) {
        if (context.mounted && !e.cancelado) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje), backgroundColor: Marca.rojo));
        }
        return;
      }
    }
  }

  Future<void> _descartar(BuildContext context, Sincronizador sinc, OperacionPendiente op) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Descartar envío'),
        content: Text('"${op.descripcion}" no se ha enviado. Si lo descartas, tendrás que volver a hacerlo. ¿Descartar?'),
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
  final bool porConfirmar;
  final VoidCallback onDescartar;
  final Future<void> Function() onConfirmar;
  const _FilaPendiente({
    required this.op,
    required this.bloqueada,
    required this.porConfirmar,
    required this.onDescartar,
    required this.onConfirmar,
  });

  @override
  Widget build(BuildContext context) {
    final (icono, color, estado) = porConfirmar
        ? (Icons.fingerprint, Marca.ambar, op.ultimoError ?? 'Falta confirmarla con tu huella o PIN.')
        : op.conError
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
        Text(estado, style: TextStyle(fontSize: 14, color: porConfirmar ? Marca.ambar : op.conError ? Marca.rojo : Marca.tintaSuave)),
        if (porConfirmar)
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            TextButton.icon(
              style: TextButton.styleFrom(foregroundColor: Marca.rojo),
              onPressed: onDescartar,
              icon: const Icon(Icons.delete_outline),
              label: const Text('Descartar'),
            ),
            const SizedBox(width: 6),
            BotonAccion(onPressed: onConfirmar, icono: Icons.fingerprint, texto: 'Confirmar con huella', textoOcupado: 'Confirmando…'),
          ])
        else if (op.conError) ...[
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
