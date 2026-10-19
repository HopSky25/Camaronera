import 'dart:io' show Platform;

import 'package:flutter/widgets.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:workmanager/workmanager.dart';

import '../modelos/aviso.dart';
import '../navegacion/destinos.dart';
import 'avisos.dart';

/// Canal de Android de los avisos (se ve en Ajustes > Notificaciones).
const canalAvisos = 'avisos_camaronmarket';
const nombreCanalAvisos = 'Avisos de CamaronMarket';

/// Tarea periódica de WorkManager.
const tareaAvisos = 'camaronmarket.revisar_avisos';

/// Id fijo del aviso genérico (se reemplaza a sí mismo).
const _idGenerico = 1;

/// Notificaciones locales de Android (sin Firebase) y la tarea periódica.
class NotificacionesLocales implements Notificador {
  final _plugin = FlutterLocalNotificationsPlugin();
  bool _iniciado = false;
  Future<void>? _iniciando;

  // Quién atiende un aviso tocado (el enrutador). Se guarda aparte porque
  // mostrar() puede iniciar el plugin antes de que exista el enrutador; si
  // el callback fuera solo el de la primera llamada, el toque se perdería.
  void Function(String? carga)? _alTocar;
  // Un aviso tocado antes de que haya quien lo atienda: se entrega al
  // registrarse el callback.
  String? _cargaSinAtender;
  bool _hayCargaSinAtender = false;

  static const _detalles = NotificationDetails(
    android: AndroidNotificationDetails(
      canalAvisos,
      nombreCanalAvisos,
      channelDescription: 'Verificaciones asignadas, informes por aceptar, despachos, compras y empaque.',
      importance: Importance.high,
      priority: Priority.high,
      icon: 'ic_notificacion',
      color: Color(0xFF0E8EA0),
      // En la pantalla de bloqueo solo "CamaronMarket": sin el texto.
      visibility: NotificationVisibility.private,
    ),
    iOS: DarwinNotificationDetails(),
  );

  /// [alTocar] recibe la carga del aviso tocado (ver [destinoDeCarga]).
  Future<void> iniciar({void Function(String? carga)? alTocar}) {
    if (alTocar != null) {
      _alTocar = alTocar;
      if (_hayCargaSinAtender) {
        final carga = _cargaSinAtender;
        _hayCargaSinAtender = false;
        _cargaSinAtender = null;
        alTocar(carga);
      }
    }
    if (_iniciado) return Future.value();
    return _iniciando ??= _inicializar();
  }

  void _tocado(String? carga) {
    final atender = _alTocar;
    if (atender != null) {
      atender(carga);
    } else {
      _cargaSinAtender = carga;
      _hayCargaSinAtender = true;
    }
  }

  Future<void> _inicializar() async {
    try {
      await _plugin.initialize(
        settings: const InitializationSettings(
          android: AndroidInitializationSettings('ic_notificacion'),
          // El permiso en iOS se pide aparte (pedirPermiso).
          iOS: DarwinInitializationSettings(
              requestAlertPermission: false, requestBadgePermission: false, requestSoundPermission: false),
        ),
        onDidReceiveNotificationResponse: (r) => _tocado(r.payload),
      );
      _iniciado = true;
    } catch (e) {
      debugPrint('No se pudieron preparar las notificaciones: $e');
    } finally {
      _iniciando = null;
    }
  }

  /// Si la app se abrió tocando un aviso: su carga.
  Future<String?> cargaDeLanzamiento() async {
    try {
      final d = await _plugin.getNotificationAppLaunchDetails();
      if (d?.didNotificationLaunchApp ?? false) return d!.notificationResponse?.payload ?? '';
    } catch (_) {}
    return null;
  }

  AndroidFlutterLocalNotificationsPlugin? get _android =>
      _plugin.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>();

  /// ¿Ya puede mostrar notificaciones? (Android 13+ necesita el permiso).
  Future<bool> permitidas() async {
    try {
      if (Platform.isAndroid) return await _android?.areNotificationsEnabled() ?? false;
    } catch (_) {}
    return false;
  }

  /// Pide POST_NOTIFICATIONS (Android 13+) o el permiso de iOS.
  Future<bool> pedirPermiso() async {
    try {
      if (Platform.isAndroid) return await _android?.requestNotificationsPermission() ?? false;
      if (Platform.isIOS) {
        return await _plugin
                .resolvePlatformSpecificImplementation<IOSFlutterLocalNotificationsPlugin>()
                ?.requestPermissions(alert: true, badge: true, sound: true) ??
            false;
      }
    } catch (_) {}
    return false;
  }

  @override
  Future<void> mostrar(Aviso a) async {
    await iniciar();
    await _plugin.show(
      id: a.id.hashCode & 0x7fffffff,
      title: a.titulo,
      body: a.cuerpo.isEmpty ? null : a.cuerpo,
      notificationDetails: _detalles,
      payload: cargaDeAviso(a),
    );
  }

  @override
  Future<void> mostrarGenerico() async {
    await iniciar();
    await _plugin.show(
      id: _idGenerico,
      title: 'CamaronMarket',
      body: 'Tienes avisos nuevos en CamaronMarket',
      notificationDetails: _detalles,
      payload: 'avisos',
    );
  }

  @override
  Future<void> programarRevision() async {
    if (!Platform.isAndroid) return; // iOS: BGTaskScheduler, sin configurar todavía
    await Workmanager().registerPeriodicTask(
      tareaAvisos,
      tareaAvisos,
      frequency: const Duration(minutes: 15),
      constraints: Constraints(networkType: NetworkType.connected),
      existingWorkPolicy: ExistingPeriodicWorkPolicy.keep,
    );
  }

  @override
  Future<void> cancelarRevision() async {
    if (!Platform.isAndroid) return;
    await Workmanager().cancelByUniqueName(tareaAvisos);
  }
}

/// Punto de entrada de WorkManager (isolate en segundo plano).
@pragma('vm:entry-point')
void despachadorSegundoPlano() {
  Workmanager().executeTask((tarea, datos) async {
    try {
      WidgetsFlutterBinding.ensureInitialized();
      return await revisarAvisosEnSegundoPlano(almacen: AlmacenEstadoAvisos(), notificador: NotificacionesLocales());
    } catch (e) {
      debugPrint('Fallo la revisión de avisos en segundo plano: $e');
      return true; // no reintentar en bucle: la próxima pasada llega en 15 min
    }
  });
}
