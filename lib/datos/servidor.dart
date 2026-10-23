/// Servidor al que entra la app si el usuario no elige otro en la pantalla de
/// entrada. Se fija al compilar:
///   flutter build apk --release --dart-define=CM_SERVIDOR=https://camaronmarket.com
/// Sin el parámetro (desarrollo, pruebas) apunta a la demo.
const servidorPorDefecto = String.fromEnvironment('CM_SERVIDOR', defaultValue: 'https://demo.camaronmarket.com');
