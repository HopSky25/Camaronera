import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../tema.dart';

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
  bool _verClave = false;
  String? _error;

  Future<void> _conectar() async {
    if (_clave.text.trim().isEmpty) {
      setState(() => _error = 'Pega la clave de API que te entregó tu empresa.');
      return;
    }
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
      body: Column(
        children: [
          // Cabecera con el degradado de la marca y el logo, como la portada del portal.
          Container(
            width: double.infinity,
            padding: const EdgeInsets.fromLTRB(24, 72, 24, 32),
            decoration: const BoxDecoration(gradient: Marca.degradado),
            child: Column(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
                  decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(14)),
                  child: Image.asset('assets/logo.png', height: 40),
                ),
                const SizedBox(height: 18),
                const Text('Verificador de campo',
                    style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w800)),
                const SizedBox(height: 6),
                Text('Inspecciona en la piscina o en la planta, con o sin señal.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 14)),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                const Rotulo('Conexión'),
                TextField(
                  controller: _url,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(labelText: 'Servidor', prefixIcon: Icon(Icons.language)),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: _clave,
                  obscureText: !_verClave,
                  decoration: InputDecoration(
                    labelText: 'Clave de API',
                    helperText: 'La crea tu empresa verificadora en el portal. Empieza por trz_',
                    prefixIcon: const Icon(Icons.key_outlined),
                    suffixIcon: IconButton(
                      icon: Icon(_verClave ? Icons.visibility_off_outlined : Icons.visibility_outlined),
                      onPressed: () => setState(() => _verClave = !_verClave),
                    ),
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 14),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(color: const Color(0xFFFDEAEA), borderRadius: BorderRadius.circular(12)),
                    child: Row(children: [
                      const Icon(Icons.error_outline, color: Marca.rojo),
                      const SizedBox(width: 10),
                      Expanded(child: Text(_error!, style: const TextStyle(color: Marca.rojo))),
                    ]),
                  ),
                ],
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: _ocupado ? null : _conectar,
                  icon: _ocupado
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.login),
                  label: Text(_ocupado ? 'Conectando…' : 'Conectar'),
                ),
                const SizedBox(height: 16),
                const Text(
                  'Solo necesitas internet para conectar y para sincronizar. '
                  'Lo que captures en campo se guarda en el teléfono.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Marca.tintaSuave, fontSize: 13),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
