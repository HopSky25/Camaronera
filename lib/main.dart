import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'datos/repositorio.dart';
import 'datos/sesion.dart';
import 'datos/sincronizador.dart';
import 'modelos/perfil.dart';
import 'pantallas/empacadora/panel_empacadora.dart';
import 'pantallas/inicio/pantalla_elegir_perfil.dart';
import 'pantallas/inicio/pantalla_inicio_sesion.dart';
import 'pantallas/maquilador/panel_maquilador.dart';
import 'pantallas/pantalla_bandeja.dart';
import 'pantallas/seguridad/pantalla_desbloqueo.dart';
import 'pantallas/seguridad/pantalla_protege_cuenta.dart';
import 'pantallas/supervisor/franja_supervisor.dart';
import 'pantallas/supervisor/pantalla_directorio_socios.dart';
import 'pantallas/vendedor/panel_vendedor.dart';
import 'tema.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
  ));
  runApp(const AppCamaronMarket());
}

/// La app de CamaronMarket para todos los perfiles: una sola app, un panel
/// por perfil. El técnico de campo conserva su flujo sin internet.
class AppCamaronMarket extends StatelessWidget {
  const AppCamaronMarket({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => Sesion()..iniciar()),
        ChangeNotifierProvider(create: (c) => Sincronizador(sesion: c.read<Sesion>())..iniciar()),
        Provider(create: (c) => Repositorio(sesion: c.read<Sesion>())),
      ],
      child: _GuardiaBloqueo(
        child: MaterialApp(
          title: 'CamaronMarket',
          debugShowCheckedModeBanner: false,
          theme: temaCamaronMarket(),
          navigatorKey: claveNavegador,
          // La pantalla de desbloqueo tapa TODO (también las pantallas
          // abiertas encima de la raíz), sin cerrarlas: al desbloquear se
          // sigue donde se estaba (p. ej. un informe a medio llenar). En
          // modo supervisor, la franja "Viendo como…" queda fija arriba.
          builder: (context, child) => _CapaBloqueo(child: CapaSupervisor(child: child ?? const SizedBox())),
          home: const _Raiz(),
        ),
      ),
    );
  }
}

/// Avisa a la sesión cuando la app pasa a segundo plano y cuando vuelve
/// (más de 2 minutos fuera → bloqueada).
class _GuardiaBloqueo extends StatefulWidget {
  final Widget child;
  const _GuardiaBloqueo({required this.child});
  @override
  State<_GuardiaBloqueo> createState() => _GuardiaBloqueoState();
}

class _GuardiaBloqueoState extends State<_GuardiaBloqueo> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final sesion = context.read<Sesion>();
    switch (state) {
      case AppLifecycleState.paused || AppLifecycleState.hidden:
        sesion.alSegundoPlano();
      case AppLifecycleState.resumed:
        sesion.alVolver();
      default:
        break;
    }
  }

  /// Con la app bloqueada, el botón atrás no mueve nada de lo de debajo.
  @override
  Future<bool> didPopRoute() async => context.read<Sesion>().bloqueada;

  @override
  Widget build(BuildContext context) => widget.child;
}

class _CapaBloqueo extends StatelessWidget {
  final Widget child;
  const _CapaBloqueo({required this.child});

  @override
  Widget build(BuildContext context) {
    final bloqueada = context.select<Sesion, bool>((s) => s.bloqueada);
    if (bloqueada) FocusManager.instance.primaryFocus?.unfocus();
    return Stack(children: [
      // Debajo no se pinta nada mientras está bloqueada (Offstage), pero
      // las pantallas abiertas conservan su estado.
      Offstage(offstage: bloqueada, child: TickerMode(enabled: !bloqueada, child: child)),
      if (bloqueada)
        // Navigator propio: los diálogos de la pantalla de desbloqueo.
        Positioned.fill(
          child: Navigator(onGenerateRoute: (_) => MaterialPageRoute(builder: (_) => const PantallaDesbloqueo())),
        ),
    ]);
  }
}

class _Raiz extends StatefulWidget {
  const _Raiz();
  @override
  State<_Raiz> createState() => _RaizState();
}

class _RaizState extends State<_Raiz> {
  String? _clave;

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    // Si la sesión caduca o se cambia de perfil estando en una pantalla
    // interior, se vuelve a la raíz para que no quede nada encima.
    final clave = '${sesion.conectado}/${sesion.necesitaProteger}/${sesion.comoSocio?.socio.id}/${sesion.perfil}';
    if (_clave != null && _clave != clave) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
      });
    }
    _clave = clave;
    if (sesion.cargando) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    // Bloqueada al abrir la app: la capa de desbloqueo tapa esto.
    if (sesion.bloqueada && !sesion.conectado) return const Scaffold();
    if (!sesion.conectado) return const PantallaInicioSesion();
    if (sesion.necesitaProteger) return const PantallaProtegeCuenta();
    // Modo supervisor sin socio elegido (o tras caducar su acceso).
    if (sesion.esSupervisor && sesion.comoSocio == null) return const PantallaDirectorioSocios();
    if (sesion.necesitaElegirPerfil) return const PantallaElegirPerfil();
    final perfil = sesion.perfil!;
    if (perfil == perfilTecnico) return const PantallaBandeja(titulo: 'Mis verificaciones');
    if (perfil == 'verificador') return const PantallaBandeja(titulo: 'Bandeja de la verificadora');
    if (perfil == 'empacadora') return const PanelEmpacadora();
    if (perfil == 'maquilador') return const PanelMaquilador();
    // semillero, laboratorio y camaronera venden: mismo panel.
    return const PanelVendedor();
  }
}
