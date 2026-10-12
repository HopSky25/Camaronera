import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../tema.dart';

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
  bool _ocupado = false;

  @override
  void dispose() {
    _notas.dispose();
    super.dispose();
  }

  Future<void> _confirmar() async {
    if (_ocupado) return;
    if (_veredicto != 'approve' && _notas.text.trim().isEmpty) {
      setState(() => _error = 'Con observaciones o rechazo, las notas son obligatorias.');
      return;
    }
    final sinc = context.read<Sincronizador>();
    final v = sinc.bandeja.where((e) => e.id == widget.id).firstOrNull;
    if (v == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Esta verificación ya no está en tu bandeja.')));
      return;
    }
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
    if (ok != true || !mounted) return;
    setState(() => _ocupado = true);
    try {
      final faltan = await sinc.darVeredicto(v, _veredicto, _notas.text);
      if (faltan.isNotEmpty) {
        if (mounted) {
          setState(() {
            _ocupado = false;
            _error = null;
          });
          await showDialog<void>(
            context: context,
            builder: (c) => AlertDialog(
              title: const Text('Falta completar el informe'),
              content: Text('El servidor no admite el veredicto sin estos datos:\n\n• ${faltan.join('\n• ')}\n\n'
                  'Complétalos en "Informe de campo" y vuelve a intentarlo.'),
              actions: [FilledButton(onPressed: () => Navigator.pop(c), child: const Text('Entendido'))],
            ),
          );
        }
        return;
      }
    } catch (_) {
      if (mounted) {
        setState(() => _ocupado = false);
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('No se pudo guardar el veredicto. Inténtalo otra vez.')));
      }
      return;
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Veredicto guardado; se enviará con internet')));
    Navigator.popUntil(context, (r) => r.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Veredicto')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
        children: [
          const Rotulo('Resultado de la inspección'),
          RadioGroup<String>(
            groupValue: _veredicto,
            onChanged: (v) {
              if (!_ocupado) setState(() => _veredicto = v ?? 'approve');
            },
            child: const Column(children: [
              RadioListTile(value: 'approve', title: Text('Aprobar'), subtitle: Text('El lote cumple lo publicado')),
              RadioListTile(value: 'approve_obs', title: Text('Aprobar con observaciones'),
                  subtitle: Text('Cumple, pero hay diferencias que dejar por escrito')),
              RadioListTile(value: 'reject', title: Text('Rechazar'), subtitle: Text('No cumple; la compra se cancela')),
            ]),
          ),
          const Rotulo('Notas'),
          TextField(
            controller: _notas,
            maxLines: 4,
            enabled: !_ocupado,
            decoration: InputDecoration(labelText: 'Observaciones para el informe', errorText: _error),
            onChanged: (_) => setState(() => _error = null),
          ),
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: _ocupado ? null : _confirmar,
            icon: _ocupado
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.gavel),
            label: Text(_ocupado ? 'Guardando…' : 'Confirmar veredicto'),
          ),
        ],
      ),
    );
  }
}
