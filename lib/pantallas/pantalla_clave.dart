import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';

/// Primera pantalla: servidor y clave de API del técnico.
class PantallaClave extends StatefulWidget {
  const PantallaClave({super.key});
  @override
  State<PantallaClave> createState() => _PantallaClaveState();
}

class _PantallaClaveState extends State<PantallaClave> {
  final _url = TextEditingController(text: 'https://demo.camaronmarket.com');
  final _clave = TextEditingController();
  bool _ocupado = false;
  String? _error;

  Future<void> _conectar() async {
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final yo = await context.read<Sincronizador>().conectar(url: _url.text, clave: _clave.text);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Conectado como ${yo['name']}')));
    } catch (e) {
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const SizedBox(height: 32),
            Icon(Icons.verified_outlined, size: 72, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 12),
            Text('CamaronMarket', textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineMedium),
            Text('Verificador de campo', textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 32),
            TextField(
              controller: _url,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(labelText: 'Servidor', hintText: 'https://demo.camaronmarket.com'),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _clave,
              obscureText: true,
              decoration: const InputDecoration(
                labelText: 'Clave de API del técnico',
                helperText: 'Te la entrega tu empresa verificadora (empieza por trz_)',
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _ocupado ? null : _conectar,
              icon: _ocupado
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.login),
              label: const Text('Conectar'),
            ),
            const SizedBox(height: 12),
            const Text(
              'Solo necesitas internet para conectar y para sincronizar. '
              'El trabajo en campo se guarda en el teléfono.',
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}
