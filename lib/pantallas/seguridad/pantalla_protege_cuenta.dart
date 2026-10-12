import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/sesion.dart';
import '../../seguridad/firmador.dart';
import '../../tema.dart';
import '../comunes/widgets.dart';
import 'comunes_seguridad.dart';

/// "Protege tu cuenta": obligatoria tras entrar (y para quien venía de la
/// versión anterior). Exige bloqueo de pantalla; con él, crea las claves del
/// teléfono, guarda la clave de API cifrada bajo la huella / PIN y registra
/// el teléfono en el servidor.
class PantallaProtegeCuenta extends StatefulWidget {
  const PantallaProtegeCuenta({super.key});
  @override
  State<PantallaProtegeCuenta> createState() => _PantallaProtegeCuentaState();
}

class _PantallaProtegeCuentaState extends State<PantallaProtegeCuenta> with WidgetsBindingObserver {
  EstadoProteccion? _estado;
  bool _comprobando = true;
  bool _ocupado = false;
  String? _error;

  /// 409 not-an-app-key: la clave se pegó a mano (o es de antes de la firma).
  bool _noEsDeApp = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _comprobar();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// Al volver de Ajustes se comprueba otra vez el bloqueo de pantalla.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && !_ocupado) _comprobar();
  }

  Future<void> _comprobar() async {
    setState(() => _comprobando = true);
    EstadoProteccion? estado;
    String? error;
    try {
      estado = await context.read<Sesion>().firmador.estado();
    } catch (e) {
      error = mensajeDeError(e);
    }
    if (!mounted) return;
    setState(() {
      _estado = estado;
      _comprobando = false;
      if (error != null) _error = error;
    });
  }

  Future<void> _proteger({bool conPin = false}) async {
    if (_ocupado) return;
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await context.read<Sesion>().proteger(conPin: conPin);
    } on ErrorSeguridad catch (e) {
      if (mounted && !e.cancelado) setState(() => _error = e.mensaje);
      if (e.codigo == 'sin_bloqueo') await _comprobar();
    } on ErrorApi catch (e) {
      if (mounted) {
        setState(() {
          _noEsDeApp = e.tipo == 'not-an-app-key';
          _error = _noEsDeApp ? null : e.mensaje;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final soloRegistro = sesion.protegida; // device-not-registered: la clave ya está cifrada
    final estado = _estado;
    return Scaffold(
      body: Column(children: [
        CabeceraSeguridad(
          icono: Icons.shield_outlined,
          titulo: soloRegistro ? 'Registra este teléfono' : 'Protege tu cuenta',
          texto: soloRegistro
              ? 'El servidor no tiene registrado este teléfono. Regístralo para poder confirmar operaciones.'
              : 'Un paso más antes de empezar: tu cuenta quedará ligada a este teléfono y a tu huella.',
        ),
        Expanded(
          child: _comprobando && estado == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(padding: const EdgeInsets.all(24), children: [
                  if (_noEsDeApp)
                    ..._claveNoDeApp(context)
                  else if (estado != null && !estado.bloqueoPantalla)
                    ..._sinBloqueo(context)
                  else
                    ..._conBloqueo(estado, soloRegistro),
                  if (_error != null) ...[const SizedBox(height: 14), CajaError(texto: _error!)],
                  const SizedBox(height: 20),
                  TextButton(
                    onPressed: _ocupado ? null : () => _salir(context),
                    child: const Text('Cerrar sesión'),
                  ),
                ]),
        ),
      ]),
    );
  }

  List<Widget> _claveNoDeApp(BuildContext context) => [
        const CajaError(
          texto: 'Esta sesión no se puede ligar a tu teléfono: se abrió con una clave de API creada a mano en el portal '
              'o con una versión anterior de la app.',
        ),
        const SizedBox(height: 16),
        const PuntoSeguridad(
            icono: Icons.login,
            texto: 'Entra con tu correo y contraseña de CamaronMarket: así la sesión queda protegida con tu huella. '
                'Lo que tengas guardado en el teléfono se conserva.'),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: () => context.read<Sesion>().entrarConCuenta(),
          icon: const Icon(Icons.login),
          label: const Text('Entrar con correo y contraseña'),
        ),
      ];

  List<Widget> _sinBloqueo(BuildContext context) => [
        const CajaError(texto: 'Tu teléfono no tiene bloqueo de pantalla. Sin él no se puede proteger la cuenta.'),
        const SizedBox(height: 16),
        const Rotulo('Cómo activarlo'),
        const PuntoSeguridad(icono: Icons.settings_outlined, texto: 'Abre Ajustes del teléfono y entra en Seguridad (o "Pantalla de bloqueo").'),
        const PuntoSeguridad(icono: Icons.pin_outlined, texto: 'Elige "Bloqueo de pantalla" y pon un PIN, patrón o contraseña.'),
        const PuntoSeguridad(icono: Icons.fingerprint, texto: 'Si tu teléfono tiene lector, registra también tu huella: es más rápido en campo.'),
        const PuntoSeguridad(icono: Icons.undo, texto: 'Vuelve a la app: lo comprobaremos solos.'),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: () async {
            try {
              await context.read<Sesion>().firmador.abrirAjustesSeguridad();
            } catch (_) {
              if (context.mounted) {
                ScaffoldMessenger.of(context)
                    .showSnackBar(const SnackBar(content: Text('Abre Ajustes > Seguridad desde el menú del teléfono.')));
              }
            }
          },
          icon: const Icon(Icons.open_in_new),
          label: const Text('Abrir Ajustes'),
        ),
        const SizedBox(height: 10),
        OutlinedButton.icon(
          onPressed: _comprobando ? null : _comprobar,
          icon: const Icon(Icons.refresh),
          label: const Text('Ya lo activé, comprobar'),
        ),
      ];

  List<Widget> _conBloqueo(EstadoProteccion? estado, bool soloRegistro) => [
        if (!soloRegistro) ...[
          const PuntoSeguridad(icono: Icons.lock_open_outlined, texto: 'Al abrir la app te pediremos tu huella (o el PIN del teléfono).'),
          const PuntoSeguridad(
              icono: Icons.draw_outlined,
              texto: 'Las operaciones importantes (compras, veredictos, informes, despachos, maquila) se confirman con tu huella, '
                  'también sin internet.'),
          const PuntoSeguridad(icono: Icons.verified_user_outlined, texto: textoHuellaNoSale),
          if (estado != null && !estado.hayHuella)
            Container(
              margin: const EdgeInsets.only(bottom: 12),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: const Color(0xFFFDF1E0), borderRadius: BorderRadius.circular(12)),
              child: const Text(
                'No hay huella registrada en este teléfono: se usará su PIN o patrón. Puedes registrar una huella en Ajustes cuando quieras.',
                style: TextStyle(color: Marca.ambar),
              ),
            ),
        ],
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: _ocupado ? null : () => _proteger(),
          icon: _ocupado
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.fingerprint),
          label: Text(_ocupado ? 'Protegiendo…' : (soloRegistro ? 'Registrar este teléfono' : 'Proteger con mi huella')),
        ),
        if (!soloRegistro) ...[
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _ocupado ? null : () => _proteger(conPin: true),
            icon: const Icon(Icons.pin_outlined),
            label: const Text('Usar PIN del teléfono'),
          ),
        ],
      ];

  Future<void> _salir(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cerrar sesión'),
        content: const Text('Sin proteger la cuenta no puedes usar la app en este teléfono. ¿Cerrar sesión?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cerrar sesión')),
        ],
      ),
    );
    if (ok == true && context.mounted) await context.read<Sesion>().cerrarSesion();
  }
}
