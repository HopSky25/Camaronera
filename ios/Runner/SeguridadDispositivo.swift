// SeguridadDispositivo.swift — canal "com.camaronmarket/seguridad" para iPhone.
//
// ⚠️ NO COMPILADO NI PROBADO (no hay Mac en el equipo). Es la traducción de
// android/.../SeguridadDispositivo.kt; ver ios/README-SEGURIDAD.md. Lo marcado
// con TODO hay que comprobarlo en Xcode con un iPhone real antes de publicar.
//
// Mismo contrato que Android (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md):
//  - Firma: par P-256 en el Secure Enclave (no exportable). La pública sale
//    como SubjectPublicKeyInfo DER en base64; la firma, ECDSA SHA-256 en DER
//    (X9.62), en base64. Dos ranuras ("a" / "b") para "Registrar de nuevo
//    este teléfono".
//  - Cifrado de la clave de API: clave AES-256 aleatoria guardada en el
//    Keychain, protegida por biometría (o el código del iPhone) y solo en
//    este dispositivo. Formato base64(nonce(12) ‖ cifrado ‖ tag(16)), el
//    mismo que AES/GCM de Android.
//  - Cambiar las huellas / caras registradas invalida las claves (como
//    setInvalidatedByBiometricEnrollment en Android): se guarda el
//    evaluatedPolicyDomainState al crearlas y, si cambia, se borran y se
//    devuelve "clave_invalidada".
//
// Errores (FlutterError.code): cancelado, sin_bloqueo, sin_biometria,
// bloqueado_intentos, clave_invalidada, sin_claves, no_autenticado,
// datos_danados, ocupado, error.

import CryptoKit
import Flutter
import Foundation
import LocalAuthentication
import Security
import UIKit

final class SeguridadDispositivo: NSObject {
  static let canal = "com.camaronmarket/seguridad"

  private let etiquetaFirma = "com.camaronmarket.firma.es256."
  private let cuentaAes = "com.camaronmarket.cifrado.aes"
  private let servicioAes = "com.camaronmarket.seguridad"
  private let clavePrefsRanura = "camaronmarket_ranura_firma"
  private let clavePrefsDominio = "camaronmarket_dominio_biometria"

  private var ocupado = false

  /// Prefijo SubjectPublicKeyInfo de una clave EC P-256 sin comprimir:
  /// SEQUENCE { SEQUENCE { id-ecPublicKey, prime256v1 }, BIT STRING (65 bytes) }.
  private let prefijoSpkiP256: [UInt8] = [
    0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01,
    0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
  ]

  // MARK: - Ranuras

  private var ranura: String { UserDefaults.standard.string(forKey: clavePrefsRanura) ?? "a" }
  private var otraRanura: String { ranura == "a" ? "b" : "a" }
  private func etiqueta(_ r: String) -> Data { (etiquetaFirma + r).data(using: .utf8)! }

  // MARK: - Canal

  func registrar(con messenger: FlutterBinaryMessenger) {
    let canal = FlutterMethodChannel(name: SeguridadDispositivo.canal, binaryMessenger: messenger)
    canal.setMethodCallHandler { [weak self] llamada, resultado in
      self?.atender(llamada, resultado) ?? resultado(FlutterMethodNotImplemented)
    }
  }

  private func atender(_ llamada: FlutterMethodCall, _ resultado: @escaping FlutterResult) {
    let args = llamada.arguments as? [String: Any] ?? [:]
    do {
      switch llamada.method {
      case "estado":
        resultado(estado())
      case "prepararClaves":
        try verificarDominio()
        if try clavePrivada(ranura, contexto: nil, exigir: false) == nil { try generarParFirma(ranura) }
        if !existeAes() { try generarAes() }
        guardarDominio()
        resultado(try clavePublica(ranura))
      case "clavePublica":
        resultado(try? clavePublica(ranura))
      case "prepararClaveNueva":
        borrarPar(otraRanura)
        try generarParFirma(otraRanura)
        resultado(try clavePublica(otraRanura))
      case "activarClaveNueva":
        guard try clavePrivada(otraRanura, contexto: nil, exigir: false) != nil else { throw ErrorSeg.sinClaves }
        let vieja = ranura
        UserDefaults.standard.set(otraRanura, forKey: clavePrefsRanura)
        borrarPar(vieja)
        resultado(nil)
      case "descartarClaveNueva":
        borrarPar(otraRanura)
        resultado(nil)
      case "firmar":
        guard let datos = (args["datos"] as? FlutterStandardTypedData)?.data else { throw ErrorSeg.otro("Faltan los datos") }
        conAutenticacion(args, resultado) { contexto in
          try self.firmar(datos, contexto: contexto)
        }
      case "cifrar":
        guard let texto = args["texto"] as? String else { throw ErrorSeg.otro("Falta el texto") }
        conAutenticacion(args, resultado) { contexto in
          try self.cifrar(texto, contexto: contexto)
        }
      case "descifrar":
        guard let datos = args["datos"] as? String else { throw ErrorSeg.otro("Faltan los datos") }
        conAutenticacion(args, resultado) { contexto in
          try self.descifrar(datos, contexto: contexto)
        }
      case "borrarClaveFirma":
        borrarPar("a")
        borrarPar("b")
        resultado(nil)
      case "borrarClaves":
        borrarPar("a")
        borrarPar("b")
        borrarAes()
        UserDefaults.standard.removeObject(forKey: clavePrefsDominio)
        resultado(nil)
      case "abrirAjustesSeguridad":
        if let url = URL(string: UIApplication.openSettingsURLString) {
          UIApplication.shared.open(url)
        }
        resultado(nil)
      default:
        resultado(FlutterMethodNotImplemented)
      }
    } catch {
      let (codigo, mensaje) = traducir(error)
      resultado(FlutterError(code: codigo, message: mensaje, details: nil))
    }
  }

  // MARK: - Estado

  private func estado() -> [String: Any] {
    let contexto = LAContext()
    var error: NSError?
    let bloqueo = contexto.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error)
    var errorBio: NSError?
    let biometria: String
    if LAContext().canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &errorBio) {
      biometria = "disponible"
    } else {
      switch (errorBio as? LAError)?.code {
      case .some(.biometryNotEnrolled): biometria = "sin_inscribir"
      case .some(.biometryNotAvailable): biometria = "sin_hardware"
      default: biometria = "no_disponible"
      }
    }
    var info = utsname()
    uname(&info)
    let modelo = withUnsafePointer(to: &info.machine) {
      $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
    }
    return [
      "bloqueo": bloqueo,
      "biometria": biometria,
      "sdk": 0,
      "fabricante": "Apple",
      "modelo": modelo,
      "tieneClaveFirma": ((try? clavePrivada(ranura, contexto: nil, exigir: false)) ?? nil) != nil,
      "tieneClaveCifrado": existeAes(),
    ]
  }

  // MARK: - Control de acceso

  /// Huella / cara ACTUALES o, si no, el código del iPhone (como
  /// BIOMETRIC_STRONG | DEVICE_CREDENTIAL en Android).
  private func controlDeAcceso(firma: Bool) throws -> SecAccessControl {
    var flags: SecAccessControlCreateFlags = [.biometryCurrentSet, .or, .devicePasscode]
    if firma { flags.insert(.privateKeyUsage) }
    var error: Unmanaged<CFError>?
    guard let control = SecAccessControlCreateWithFlags(
      kCFAllocatorDefault, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, flags, &error)
    else {
      throw error?.takeRetainedValue() ?? ErrorSeg.otro("No se pudo crear el control de acceso")
    }
    return control
  }

  /// Con `.or .devicePasscode` iOS NO invalida la clave al cambiar la
  /// biometría (el código sigue valiendo). Para igualar a Android se compara
  /// el estado del dominio biométrico con el guardado al crear las claves.
  /// TODO(iPhone): comprobar que evaluatedPolicyDomainState solo cambia al
  /// añadir / quitar huellas o caras (y no tras actualizar iOS).
  private func verificarDominio() throws {
    guard let guardado = UserDefaults.standard.data(forKey: clavePrefsDominio) else { return }
    let contexto = LAContext()
    var error: NSError?
    _ = contexto.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
    if let actual = contexto.evaluatedPolicyDomainState, actual != guardado {
      borrarPar("a")
      borrarPar("b")
      borrarAes()
      UserDefaults.standard.removeObject(forKey: clavePrefsDominio)
      throw ErrorSeg.claveInvalidada
    }
  }

  private func guardarDominio() {
    let contexto = LAContext()
    var error: NSError?
    _ = contexto.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
    if let dominio = contexto.evaluatedPolicyDomainState {
      UserDefaults.standard.set(dominio, forKey: clavePrefsDominio)
    }
  }

  // MARK: - Par de firma (Secure Enclave)

  private func generarParFirma(_ r: String) throws {
    let atributos: [String: Any] = [
      kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
      kSecAttrKeySizeInBits as String: 256,
      kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
      kSecPrivateKeyAttrs as String: [
        kSecAttrIsPermanent as String: true,
        kSecAttrApplicationTag as String: etiqueta(r),
        kSecAttrAccessControl as String: try controlDeAcceso(firma: true),
      ] as [String: Any],
    ]
    var error: Unmanaged<CFError>?
    guard SecKeyCreateRandomKey(atributos as CFDictionary, &error) != nil else {
      throw error?.takeRetainedValue() ?? ErrorSeg.otro("No se pudo crear la clave de firma")
    }
  }

  /// La clave privada de la ranura. Con [contexto] ya evaluado no vuelve a
  /// pedir la huella. Nula (sin lanzar) si no existe y [exigir] es false.
  private func clavePrivada(_ r: String, contexto: LAContext?, exigir: Bool = true) throws -> SecKey? {
    var consulta: [String: Any] = [
      kSecClass as String: kSecClassKey,
      kSecAttrApplicationTag as String: etiqueta(r),
      kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
      kSecReturnRef as String: true,
    ]
    if let contexto = contexto {
      consulta[kSecUseAuthenticationContext as String] = contexto
    }
    var item: CFTypeRef?
    let estado = SecItemCopyMatching(consulta as CFDictionary, &item)
    if estado == errSecItemNotFound {
      if exigir { throw ErrorSeg.sinClaves }
      return nil
    }
    guard estado == errSecSuccess, let clave = item else { throw ErrorSeg.keychain(estado) }
    return (clave as! SecKey)  // swiftlint:disable:this force_cast
  }

  /// SubjectPublicKeyInfo DER (base64) de la ranura.
  private func clavePublica(_ r: String) throws -> String {
    guard let privada = try clavePrivada(r, contexto: nil),
          let publica = SecKeyCopyPublicKey(privada)
    else { throw ErrorSeg.sinClaves }
    var error: Unmanaged<CFError>?
    // Representación X9.63 sin comprimir: 04 ‖ X ‖ Y (65 bytes).
    guard let punto = SecKeyCopyExternalRepresentation(publica, &error) as Data?, punto.count == 65 else {
      throw error?.takeRetainedValue() ?? ErrorSeg.otro("Clave pública inesperada")
    }
    return (Data(prefijoSpkiP256) + punto).base64EncodedString()
  }

  private func borrarPar(_ r: String) {
    let consulta: [String: Any] = [
      kSecClass as String: kSecClassKey,
      kSecAttrApplicationTag as String: etiqueta(r),
    ]
    SecItemDelete(consulta as CFDictionary)
  }

  private func firmar(_ datos: Data, contexto: LAContext) throws -> String {
    try verificarDominio()
    guard let privada = try clavePrivada(ranura, contexto: contexto) else { throw ErrorSeg.sinClaves }
    var error: Unmanaged<CFError>?
    // ecdsaSignatureMessageX962SHA256: hace el SHA-256 y devuelve la firma DER.
    guard let firma = SecKeyCreateSignature(privada, .ecdsaSignatureMessageX962SHA256, datos as CFData, &error) as Data? else {
      throw error?.takeRetainedValue() ?? ErrorSeg.noAutenticado
    }
    return firma.base64EncodedString()
  }

  // MARK: - Clave AES (Keychain, protegida por biometría)

  private func existeAes() -> Bool {
    let consulta: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: servicioAes,
      kSecAttrAccount as String: cuentaAes,
      // Solo se pregunta si existe: sin pedir la huella.
      kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail,
    ]
    let estado = SecItemCopyMatching(consulta as CFDictionary, nil)
    // errSecInteractionNotAllowed = existe pero pide autenticación.
    return estado == errSecSuccess || estado == errSecInteractionNotAllowed
  }

  private func generarAes() throws {
    let clave = SymmetricKey(size: .bits256)
    let datos = clave.withUnsafeBytes { Data($0) }
    let item: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: servicioAes,
      kSecAttrAccount as String: cuentaAes,
      kSecValueData as String: datos,
      kSecAttrAccessControl as String: try controlDeAcceso(firma: false),
    ]
    let estado = SecItemAdd(item as CFDictionary, nil)
    guard estado == errSecSuccess else { throw ErrorSeg.keychain(estado) }
  }

  private func leerAes(_ contexto: LAContext) throws -> SymmetricKey {
    let consulta: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: servicioAes,
      kSecAttrAccount as String: cuentaAes,
      kSecReturnData as String: true,
      kSecUseAuthenticationContext as String: contexto,
    ]
    var item: CFTypeRef?
    let estado = SecItemCopyMatching(consulta as CFDictionary, &item)
    if estado == errSecItemNotFound { throw ErrorSeg.sinClaves }
    guard estado == errSecSuccess, let datos = item as? Data else { throw ErrorSeg.keychain(estado) }
    return SymmetricKey(data: datos)
  }

  private func borrarAes() {
    let consulta: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: servicioAes,
      kSecAttrAccount as String: cuentaAes,
    ]
    SecItemDelete(consulta as CFDictionary)
  }

  private func cifrar(_ texto: String, contexto: LAContext) throws -> String {
    try verificarDominio()
    let clave = try leerAes(contexto)
    let caja = try AES.GCM.seal(Data(texto.utf8), using: clave)
    guard let combinado = caja.combined else { throw ErrorSeg.otro("No se pudo cifrar") }
    return combinado.base64EncodedString()  // nonce(12) ‖ cifrado ‖ tag(16)
  }

  private func descifrar(_ base64: String, contexto: LAContext) throws -> String {
    try verificarDominio()
    guard let datos = Data(base64Encoded: base64), datos.count > 28 else { throw ErrorSeg.datosDanados }
    let clave = try leerAes(contexto)
    do {
      let caja = try AES.GCM.SealedBox(combined: datos)
      let claro = try AES.GCM.open(caja, using: clave)
      guard let texto = String(data: claro, encoding: .utf8) else { throw ErrorSeg.datosDanados }
      return texto
    } catch is CryptoKitError {
      throw ErrorSeg.datosDanados
    }
  }

  // MARK: - Autenticación

  /// Pide Face ID / Touch ID (o el código con conPin) UNA vez y hace la
  /// operación con ese contexto ya evaluado (el Keychain no vuelve a pedir).
  /// TODO(iPhone): confirmar que el contexto evaluado sirve para la clave del
  /// Secure Enclave con .privateKeyUsage sin un segundo diálogo.
  private func conAutenticacion(
    _ args: [String: Any], _ resultado: @escaping FlutterResult, _ operacion: @escaping (LAContext) throws -> String
  ) {
    if ocupado {
      resultado(FlutterError(code: "ocupado", message: "Ya hay una confirmación en curso.", details: nil))
      return
    }
    ocupado = true
    let titulo = args["titulo"] as? String ?? "Confirma que eres tú"
    let subtitulo = args["subtitulo"] as? String
    let conPin = args["conPin"] as? Bool ?? false
    let contexto = LAContext()
    contexto.localizedCancelTitle = "Cancelar"
    contexto.localizedFallbackTitle = "Usar el código del iPhone"
    // Biometría con el código del iPhone como alternativa. TODO(iPhone): iOS
    // no permite forzar SOLO el código; con conPin se usa la misma política.
    _ = conPin
    let politica: LAPolicy = .deviceOwnerAuthentication
    let razon = [titulo, subtitulo].compactMap { $0 }.joined(separator: ". ")
    contexto.evaluatePolicy(politica, localizedReason: razon) { ok, error in
      DispatchQueue.main.async {
        self.ocupado = false
        if !ok {
          let (codigo, mensaje) = self.traducir(error ?? ErrorSeg.noAutenticado)
          resultado(FlutterError(code: codigo, message: mensaje, details: nil))
          return
        }
        do {
          resultado(try operacion(contexto))
        } catch {
          let (codigo, mensaje) = self.traducir(error)
          resultado(FlutterError(code: codigo, message: mensaje, details: nil))
        }
      }
    }
  }

  // MARK: - Errores

  private enum ErrorSeg: Error {
    case sinClaves, claveInvalidada, noAutenticado, datosDanados
    case keychain(OSStatus)
    case otro(String)
  }

  private func traducir(_ error: Error) -> (String, String) {
    if let e = error as? ErrorSeg {
      switch e {
      case .sinClaves: return ("sin_claves", "No hay claves en este teléfono")
      case .claveInvalidada: return ("clave_invalidada", "Cambió el código o la biometría del teléfono.")
      case .noAutenticado: return ("no_autenticado", "No se confirmó a tiempo. Vuelve a intentarlo.")
      case .datosDanados: return ("datos_danados", "Los datos guardados no se pueden descifrar.")
      case .keychain(let estado):
        if estado == errSecUserCanceled { return ("cancelado", "No se confirmó.") }
        if estado == errSecAuthFailed { return ("no_autenticado", "No se confirmó.") }
        // TODO(iPhone): revisar qué OSStatus devuelve una clave invalidada.
        return ("error", "Keychain: \(estado)")
      case .otro(let m): return ("error", m)
      }
    }
    if let e = error as? LAError {
      switch e.code {
      case .userCancel, .appCancel, .systemCancel, .userFallback: return ("cancelado", "No se confirmó.")
      case .passcodeNotSet: return ("sin_bloqueo", "El iPhone no tiene código.")
      case .biometryLockout: return ("bloqueado_intentos", "Demasiados intentos.")
      case .biometryNotAvailable, .biometryNotEnrolled: return ("sin_biometria", "Sin Face ID / Touch ID.")
      case .authenticationFailed: return ("no_autenticado", "No se reconoció.")
      default: return ("error", e.localizedDescription)
      }
    }
    return ("error", error.localizedDescription)
  }
}
