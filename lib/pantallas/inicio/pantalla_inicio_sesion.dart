import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/sesion.dart';
import '../../tema.dart';
import '../comunes/widgets.dart';
import '../pantalla_clave.dart';

/// Primera pantalla: servidor, correo y contraseña de la cuenta de CamaronMarket.
class PantallaInicioSesion extends StatefulWidget {
  const PantallaInicioSesion({super.key});
  @override
  State<PantallaInicioSesion> createState() => _PantallaInicioSesionState();
}

class _PantallaInicioSesionState extends State<PantallaInicioSesion> {
  late final _url = TextEditingController(text: context.read<Sesion>().url);
  final _correo = TextEditingController();
  final _contrasena = TextEditingController();
  bool _ocupado = false;
  bool _verContrasena = false;
  bool _verServidor = false;
  String? _error;

  @override
  void dispose() {
    _url.dispose();
    _correo.dispose();
    _contrasena.dispose();
    super.dispose();
  }

  Future<void> _entrar() async {
    if (_ocupado) return; // el teclado puede repetir "Entrar" mientras se procesa
    if (_correo.text.trim().isEmpty || _contrasena.text.isEmpty) {
      setState(() => _error = 'Escribe tu correo y tu contraseña.');
      return;
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await context.read<Sesion>().iniciarSesion(url: _url.text, correo: _correo.text, contrasena: _contrasena.text);
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final aviso = context.watch<Sesion>().avisoCaducidad;
    return Scaffold(
      body: Column(
        children: [
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
                const Text('Entra con tu cuenta', style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w800)),
                const SizedBox(height: 6),
                Text('La misma cuenta del portal de CamaronMarket.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 14)),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(24),
              children: [
                if (aviso != null) ...[
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(color: const Color(0xFFFDF1E0), borderRadius: BorderRadius.circular(12)),
                    child: Row(children: [
                      const Icon(Icons.info_outline, color: Marca.ambar),
                      const SizedBox(width: 10),
                      Expanded(child: Text(aviso, style: const TextStyle(color: Marca.ambar))),
                    ]),
                  ),
                  const SizedBox(height: 8),
                ],
                const Rotulo('Cuenta'),
                TextField(
                  controller: _correo,
                  keyboardType: TextInputType.emailAddress,
                  autocorrect: false,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(labelText: 'Correo', prefixIcon: Icon(Icons.mail_outline)),
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: _contrasena,
                  obscureText: !_verContrasena,
                  textInputAction: TextInputAction.done,
                  onSubmitted: (_) => _entrar(),
                  decoration: InputDecoration(
                    labelText: 'Contraseña',
                    prefixIcon: const Icon(Icons.lock_outline),
                    suffixIcon: IconButton(
                      icon: Icon(_verContrasena ? Icons.visibility_off_outlined : Icons.visibility_outlined),
                      onPressed: () => setState(() => _verContrasena = !_verContrasena),
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: () => setState(() => _verServidor = !_verServidor),
                    icon: Icon(_verServidor ? Icons.expand_less : Icons.expand_more, size: 18),
                    label: Text(_verServidor ? 'Ocultar servidor' : 'Servidor: ${_url.text.replaceFirst('https://', '')}'),
                  ),
                ),
                if (_verServidor)
                  TextField(
                    controller: _url,
                    keyboardType: TextInputType.url,
                    autocorrect: false,
                    onChanged: (_) => setState(() {}),
                    decoration: const InputDecoration(labelText: 'Servidor', prefixIcon: Icon(Icons.language)),
                  ),
                if (_error != null) ...[
                  const SizedBox(height: 14),
                  CajaError(texto: _error!),
                ],
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: _ocupado ? null : _entrar,
                  icon: _ocupado
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.login),
                  label: Text(_ocupado ? 'Entrando…' : 'Entrar'),
                ),
                const SizedBox(height: 12),
                TextButton(
                  onPressed: _ocupado
                      ? null
                      : () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaClave(url: _url.text))),
                  child: const Text('Entrar con clave de API (opción avanzada)'),
                ),
                const SizedBox(height: 8),
                const Text(
                  'Solo necesitas internet para entrar y para sincronizar. '
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
