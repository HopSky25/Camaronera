import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/api_cliente.dart';
import '../datos/sesion.dart';
import '../tema.dart';
import 'comunes/widgets.dart';

/// Opción avanzada: entrar con una clave de API creada en el portal
/// (por ejemplo la que la empresa verificadora entrega a cada técnico).
class PantallaClave extends StatefulWidget {
  final String url;
  const PantallaClave({super.key, this.url = 'https://demo.camaronmarket.com'});
  @override
  State<PantallaClave> createState() => _PantallaClaveState();
}

class _PantallaClaveState extends State<PantallaClave> {
  late final _url = TextEditingController(text: widget.url);
  final _clave = TextEditingController();
  bool _ocupado = false;
  bool _verClave = false;
  String? _error;

  @override
  void dispose() {
    _url.dispose();
    _clave.dispose();
    super.dispose();
  }

  Future<void> _conectar() async {
    if (_ocupado) return;
    if (_clave.text.trim().isEmpty) {
      setState(() => _error = 'Pega la clave de API que te entregó tu empresa.');
      return;
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await context.read<Sesion>().entrarConClave(url: _url.text, clave: _clave.text);
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Entrar con clave de API')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const Text(
            'Para integraciones y para técnicos con clave propia. Si tienes correo y contraseña, '
            'vuelve atrás y entra con tu cuenta.',
            style: TextStyle(color: Marca.tintaSuave, fontSize: 13.5),
          ),
          const Rotulo('Conexión'),
          TextField(
            controller: _url,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(labelText: 'Servidor', prefixIcon: Icon(Icons.language)),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: _clave,
            obscureText: !_verClave,
            autocorrect: false,
            decoration: InputDecoration(
              labelText: 'Clave de API',
              helperText: 'Se crea en el portal, en Mis claves de API. Empieza por trz_',
              prefixIcon: const Icon(Icons.key_outlined),
              suffixIcon: IconButton(
                icon: Icon(_verClave ? Icons.visibility_off_outlined : Icons.visibility_outlined),
                onPressed: () => setState(() => _verClave = !_verClave),
              ),
            ),
          ),
          if (_error != null) ...[const SizedBox(height: 14), CajaError(texto: _error!)],
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: _ocupado ? null : _conectar,
            icon: _ocupado
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.login),
            label: Text(_ocupado ? 'Conectando…' : 'Conectar'),
          ),
        ],
      ),
    );
  }
}
