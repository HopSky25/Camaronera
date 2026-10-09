import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';

/// Veredicto del técnico: aprobar, aprobar con observaciones o rechazar.
class PantallaVeredicto extends StatefulWidget {
  final String id;
  const PantallaVeredicto({super.key, required this.id});
  @override
  State<PantallaVeredicto> createState() => _PantallaVeredictoState();
}

class _PantallaVeredictoState extends State<PantallaVeredicto> {
  String _veredicto = 'approve';
  final _notas = TextEditingController();
  String? _error;

  Future<void> _confirmar() async {
    if (_veredicto != 'approve' && _notas.text.trim().isEmpty) {
      setState(() => _error = 'Con observaciones o rechazo, las notas son obligatorias.');
      return;
    }
    final sinc = context.read<Sincronizador>();
    final v = sinc.bandeja.firstWhere((e) => e.id == widget.id);
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Confirmar veredicto'),
        content: Text(_veredicto == 'reject'
            ? 'Rechazar cancela la compra. Esta acción no se puede deshacer desde la app.'
            : 'Se cerrará la verificación con este veredicto.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Confirmar')),
        ],
      ),
    );
    if (ok != true) return;
    await sinc.darVeredicto(v, _veredicto, _notas.text);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Veredicto guardado; se enviará con internet')));
    Navigator.popUntil(context, (r) => r.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Veredicto')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          RadioGroup<String>(
            groupValue: _veredicto,
            onChanged: (v) => setState(() => _veredicto = v ?? 'approve'),
            child: const Column(children: [
              RadioListTile(value: 'approve', title: Text('Aprobar'), subtitle: Text('El lote cumple lo publicado')),
              RadioListTile(value: 'approve_obs', title: Text('Aprobar con observaciones'),
                  subtitle: Text('Cumple, pero hay diferencias que dejar por escrito')),
              RadioListTile(value: 'reject', title: Text('Rechazar'), subtitle: Text('No cumple; la compra se cancela')),
            ]),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _notas,
            maxLines: 4,
            decoration: InputDecoration(labelText: 'Notas', errorText: _error),
            onChanged: (_) => setState(() => _error = null),
          ),
          const SizedBox(height: 24),
          FilledButton.icon(onPressed: _confirmar, icon: const Icon(Icons.gavel), label: const Text('Confirmar veredicto')),
        ],
      ),
    );
  }
}
