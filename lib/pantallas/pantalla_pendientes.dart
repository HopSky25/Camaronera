import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/operacion_pendiente.dart';

/// Lo que todavía no se ha enviado al servidor, con el error si lo hubo.
class PantallaPendientes extends StatelessWidget {
  const PantallaPendientes({super.key});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    return Scaffold(
      appBar: AppBar(title: const Text('Pendientes de enviar')),
      body: FutureBuilder<List<OperacionPendiente>>(
        future: sinc.cola(),
        builder: (context, snap) {
          final ops = snap.data ?? [];
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (ops.isEmpty) {
            return const Center(child: Text('Todo está enviado.'));
          }
          return ListView.separated(
            itemCount: ops.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (_, i) {
              final op = ops[i];
              final conError = op.ultimoError != null;
              return ListTile(
                leading: Icon(conError ? Icons.error_outline : Icons.schedule,
                    color: conError ? Theme.of(context).colorScheme.error : null),
                title: Text('${op.descripcion}  ·  ${op.verificacionId.substring(0, 8)}'),
                subtitle: Text(
                  '${DateFormat('dd/MM HH:mm').format(op.creadaEn)}'
                  '${conError ? '\n${op.ultimoError} (intentos: ${op.intentos})' : ''}',
                ),
                isThreeLine: conError,
                trailing: conError
                    ? IconButton(
                        tooltip: 'Descartar este envío',
                        icon: const Icon(Icons.delete_outline),
                        onPressed: () async {
                          final ok = await showDialog<bool>(
                            context: context,
                            builder: (c) => AlertDialog(
                              title: const Text('Descartar envío'),
                              content: const Text('El servidor rechazó este dato. Si lo descartas, tendrás que '
                                  'volver a capturarlo. ¿Descartar?'),
                              actions: [
                                TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('No')),
                                FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Descartar')),
                              ],
                            ),
                          );
                          if (ok == true) await sinc.descartarPendiente(op.idLocal!);
                        },
                      )
                    : null,
              );
            },
          );
        },
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: sinc.sincronizando ? null : sinc.sincronizar,
        icon: const Icon(Icons.sync),
        label: const Text('Enviar ahora'),
      ),
    );
  }
}
