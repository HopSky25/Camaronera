/// Versión de la app y cabeceras que la acompañan en TODAS las peticiones
/// (docs/app/CONTRATO-MEJORAS-APP.md, C0).
library;

import 'dart:io' show Platform;

/// Versión semántica `x.y.z` de la app. Tiene que coincidir con `version:`
/// de pubspec.yaml (sin el `+build`); una prueba lo comprueba. Es una
/// constante (y no package_info_plus) para que la lean igual las pruebas y
/// la tarea en segundo plano.
const versionApp = '1.2.0';

/// `android` o `ios` (contrato C0). En pruebas (escritorio) vale `android`.
String get plataformaApp {
  try {
    if (Platform.isIOS) return 'ios';
  } catch (_) {
    // Web u otra plataforma sin dart:io: se trata como Android.
  }
  return 'android';
}

/// `X-App-Version` y `X-App-Platform`.
Map<String, String> cabecerasApp() => {'X-App-Version': versionApp, 'X-App-Platform': plataformaApp};
