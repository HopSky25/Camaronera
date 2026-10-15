import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  /// Canal propio de seguridad (ver SeguridadDispositivo.swift: NO compilado
  /// ni probado todavía).
  private let seguridad = SeguridadDispositivo()

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    // TODO(iPhone): confirmar en Xcode que registrar(forPlugin:) devuelve el
    // registrar del motor implícito y su messenger.
    if let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "SeguridadDispositivo") {
      seguridad.registrar(con: registrar.messenger())
    }
  }
}
