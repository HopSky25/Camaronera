# iPhone: estado de la preparación

> **NO COMPILADO NI PROBADO.** Todo lo de `ios/` se escribió sin Mac ni iPhone.
> Antes de publicar en el App Store hay que abrirlo en Xcode, compilarlo y
> probarlo en un iPhone real. Lo dudoso está marcado con `TODO(iPhone)` en el código.

## Qué hay

| Pieza | Archivo | Estado |
|---|---|---|
| Bundle id `com.camaronmarket.app` | `Runner.xcodeproj/project.pbxproj` | hecho (el `applicationId` de Android sigue siendo `com.camaronmarket.verificador_campo`) |
| Nombre visible "CamaronMarket" | `Runner/Info.plist` | hecho |
| Textos de permisos en español: `NSFaceIDUsageDescription`, `NSCameraUsageDescription`, `NSLocationWhenInUseUsageDescription`, `NSPhotoLibraryUsageDescription` | `Runner/Info.plist` | hecho |
| Esquema `camaronmarket://` (C6) y `FlutterDeepLinkingEnabled = false` (los enlaces los atiende `app_links`) | `Runner/Info.plist` | hecho |
| Canal nativo `com.camaronmarket/seguridad` (equivalente a `SeguridadDispositivo.kt`) | `Runner/SeguridadDispositivo.swift`, registrado en `Runner/AppDelegate.swift` y añadido al target Runner | **sin compilar** |

## SeguridadDispositivo.swift (mismo contrato que Android)

* **Firma**: par P-256 en el **Secure Enclave** (`kSecAttrTokenIDSecureEnclave`, no exportable),
  control de acceso `[.privateKeyUsage, .biometryCurrentSet, .or, .devicePasscode]`, dos ranuras
  (`a`/`b`) para "Registrar de nuevo este teléfono".
  * Clave pública: `SecKeyCopyExternalRepresentation` (X9.63, 65 bytes) con el prefijo DER de
    SubjectPublicKeyInfo P-256 → 91 bytes, igual que `PublicKey.getEncoded()` de Android.
  * Firma: `SecKeyCreateSignature(.ecdsaSignatureMessageX962SHA256)` → DER (ASN.1), igual que
    `SHA256withECDSA`.
* **Cifrado de la clave de API**: clave AES-256 aleatoria guardada en el **Keychain**
  (`kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly` + biometría o código), AES-GCM con CryptoKit;
  formato `base64(nonce ‖ cifrado ‖ tag)`, el mismo que Android.
* **Invalidación al cambiar la biometría**: iOS con `.or .devicePasscode` no invalida la clave al añadir
  una cara / huella. Para igualar a Android (`setInvalidatedByBiometricEnrollment`) se guarda
  `evaluatedPolicyDomainState` al crear las claves y, si cambia, se borran y se devuelve
  `clave_invalidada` (la app pide volver a entrar).
* **Autenticación**: un `LAContext` con `.deviceOwnerAuthentication` (Face ID / Touch ID con el código
  como alternativa) se evalúa una vez y se pasa al Keychain con `kSecUseAuthenticationContext`.
  iOS no permite forzar "solo código", así que `conPin` usa la misma política.
* Códigos de error devueltos a Dart: los mismos que Android (`cancelado`, `sin_bloqueo`,
  `bloqueado_intentos`, `clave_invalidada`, `sin_claves`, `no_autenticado`, `datos_danados`, `ocupado`, `error`).
* El lado Dart no cambia: `FirmadorAndroid` habla con el canal por nombre, sirve igual en iOS
  (`registrarDispositivo` manda `platform: "ios"` por `X-App-Platform`).

## TODO antes de publicar en iPhone

1. Compilar en Xcode; revisar los `TODO(iPhone)` (registrar del motor implícito, contexto evaluado
   reutilizado por el Secure Enclave sin segundo diálogo, OSStatus de una clave invalidada,
   estabilidad de `evaluatedPolicyDomainState`).
2. Probar en un iPhone real: registro del dispositivo contra `shrimp_api` (que el servidor acepte la
   SPKI y verifique la firma DER), desbloqueo, firmar con Face ID y con el código.
3. **Universal Links (C6)**: falta el entitlement `com.apple.developer.associated-domains`
   (`applinks:camaronmarket.com`, `applinks:demo.camaronmarket.com`, …) y que el servidor publique
   `/.well-known/apple-app-site-association` con el Team ID. Sin eso solo funciona `camaronmarket://`.
4. **Avisos en segundo plano (C3)**: `workmanager` en iOS usa BGTaskScheduler y necesita
   `BGTaskSchedulerPermittedIdentifiers` y el modo de fondo "fetch" en Info.plist; la app ya no
   programa la tarea fuera de Android. Las notificaciones locales piden permiso con
   `requestPermissions` (ya está en `NotificacionesLocales.pedirPermiso`).
5. Íconos de iOS: los generó `flutter_launcher_icons` (sin transparencia), revisar en el simulador.
