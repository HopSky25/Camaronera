import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:workmanager/workmanager.dart';

import 'datos/api_cliente.dart';
import 'datos/avisos.dart';
import 'datos/base_local.dart';
import 'datos/control_version.dart';
import 'datos/notificaciones.dart';
import 'datos/reporte_errores.dart';
import 'datos/repositorio.dart';
import 'datos/sesion.dart';
import 'datos/sincronizador.dart';
import 'modelos/perfil.dart';
import 'navegacion/destinos.dart';
import 'navegacion/enrutador.dart';
import 'pantallas/empacadora/panel_empacadora.dart';
import 'pantallas/inicio/pantalla_actualizar.dart';
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

/// Reporte de errores de la app (C2). En depuración no envía nada.
final reporteErrores = ReporteErrores(habilitado: !kDebugMode);

/// Notificaciones locales de los avisos (C3) y su tarea periódica.
final notificaciones = NotificacionesLocales();

void main() {
  // Errores de zonas asíncronas que nadie atrapó; los de Flutter y los de la
  // plataforma se enganchan dentro (reporteErrores.instalar).
  runZonedGuarded(() {
    WidgetsFlutterBinding.ensureInitialized();
    reporteErrores.instalar();
    SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.dark,
    ));
    // Con señal de nuevo: se envían los reportes guardados.
    Connectivity().onConnectivityChanged.listen((estados) {
      if (!estados.contains(ConnectivityResult.none)) reporteErrores.enviar();
    });
    // Tarea periódica de avisos (WorkManager): ver revisarAvisosEnSegundoPlano.
    if (defaultTargetPlatform == TargetPlatform.android) {
      Workmanager().initialize(despachadorSegundoPlano).catchError((Object e) {
        debugPrint('No se pudo iniciar WorkManager: $e');
      });
    }
    runApp(const AppCamaronMarket());
  }, reporteErrores.capturar);
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
        ChangeNotifierProvider(create: (c) {
          final sesion = c.read<Sesion>();
          final control = ControlVersion(servidor: () => sesion.url);
          // Cualquier 426 de la API (también login y rutas públicas) bloquea.
          ApiCliente.alActualizacionObligatoria = control.alRecibir426;
          // Al abrir: en cuanto se sabe a qué servidor preguntar.
          sesion.listo.then((_) => control.comprobar());
          reporteErrores.servidor = () => sesion.url;
          sesion.listo.then((_) => reporteErrores.enviar());
          return control;
        }),
        ChangeNotifierProvider(
            create: (c) => ServicioAvisos(sesion: c.read<Sesion>(), notificador: notificaciones, atenderFondo: true)),
        Provider<Enrutador>(
          create: (c) {
            final sinc = c.read<Sincronizador>();
            final enrutador = Enrutador(sesion: c.read<Sesion>(), navegador: claveNavegador, alAbrirVerificacion: sinc.sincronizar);
            // Tocar un aviso (con la app abierta, en segundo plano o cerrada)
            // abre su destino tras desbloquear / entrar.
            notificaciones.iniciar(alTocar: (carga) => enrutador.ir(destinoDeCarga(carga))).then((_) async {
              final carga = await notificaciones.cargaDeLanzamiento();
              if (carga != null) enrutador.ir(destinoDeCarga(carga));
            });
            // Enlaces directos (C6): https de CamaronMarket y camaronmarket://.
            // El flujo trae también el enlace con el que se abrió la app. Una
            // ruta desconocida (destino nulo) lleva al inicio.
            try {
              AppLinks().uriLinkStream.listen((uri) => enrutador.ir(destinoDeEnlace(uri)),
                  onError: (Object e) => debugPrint('Enlace no válido: $e'));
            } catch (e) {
              debugPrint('Sin enlaces directos: $e');
            }
            return enrutador;
          },
          dispose: (_, e) => e.dispose(),
        ),
      ],
      child: _GuardiaBloqueo(
        child: MaterialApp(
          title: 'CamaronMarket',
          debugShowCheckedModeBanner: false,
          theme: temaCamaronMarket(),
          navigatorKey: claveNavegador,
          navigatorObservers: [ObservadorPantallas()],
          // La pantalla de desbloqueo tapa TODO (también las pantallas
          // abiertas encima de la raíz), sin cerrarlas: al desbloquear se
          // sigue donde se estaba (p. ej. un informe a medio llenar). En
          // modo supervisor, la franja "Viendo como…" queda fija arriba.
          builder: (context, child) =>
              _CapaVersion(child: _CapaBloqueo(child: CapaSupervisor(child: child ?? const SizedBox()))),
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
    final avisos = context.read<ServicioAvisos>();
    switch (state) {
      case AppLifecycleState.paused || AppLifecycleState.hidden:
        sesion.alSegundoPlano();
        avisos.enPrimerPlano = false;
      case AppLifecycleState.resumed:
        sesion.alVolver();
        avisos.enPrimerPlano = true;
        avisos.refrescar();
        // Versión mínima: como mucho una consulta por hora.
        context.read<ControlVersion>().comprobar();
        reporteErrores.enviar();
      default:
        break;
    }
  }

  /// Con la app bloqueada (o pidiendo actualizar), el botón atrás no mueve
  /// nada de lo de debajo.
  @override
  Future<bool> didPopRoute() async => context.read<Sesion>().bloqueada || context.read<ControlVersion>().obligatoria;

  @override
  Widget build(BuildContext context) => widget.child;
}

/// Versión mínima (C1): "Actualiza la app" tapa TODO (también el desbloqueo
/// y la entrada); una versión nueva no obligatoria se avisa abajo, discreta.
class _CapaVersion extends StatelessWidget {
  final Widget child;
  const _CapaVersion({required this.child});

  @override
  Widget build(BuildContext context) {
    final obligatoria = context.select<ControlVersion, bool>((c) => c.obligatoria);
    if (obligatoria) FocusManager.instance.primaryFocus?.unfocus();
    return Stack(children: [
      Offstage(offstage: obligatoria, child: TickerMode(enabled: !obligatoria, child: child)),
      if (obligatoria)
        Positioned.fill(
          child: Navigator(onGenerateRoute: (_) => MaterialPageRoute(builder: (_) => const PantallaActualizar())),
        )
      else
        const Positioned(left: 0, right: 0, bottom: 0, child: AvisoVersionNueva()),
    ]);
  }
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
  bool _permisoAvisos = false;

  /// Una vez por apertura, ya en un panel (cuenta propia): si Android 13+
  /// no permite notificaciones y nunca se preguntó, se explica para qué
  /// sirven y se pide el permiso POST_NOTIFICATIONS.
  Future<void> _quizasPedirPermisoAvisos() async {
    if (_permisoAvisos) return;
    _permisoAvisos = true;
    final bd = BaseLocal();
    try {
      if (await notificaciones.permitidas()) return;
      if (await bd.leerConfig('permiso_avisos_preguntado') != null) return;
    } catch (_) {
      return;
    }
    await Future<void>.delayed(const Duration(milliseconds: 600));
    if (!mounted) return;
    final quiere = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        icon: const Icon(Icons.notifications_active_outlined),
        title: const Text('Avisos de CamaronMarket'),
        content: const Text('Te avisamos aunque no tengas la app abierta: verificación asignada, informe listo para '
            'aceptar, plazo por vencer, despacho programado o cambiado, llegada del camión, compras y empaque.\n\n'
            'Android te pedirá permiso para mostrar notificaciones.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Ahora no')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Permitir avisos')),
        ],
      ),
    );
    await bd.escribirConfig('permiso_avisos_preguntado', '1');
    if (quiere == true) await notificaciones.pedirPermiso();
  }

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
    if (!sesion.soloLectura && !_permisoAvisos) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _quizasPedirPermisoAvisos());
    }
    if (perfil == perfilTecnico) return const PantallaBandeja(titulo: 'Mis verificaciones');
    if (perfil == 'verificador') return const PantallaBandeja(titulo: 'Bandeja de la verificadora');
    if (perfil == 'empacadora') return const PanelEmpacadora();
    if (perfil == 'maquilador') return const PanelMaquilador();
    // semillero, laboratorio y camaronera venden: mismo panel.
    return const PanelVendedor();
  }
}
